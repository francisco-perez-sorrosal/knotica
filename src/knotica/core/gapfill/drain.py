"""One drain: select open gaps, discover, heal the queue, write once.

Reads the P1 gap queue, keeps only ``genuine_gap`` records still ``open``,
formulates one deterministic query per gap, runs an injected ``DiscoveryService``,
and stages one ``pending`` :class:`~knotica.core.records.SuggestionRecord` per
(gap, ranked candidate) -- deduped on ``(gap_id, source_key)`` so a persistent
regression never spams the queue, and against the vault's own stored sources by
URL identity (``core.source_inventory``) so discovery never re-proposes what an
earlier ingest already holds -- writing once per drain in its own
:class:`VaultTransaction`.

The healing pass (:func:`_heal_queue`) lives here rather than beside the review
lifecycle because it is drain-time work: it runs on every drain, inside the same
transaction and against the same records the staging pass dedups on.

**Ordering is load-bearing** and is the shape of
:func:`refresh_suggestions_for_gaps` itself: every network call happens first, on
gap records alone; only then is the vault state read and written, under one span
lock the write's transaction reuses reentrantly.

Exception discipline mirrors ``core.gap_classifier``: the drain never catches a
failure from the injected service -- a raise propagates uncaught, because failure
isolation is the loop hook's single ``try/except`` boundary, not this module's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from knotica.core.gapfill.gap_review import _is_cascade_rejection
from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill.queue_io import (
    _PROPOSE_OP,
    _candidate_url_key,
    _is_protected,
    _open_genuine_gaps,
    _published_source_id8s,
    _read_gaps,
    _read_suggestions,
    _serialize,
    _source_key,
    _suggestion_id,
    _utc_now_iso,
    suggestions_path,
)
from knotica.core.lock import vault_span_lock
from knotica.core.records import GapRecord, SuggestionRecord
from knotica.core.source_inventory import stored_source_url_keys
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

if TYPE_CHECKING:
    from knotica.discovery.records import SearchQuery, SourceCandidate
    from knotica.discovery.service import DiscoveryService

#: Candidate cap per formulated query -- the deterministic v1 formulation asks for
#: the ``SearchQuery`` default breadth; a wider cap is a ``proposer_version`` bump.
DEFAULT_MAX_RESULTS = 10

#: Statuses the queue-healing pass may close; ``ingested``/``rejected`` are
#: terminal and a healed record must never overwrite a decision already made.
_HEALABLE: frozenset[str] = frozenset({"pending", "approved", "deferred"})
#: Precedence when several editions of one source survive per gap: a human
#: decision outranks the undecided, then the better-ranked candidate wins.
_HEAL_STATUS_RANK: Mapping[str, int] = {"approved": 2, "deferred": 1, "pending": 0}


@dataclass(frozen=True)
class RefreshResult:
    """The outcome of one drain, for the CLI / loop hook to summarize.

    ``service_available`` is ``False`` only when the drain was called with no
    configured discovery service (a clean no-op). ``gaps_drained`` counts the
    open ``genuine_gap`` records a discovery query was issued for; a gap whose
    candidates were all already suggested still counts as drained but contributes
    zero to ``suggestions_written`` (dedup). ``candidates_already_in_vault``
    counts the candidates dropped because their URL identity matches a source
    the vault already stores -- surfaced rather than swallowed, so a drain that
    stages little says why.
    """

    service_available: bool
    gaps_considered: int
    gaps_drained: int
    suggestions_written: int
    candidates_already_in_vault: int = 0
    #: Pre-existing open queue records the drain closed: sources the vault
    #: already stores, plus per-gap duplicates the canonical identity now
    #: collapses (see :func:`_heal_queue`). Counted so a queue that shrank
    #: explains itself.
    stale_suggestions_closed: int = 0
    #: Gaps whose *entire* candidate yield was dropped as already-in-vault.
    #: ``candidates_already_in_vault`` is a topic-level total and cannot say
    #: which gap is inert; such a gap can never be resolved (only a merged
    #: source or a human dismissal closes one) yet costs a billed search on
    #: every drain, so it is named rather than folded into the count. The same
    #: observation is persisted on each named gap as ``answered_in_vault_at``,
    #: so an operator who did not run this drain still sees it on Home.
    gaps_fully_in_vault: tuple[str, ...] = ()


def formulate_query(gap: GapRecord) -> SearchQuery:
    """Deterministically map one gap to a search request (no LLM, no wall clock).

    The failed golden question *is* the information need, so ``text`` is the gap's
    question verbatim; ``category="paper"`` biases providers toward scholarly
    sources. Reference-page-name augmentation is a documented ``proposer_version``
    bump, not v1. ``discovery.records.SearchQuery`` is imported lazily so this
    module stays off the MCP cold-start import path.
    """
    from knotica.discovery.records import SearchQuery

    return SearchQuery(text=gap.question, category="paper", max_results=DEFAULT_MAX_RESULTS)


def build_suggestion_records(
    gap: GapRecord,
    candidates: Sequence[SourceCandidate],
    *,
    proposer_version: int = 1,
    clock: Callable[[], str] | None = None,
) -> list[SuggestionRecord]:
    """Join one gap to its ranked candidates as ``pending`` suggestion records (pure).

    ``gap``'s display fields (``qa_id``/``fault_class``/``question``/
    ``reference_pages``/``detected_generation``/``origin``) are copied verbatim so a card
    renders with zero cross-file join; each candidate is embedded as its opaque
    ``to_record()`` dict. ``rank`` is 1-based in the given order (the service owns
    ordering). ``proposer_version`` identifies the formulation logic; the v1 record
    does not persist it (it becomes a stored field only at a future schema bump),
    but the parameter is part of the builder contract. ``clock`` yields the ISO-8601
    UTC ``proposed_at`` stamp, injectable for deterministic tests.
    """
    stamp = clock or _utc_now_iso
    proposed_at = stamp()
    records: list[SuggestionRecord] = []
    for rank, candidate in enumerate(candidates, start=1):
        candidate_record = candidate.to_record()
        records.append(
            SuggestionRecord(
                suggestion_id=_suggestion_id(gap.topic, gap.gap_id, _source_key(candidate_record)),
                topic=gap.topic,
                gap_id=gap.gap_id,
                qa_id=gap.qa_id,
                fault_class=gap.fault_class,
                question=gap.question,
                reference_pages=gap.reference_pages,
                rank=rank,
                query_text=gap.question,
                candidate=candidate_record,
                status="pending",
                proposed_at=proposed_at,
                decided_at=None,
                decided_reason=None,
                ingested_at=None,
                detected_generation=gap.detected_generation,
                gap_origin=gap.origin,
            )
        )
    return records


def refresh_suggestions_for_gaps(
    store: VaultStore,
    root: str | Path,
    topic: str,
    *,
    service: DiscoveryService | None,
    max_gaps: int | None = None,
    clock: Callable[[], str] | None = None,
) -> RefreshResult:
    """Drain open ``genuine_gap`` records into staged ``pending`` suggestions.

    Reads ``gaps.jsonl``, keeps only ``fault_class == genuine_gap AND status ==
    open``, optionally caps to the ``max_gaps`` highest-``|quality_delta|`` gaps,
    formulates one query per surviving gap, runs ``service.discover``, drops
    candidates whose URL identity the vault already stores as an ingested source
    (``core.source_inventory`` -- counted on the result, never silent), dedups
    the rest against every existing suggestion on ``(gap_id, source_key)`` --
    every one **but** a record a gap dismissal closed
    (:func:`_is_cascade_rejection`), so a reopened gap can be re-sourced -- and
    writes the survivors once in an own ``VaultTransaction``. A ``None`` service
    (no key configured) or zero survivors stages nothing -- but the queue-healing
    pass still runs, because healing is local work that needs neither a provider
    nor an open gap (a topic whose last gap just resolved is precisely where
    stale ``approved`` records pile up). Nothing to write is still a clean no-op:
    no transaction, no commit. A failure raised by ``service.discover`` is **not**
    caught (failure isolation is the loop hook's boundary).

    **Ordering is load-bearing.** Every network call happens first, on gap
    records alone; only then is the vault state read and written, under the
    :func:`~knotica.core.lock.vault_span_lock` the write's own transaction
    reuses reentrantly. Reading the queue *before* a discovery run that takes
    seconds-to-minutes and then rewriting the whole file from that snapshot
    silently reverted every decision an operator took meanwhile.
    """
    open_gaps = _open_genuine_gaps(store, topic)
    selected = _select_gaps(open_gaps, max_gaps) if service is not None else []
    # Network first, outside the lock: the results are pure data, so nothing
    # read here can be invalidated by a concurrent decision.
    discovered = [
        (gap, build_suggestion_records(gap, service.discover(formulate_query(gap)), clock=clock))
        for gap in selected
        if service is not None
    ]
    with vault_span_lock(Path(root)):
        staged = _stage_discovered(store, root, topic, discovered, stamp=clock or _utc_now_iso)
    return replace(
        staged,
        service_available=service is not None,
        gaps_considered=len(open_gaps),
        gaps_drained=len(selected),
    )


def _stage_discovered(
    store: VaultStore,
    root: str | Path,
    topic: str,
    discovered: Sequence[tuple[GapRecord, Sequence[SuggestionRecord]]],
    *,
    stamp: Callable[[], str],
) -> RefreshResult:
    """Heal the queue, stage the new candidates, restamp the gaps, write once (lock held).

    Every vault read happens here so it is bracketed by the caller's span lock
    together with the write -- see :func:`refresh_suggestions_for_gaps`. The
    ``gaps_considered``/``gaps_drained``/``service_available`` slots are the
    caller's to fill; this returns the queue-derived half of the result.
    """
    vault_urls = stored_source_url_keys(store, topic)
    protected = _published_source_id8s(root, topic)
    healed, healed_count = _heal_queue(
        _read_suggestions(store, topic), vault_urls, stamp=stamp, protected=protected
    )
    # A cascade-rejected record is the gap's dismissal speaking, not a human's
    # judgement of the source, so it must not dedup a re-drain of the reopened
    # gap -- that is the contract ``_plan_dismiss_cascade`` documents.
    seen = {
        (record.gap_id, _source_key(record.candidate))
        for record in healed
        if not _is_cascade_rejection(record)
    }
    known_ids = {record.suggestion_id for record in healed}
    in_vault = 0
    inert_gaps: list[str] = []
    staged_gaps: set[str] = set()
    # A re-staged record keeps its deterministic id, so it must REPLACE the
    # cascade-rejected line rather than append a second one under that id.
    revived: dict[str, SuggestionRecord] = {}
    new_records: list[SuggestionRecord] = []
    for gap, built in discovered:
        gap_in_vault = 0
        for record in built:
            if _candidate_url_key(record.candidate) in vault_urls:
                gap_in_vault += 1
                continue
            key = (record.gap_id, _source_key(record.candidate))
            if key in seen:
                continue
            seen.add(key)
            staged_gaps.add(gap.gap_id)
            if record.suggestion_id in known_ids:
                revived[record.suggestion_id] = record
            else:
                new_records.append(record)
        in_vault += gap_in_vault
        if built and gap_in_vault == len(built):
            inert_gaps.append(gap.gap_id)

    written = len(new_records) + len(revived)
    records = [revived.get(record.suggestion_id, record) for record in healed] + new_records
    gaps_body = _restamped_gaps_body(store, topic, inert_gaps, staged_gaps, stamp=stamp)
    if written or healed_count or gaps_body is not None:
        _write_queues(
            store,
            root,
            topic,
            records,
            staged=written,
            closed=healed_count,
            gaps_body=gaps_body,
        )
    return RefreshResult(
        service_available=True,
        gaps_considered=0,
        gaps_drained=0,
        suggestions_written=written,
        candidates_already_in_vault=in_vault,
        stale_suggestions_closed=healed_count,
        gaps_fully_in_vault=tuple(inert_gaps),
    )


def _select_gaps(gaps: Sequence[GapRecord], max_gaps: int | None) -> list[GapRecord]:
    """The gaps a drain issues a query for: all, or up to ``max_gaps`` selected.

    Gaps with real evidence (``evidence.quality_delta != 0.0``) rank by
    descending ``|quality_delta|``, tie-broken by ascending ``gap_id``.
    Zero-evidence gaps -- ``reported``/``retracted`` origins score a
    constant-zero delta by construction, never a real measurement -- rank by
    ``detected_at`` recency (most recent first) instead, also tie-broken by
    ascending ``gap_id``. At least one zero-evidence gap is reserved a slot
    under the cap (when one is open), so a deliberate human report or a
    guillotine retraction is never starved indefinitely behind an unbroken run
    of measured regressions.
    """
    if max_gaps is None or max_gaps >= len(gaps):
        return list(gaps)

    scored = [gap for gap in gaps if gap.evidence.quality_delta != 0.0]
    zero_evidence = [gap for gap in gaps if gap.evidence.quality_delta == 0.0]

    scored_ranked = sorted(scored, key=lambda gap: (-abs(gap.evidence.quality_delta), gap.gap_id))
    zero_ranked = sorted(
        sorted(zero_evidence, key=lambda gap: gap.gap_id),
        key=lambda gap: gap.detected_at,
        reverse=True,
    )

    reserved = zero_ranked[:1]
    remaining_cap = max_gaps - len(reserved)
    fill = scored_ranked[:remaining_cap]
    if len(fill) < remaining_cap:
        shortfall = remaining_cap - len(fill)
        fill = fill + zero_ranked[len(reserved) : len(reserved) + shortfall]

    return fill + reserved


def _heal_queue(
    records: Sequence[SuggestionRecord],
    vault_urls: frozenset[str],
    *,
    stamp: Callable[[], str],
    protected: frozenset[str] = frozenset(),
) -> tuple[list[SuggestionRecord], int]:
    """Close stale queue records the identity rule now sees through (pure).

    Two sweeps over the still-open (pending/approved/deferred) records, run on
    every drain so the queue converges without a manual decision per record --
    a field report held *fourteen* approved editions of one already-ingested
    SEP entry, one ``withdraw`` at a time being the only exit:

    * a record whose canonical URL the vault already stores closes as
      ``rejected`` (``source already stored in the vault``);
    * within one gap, records sharing a canonical source identity collapse to
      a single winner (human decision first, then best rank, then newest);
      the losers close as ``rejected`` naming the winner, and the winner's own
      stored URL is rewritten to its canonical form -- otherwise the survivor
      of a pre-canonicalization queue can be the broken-case edition while the
      correct living entry is the one closed as its duplicate.

    ``protected`` holds the ``id8`` branch infixes of suggestions with a live
    source-candidate branch: an ``approved`` record already published as
    ``loop/c/...`` is untouchable here, because the gate merges its branch and
    *then* stamps the record, so un-approving it behind the gate's back is how
    a merged-but-unstamped source happens.

    Returns the full record list (order preserved) and how many were closed.
    """
    now = stamp()
    healed: dict[str, SuggestionRecord] = {}
    rewritten: dict[str, SuggestionRecord] = {}
    open_records = [
        record
        for record in records
        if record.status in _HEALABLE and not _is_protected(record, protected)
    ]
    for record in open_records:
        if _candidate_url_key(record.candidate) in vault_urls:
            healed[record.suggestion_id] = replace(
                record,
                status="rejected",
                decided_at=now,
                decided_reason="source already stored in the vault",
            )

    groups: dict[tuple[str, str], list[SuggestionRecord]] = {}
    for record in open_records:
        if record.suggestion_id in healed:
            continue
        groups.setdefault((record.gap_id, _source_key(record.candidate)), []).append(record)
    for group in groups.values():
        if len(group) < 2:
            continue
        winner = max(
            group,
            key=lambda r: (_HEAL_STATUS_RANK[r.status], -r.rank, r.proposed_at),
        )
        canonical = _canonicalized(winner)
        if canonical is not None:
            rewritten[winner.suggestion_id] = canonical
        for loser in group:
            if loser.suggestion_id == winner.suggestion_id:
                continue
            healed[loser.suggestion_id] = replace(
                loser,
                status="rejected",
                decided_at=now,
                decided_reason=f"duplicate of {winner.suggestion_id} (same source)",
            )

    updated = {**rewritten, **healed}
    return [updated.get(record.suggestion_id, record) for record in records], len(healed)


def _canonicalized(record: SuggestionRecord) -> SuggestionRecord | None:
    """``record`` with its candidate URL rewritten canonically, or ``None`` if unchanged."""
    from knotica.discovery.normalize import canonicalize_url

    url = record.candidate.get("url")
    if not isinstance(url, str):
        return None
    canonical = canonicalize_url(url)
    if canonical == url:
        return None
    return replace(record, candidate={**record.candidate, "url": canonical})


def _restamped_gaps_body(
    store: VaultStore,
    topic: str,
    inert_gaps: Sequence[str],
    staged_gaps: frozenset[str] | set[str],
    *,
    stamp: Callable[[], str],
) -> str | None:
    """The whole ``gaps.jsonl`` body with this drain's ``answered_in_vault_at`` edits.

    Two edits, both this drain's own observation about a gap it just queried:
    a gap whose *entire* non-empty candidate yield the vault already stores is
    stamped ``now``; a gap this drain staged at least one suggestion for has its
    stamp cleared, because a stageable candidate is proof the vault does not
    already answer it. Every other record is passed through byte-for-byte.

    Persisting the observation is the whole point (td-070): the drain result
    already names the inert gaps, but only to whoever ran the drain -- Home
    reads the *record*, so the signal costs it no discovery work (dec-092).
    Terminal gap statuses need no clearing pass: every reader filters to
    ``open``.

    Returns ``None`` when nothing changed, which is what keeps a drain that
    stages nothing and heals nothing a zero-commit no-op.
    """
    if not inert_gaps and not staged_gaps:
        return None
    inert = frozenset(inert_gaps)
    now = stamp()
    gaps = _read_gaps(store, topic)
    updated = [
        replace(gap, answered_in_vault_at=now)
        if gap.gap_id in inert
        else (replace(gap, answered_in_vault_at=None) if gap.gap_id in staged_gaps else gap)
        for gap in gaps
    ]
    if updated == gaps:
        return None
    return "\n".join(gap.to_json_line() for gap in updated) + "\n"


def _write_queues(
    store: VaultStore,
    root: str | Path,
    topic: str,
    records: Sequence[SuggestionRecord],
    *,
    staged: int,
    closed: int,
    gaps_body: str | None,
) -> None:
    """Rewrite ``suggestions.jsonl`` (and, when restamped, ``gaps.jsonl``) in one commit.

    Both files are declared to the *same* transaction: one drain is one
    operation, however many queue files its observation touches -- the shape
    ``review.apply_gate_outcome`` already uses for the gate stamp that closes
    its gap.
    """
    title = f"refresh suggestions for {topic} ({staged} staged, {closed} closed)"
    if gaps_body is not None:
        title += ", gap stamps updated"
    with VaultTransaction(store, Path(root), _PROPOSE_OP, topic, title) as txn:
        txn.write(suggestions_path(topic), _serialize(records))
        if gaps_body is not None:
            txn.write(gaps_path(topic), gaps_body)
