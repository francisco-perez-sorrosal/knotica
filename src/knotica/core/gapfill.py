"""The gap-fill drain + decide leaf -- join gaps to discovered sources, gate them.

Two responsibilities over one committed queue (``<topic>/.knotica/suggestions/
suggestions.jsonl``), plus the gap queue's own terminal transitions (below):

* **Drain** (:func:`refresh_suggestions_for_gaps`) reads the P1 gap queue, keeps
  only ``genuine_gap`` records still ``open``, formulates one deterministic query
  per gap, runs an injected ``DiscoveryService``, and stages one ``pending``
  :class:`~knotica.core.records.SuggestionRecord` per (gap, ranked candidate) --
  deduped on ``(gap_id, source_key)`` so a persistent regression never spams the
  queue, and against the vault's own stored sources by URL identity
  (``core.source_inventory``) so discovery never re-proposes what an earlier
  ingest already holds -- writing once per drain in its own
  :class:`VaultTransaction`.
* **Decide** (:func:`apply_decision`) mediates the human approve / reject / defer /
  mark-ingested transition over the D2 lifecycle state machine, requiring a
  non-empty reason on reject, rewriting exactly one record in its own transaction.

The gap queue (``<topic>/.knotica/gaps/gaps.jsonl``) is where a drain *starts*, so
this module also owns where one *ends*: :func:`apply_gate_outcome` closes the
originating gap ``open -> resolved`` when a source candidate merges (in the gate
stamp's own transaction, one commit for both), and :func:`apply_gap_decision` gives
a human the ``dismiss`` / ``reopen`` transitions. Filing gaps stays with
``core.gap_classifier`` and :func:`report_gap`.

This module and ``core.source_inventory`` are the only P3 code touching
``discovery/`` -- and both do so **lazily, inside the function that needs it**,
never at module top level (the inventory reaches only the ``normalize`` identity
leaf). That keeps the
module importable on the MCP cold-start path (an MCP tool delegates to
:func:`apply_decision`, which imports no ``discovery`` at all) without dragging the
heavy search chain onto that path. The config->service factory
(:func:`build_default_discovery_service`) is the bridge P2 left unbuilt; it returns
``None`` (never raises) when no key is configured so the drain degrades to a no-op.

Exception discipline mirrors ``core.gap_classifier``: the drain never catches a
failure from the injected service -- a raise propagates uncaught, because failure
isolation is the loop hook's single ``try/except`` boundary, not this module's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from knotica.core.branch_namespaces import (
    CANDIDATE_BRANCH_PREFIX,
    WIP_BRANCH_PREFIX,
    _ID_INFIX_LENGTH,
    _SOURCE_INFIX,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gap_classifier import FaultClass, gaps_path, write_gap_records
from knotica.core.lock import vault_span_lock
from knotica.core.records import (
    GAP_ORIGIN_REPORTED,
    GAP_ORIGIN_RETRACTED,
    GapEvidence,
    GapRecord,
    SuggestionRecord,
    parse_gaps_jsonl,
    parse_suggestions_jsonl,
)
from knotica.core.source_inventory import stored_source_url_keys
from knotica.core.topics import require_topic
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

if TYPE_CHECKING:
    from knotica.discovery.config import SearchConfig
    from knotica.discovery.provider import SearchProvider
    from knotica.discovery.records import SearchQuery, SourceCandidate
    from knotica.discovery.service import DiscoveryService

__all__ = [
    "GATE_VERDICT_MERGED",
    "GATE_VERDICT_REFUSED",
    "DecisionResult",
    "GapDecisionResult",
    "RefreshResult",
    "ReportedGapResult",
    "apply_decision",
    "apply_gap_decision",
    "apply_gate_outcome",
    "build_default_discovery_service",
    "build_suggestion_records",
    "file_retracted_gap",
    "formulate_query",
    "plan_decision",
    "refresh_suggestions_for_gaps",
    "report_gap",
    "require_gate_mergeable",
    "suggestions_path",
]

#: Directory name under each topic that owns loop/eval artifacts.
_KNOTICA_DIR = ".knotica"
#: Subdir + basename of the per-topic human-approval suggestion queue.
_SUGGESTIONS_DIRNAME = "suggestions"
_SUGGESTIONS_FILENAME = "suggestions.jsonl"
#: Op slot of the drain's suggestion-propose commit (own transaction, one commit).
_PROPOSE_OP = "suggestion_propose"
#: Op slot of the approve/reject/defer/mark-ingested commit. Also the slot of the
#: machine gate stamp, whose merged verdict closes the originating gap in the
#: same commit -- one operation, one commit, however many files it touches.
_REVIEW_OP = "suggestion_review"
#: Op slot of the human dismiss/reopen gap commit (own transaction, one commit).
_GAP_REVIEW_OP = "gap_review"

#: Candidate cap per formulated query -- the deterministic v1 formulation asks for
#: the ``SearchQuery`` default breadth; a wider cap is a ``proposer_version`` bump.
DEFAULT_MAX_RESULTS = 10

#: Sentinel eval-provenance fields on a reported gap: it was not produced by the
#: regression classifier (``origin="reported"`` carries that signal) and belongs
#: to no eval generation, so both read as 0 rather than a fabricated version/gen.
_NO_CLASSIFIER = 0
_NO_GENERATION = 0
#: Per-origin prefix for a synthetic ``qa_id`` derived from proposer text (no
#: golden record backs it) -- mirrors ``evals.golden``'s ``golden-<hash>`` scheme.
#: The prefix keeps a reported gap and a retracted gap distinct even when their
#: source text is byte-identical (different provenance must not dedup together).
_ORIGIN_QA_ID_PREFIX: Mapping[str, str] = {
    GAP_ORIGIN_REPORTED: "reported-",
    GAP_ORIGIN_RETRACTED: "retracted-",
}

#: The D2 lifecycle state machine: which source statuses each decision may act on.
_ALLOWED_FROM: Mapping[str, frozenset[str]] = {
    "approve": frozenset({"pending", "deferred"}),
    "reject": frozenset({"pending", "deferred"}),
    "defer": frozenset({"pending"}),
    "mark_ingested": frozenset({"approved"}),
    # ``approved`` had exactly one exit, and it asserted an ingest had happened.
    # An operator who approved a suggestion and then decided against ingesting
    # it -- or who is releasing a refused one -- had to claim ``mark_ingested``
    # to move it at all, writing a false record of an ingest that never
    # occurred. ``withdraw`` returns it to the queue instead, asserting nothing.
    "withdraw": frozenset({"approved"}),
}
#: The terminal status each decision moves a record to.
_TARGET_STATUS: Mapping[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "defer": "deferred",
    "mark_ingested": "ingested",
    "withdraw": "pending",
}
#: Decisions that carry a decided_reason (required for reject, optional for defer,
#: optional for withdraw -- "why did this leave the approved queue" is worth
#: recording, but an operator changing their mind owes no justification).
_REASON_STATUSES: frozenset[str] = frozenset({"reject", "defer", "withdraw"})

#: Gate verdicts the machine gate-path stamps -- distinct from the human
#: approve/reject/defer/mark_ingested decisions above. A ``merged`` verdict
#: auto-advances ``approved -> ingested`` (mirroring ``mark_ingested``'s
#: legality); a ``refused`` verdict stamps the outcome and leaves ``status``
#: unchanged (the suggestion stays re-workable).
GATE_VERDICT_MERGED = "merged"
GATE_VERDICT_REFUSED = "refused"
_GATE_VERDICTS: frozenset[str] = frozenset({GATE_VERDICT_MERGED, GATE_VERDICT_REFUSED})

#: The gap lifecycle's *human* transitions -- :data:`_ALLOWED_FROM`'s sibling one
#: queue over. Each decision has exactly one legal source, so one mapping carries
#: the whole legality table. ``resolved`` is the *machine's* terminal state
#: (:func:`apply_gate_outcome` stamps it on a merge) and is deliberately the
#: source of neither: undoing a merge is a vault operation, not a queue edit.
_GAP_ALLOWED_FROM: Mapping[str, frozenset[str]] = {
    "dismiss": frozenset({"open"}),
    "reopen": frozenset({"dismissed"}),
}
#: The status each gap decision moves the record to (the mapping's mirror image).
_GAP_TARGET_STATUS: Mapping[str, str] = {"dismiss": "dismissed", "reopen": "open"}
#: Which gap decisions require a non-empty reason. ``dismiss`` must say why the
#: gap is not worth sourcing; ``reopen``'s reason is advisory, mirroring
#: ``plan_decision``'s ``reject``-only requirement on the suggestion lifecycle.
_GAP_REASON_REQUIRED: frozenset[str] = frozenset({"dismiss"})


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
    #: every drain, so it is named rather than folded into the count.
    gaps_fully_in_vault: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionPlan:
    """A validated, un-committed decision -- the dry-run preview seam.

    Produced by :func:`plan_decision` (pure, no I/O) so a two-phase MCP tool can
    preview a transition without writing, and :func:`apply_decision` can reuse the
    identical validation before it commits. One state machine, one home.
    """

    from_status: str
    to_status: str
    decided_reason: str | None


@dataclass(frozen=True)
class DecisionResult:
    """The outcome of a committed decision, for the tool envelope to render."""

    suggestion_id: str
    decision: str
    from_status: str
    to_status: str
    decided_at: str | None
    decided_reason: str | None
    ingested_at: str | None
    candidate_title: str
    changed: bool
    commit_sha: str


@dataclass(frozen=True)
class GapDecisionResult:
    """The outcome of a committed gap transition, for the tool envelope to render.

    ``reason`` is echoed back cleaned and is also persisted as ``decided_reason``
    on the ``GapRecord`` itself, mirroring the sibling ``apply_decision``'s
    treatment of a suggestion's ``decided_reason`` -- a dismissed (or reopened)
    gap's reason survives a re-read rather than existing only in this one-shot
    result. The commit subject and ``log.md`` still carry no reason, since
    ``format_commit_subject`` takes only ``(op, topic, title)``.
    """

    gap_id: str
    topic: str
    decision: str
    from_status: str
    to_status: str
    reason: str | None
    decided_at: str
    question: str
    changed: bool
    commit_sha: str
    #: Suggestions closed alongside a ``dismiss`` -- the gap's still-open
    #: (pending/approved/deferred) records, rejected in the same commit so a
    #: dismissed gap cannot strand approved sources in the queue. Empty for
    #: ``reopen`` and for a dismiss with no open suggestions.
    cascaded_suggestion_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportedGapResult:
    """The outcome of one :func:`report_gap`, for the tool envelope to render.

    ``written`` is ``False`` when an open gap with the same deterministic ``qa_id``
    already existed -- the write is a dedup no-op (composes with
    ``write_gap_records``' own ``(qa_id, fault_class)`` dedup), so a repeated
    identical report never appends a duplicate record.
    """

    topic: str
    gap_id: str
    qa_id: str
    question: str
    fault_class: str
    status: str
    origin: str
    reason: str | None
    reference_pages: tuple[str, ...]
    written: bool


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


def suggestions_path(topic: str) -> str:
    """Vault-relative path of a topic's ``suggestions.jsonl`` (mirrors ``gaps_path``)."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"topic must be a single path segment, got {topic!r}")
    return f"{cleaned}/{_KNOTICA_DIR}/{_SUGGESTIONS_DIRNAME}/{_SUGGESTIONS_FILENAME}"


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
    """Heal the queue, stage the new candidates, write once (lock already held).

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
            if record.suggestion_id in known_ids:
                revived[record.suggestion_id] = record
            else:
                new_records.append(record)
        in_vault += gap_in_vault
        if built and gap_in_vault == len(built):
            inert_gaps.append(gap.gap_id)

    written = len(new_records) + len(revived)
    records = [revived.get(record.suggestion_id, record) for record in healed] + new_records
    if written or healed_count:
        _write_suggestions(store, root, topic, records, staged=written, closed=healed_count)
    return RefreshResult(
        service_available=True,
        gaps_considered=0,
        gaps_drained=0,
        suggestions_written=written,
        candidates_already_in_vault=in_vault,
        stale_suggestions_closed=healed_count,
        gaps_fully_in_vault=tuple(inert_gaps),
    )


def build_default_discovery_service(
    *,
    config_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> DiscoveryService | None:
    """Construct the real ``DiscoveryService`` from config + env keys, or ``None``.

    Resolves the ``[gapfill.search]`` provider chain, builds an adapter for each
    provider that has a resolvable API key, and composes it with the keyless
    ``OpenAlexEnricher`` + ``ReputabilityScorer``. Returns ``None`` -- never raises
    -- when no provider is configured (no key), so the drain degrades to a no-op on
    a key-less host. All of ``discovery/`` is imported lazily here so this module
    stays off the MCP cold-start path. A malformed ``[gapfill.search]`` value still
    raises (a real operator error, distinct from "unconfigured").
    """
    from knotica.discovery.openalex import OpenAlexEnricher
    from knotica.discovery.reputability import ReputabilityScorer
    from knotica.discovery.service import DiscoveryService

    search_config = _resolve_search_config(config_path)
    providers = [
        provider
        for name in search_config.providers
        for provider in (_build_provider(name, environ=environ),)
        if provider is not None
    ]
    if not providers:
        return None
    return DiscoveryService(
        providers, OpenAlexEnricher(mailto=search_config.mailto), ReputabilityScorer()
    )


def plan_decision(
    record: SuggestionRecord,
    *,
    decision: str,
    reason: str | None = None,
) -> DecisionPlan:
    """Validate a decision against the D2 lifecycle; return the planned transition (pure).

    Raises a typed ``KnoticaError`` for an unknown decision, an illegal transition
    (the record's current status is not a legal source for the decision), or a
    reject with an empty/blank reason. No I/O, no mutation -- the dry-run preview
    seam a two-phase tool reuses before :func:`apply_decision` commits.
    """
    allowed_from = _ALLOWED_FROM.get(decision)
    if allowed_from is None:
        raise _invalid(
            f"decision must be one of {'|'.join(sorted(_ALLOWED_FROM))}, got {decision!r}",
            f"Pass a valid decision: {', '.join(sorted(_ALLOWED_FROM))}.",
        )
    if record.status not in allowed_from:
        raise _invalid(
            f"suggestion {record.suggestion_id!r} is {record.status!r}; cannot {decision}",
            f"Only a {'/'.join(sorted(allowed_from))} suggestion can be {decision}ed. "
            + _legal_exits_hint(record.status),
        )
    cleaned = (reason or "").strip()
    if decision == "reject" and not cleaned:
        raise _invalid(
            "reject requires a non-empty reason",
            'Pass reason="…" explaining why this source was rejected.',
        )
    decided_reason = cleaned or None if decision in _REASON_STATUSES else None
    return DecisionPlan(
        from_status=record.status,
        to_status=_TARGET_STATUS[decision],
        decided_reason=decided_reason,
    )


def apply_decision(
    store: VaultStore,
    root: str | Path,
    topic: str,
    suggestion_id: str,
    *,
    decision: str,
    reason: str | None = None,
    clock: Callable[[], str] | None = None,
) -> DecisionResult:
    """Apply one approve / reject / defer / mark-ingested transition, one commit.

    Reads ``suggestions.jsonl``, finds the record by ``suggestion_id``, validates
    the transition via :func:`plan_decision` (raising a typed ``KnoticaError`` on an
    illegal transition or an empty reject reason before any write), rewrites that
    one record's ``status`` + ``decided_at``/``decided_reason`` (or ``ingested_at``
    for mark-ingested) and commits the whole file once in an own ``VaultTransaction``.
    Imports nothing from ``discovery`` -- safe for an MCP tool to call on the
    cold-start path. Raises ``ValueError`` when no record has ``suggestion_id``.
    """
    stamp = clock or _utc_now_iso
    path = suggestions_path(topic)
    records = _read_suggestions(store, topic)
    index = _index_of(records, suggestion_id)
    if index is None:
        raise ValueError(f"no suggestion {suggestion_id!r} in topic {topic!r}")

    record = records[index]
    plan = plan_decision(record, decision=decision, reason=reason)
    updated = _mutate(record, plan, stamp=stamp)
    body = _serialize(_replace_at(records, index, updated))
    title = f"{decision} suggestion {suggestion_id[:8]}"
    with VaultTransaction(store, Path(root), _REVIEW_OP, topic, title) as txn:
        txn.write(path, body)
    return DecisionResult(
        suggestion_id=suggestion_id,
        decision=decision,
        from_status=plan.from_status,
        to_status=plan.to_status,
        decided_at=updated.decided_at,
        decided_reason=updated.decided_reason,
        ingested_at=updated.ingested_at,
        candidate_title=_candidate_title(record.candidate),
        changed=txn.result.changed,
        commit_sha=txn.result.commit_sha,
    )


def apply_gate_outcome(
    store: VaultStore,
    root: str | Path,
    topic: str,
    suggestion_id: str,
    *,
    verdict: str,
    gate_outcome: Mapping[str, object],
    clock: Callable[[], str] | None = None,
) -> DecisionResult:
    """Stamp a source candidate's gate ``gate_outcome`` in one commit (machine path).

    The gate-path companion to :func:`apply_decision`: where that mediates the
    *human* approve / reject / defer / mark-ingested lifecycle, this records the
    *machine* gate verdict on an already-approved source candidate. On
    ``verdict="merged"`` it auto-advances ``approved -> ingested`` (mirroring
    ``mark_ingested``'s legality check -- legal only from ``approved``) and stamps
    ``ingested_at``; on ``verdict="refused"`` it leaves ``status`` untouched (the
    suggestion stays re-workable). Either way it rewrites exactly one record's
    ``gate_outcome`` and commits once in its own :class:`VaultTransaction`.

    A ``merged`` verdict **also closes the originating gap** ``open -> resolved``
    (:func:`_close_gap_body`), declared to that same transaction rather than a
    second one: the source candidate that filled the hole is the event that
    closes it, so the two writes are one operation and land in one commit. A
    ``refused`` verdict leaves the gap ``open`` -- the hole is still there.

    The human-decision tables (:data:`_ALLOWED_FROM` / :data:`_TARGET_STATUS` /
    :func:`apply_decision`) are untouched. Raises ``ValueError`` when no record
    has ``suggestion_id``.
    """
    if verdict not in _GATE_VERDICTS:
        raise _invalid(
            f"gate verdict must be one of {'|'.join(sorted(_GATE_VERDICTS))}, got {verdict!r}",
            "Pass verdict='merged' or verdict='refused'.",
        )
    stamp = clock or _utc_now_iso
    path = suggestions_path(topic)
    records = _read_suggestions(store, topic)
    index = _index_of(records, suggestion_id)
    if index is None:
        raise ValueError(f"no suggestion {suggestion_id!r} in topic {topic!r}")

    record = records[index]
    updated = _stamp_gate_outcome(
        record, verdict=verdict, gate_outcome=dict(gate_outcome), stamp=stamp
    )
    body = _serialize(_replace_at(records, index, updated))
    # Planned before the transaction opens (its block declares writes and nothing
    # else) and declared to that *same* transaction: buffered together, applied
    # together, rolled back together. No interleaving leaves one half standing.
    closed_gaps = (
        _close_gap_body(store, topic, record.gap_id) if verdict == GATE_VERDICT_MERGED else None
    )
    title = f"gate {verdict} suggestion {suggestion_id[:8]}"
    with VaultTransaction(store, Path(root), _REVIEW_OP, topic, title) as txn:
        txn.write(path, body)
        if closed_gaps is not None:
            txn.write(gaps_path(topic), closed_gaps)
    return DecisionResult(
        suggestion_id=suggestion_id,
        decision=f"gate_{verdict}",
        from_status=record.status,
        to_status=updated.status,
        decided_at=updated.decided_at,
        decided_reason=updated.decided_reason,
        ingested_at=updated.ingested_at,
        candidate_title=_candidate_title(record.candidate),
        changed=txn.result.changed,
        commit_sha=txn.result.commit_sha,
    )


def _stamp_gate_outcome(
    record: SuggestionRecord,
    *,
    verdict: str,
    gate_outcome: dict[str, object],
    stamp: Callable[[], str],
) -> SuggestionRecord:
    """Return ``record`` with ``gate_outcome`` set; on merge advance approved->ingested.

    A ``refused`` verdict leaves ``status`` (and every timestamp) unchanged; a
    ``merged`` verdict requires the record be ``approved`` (mirroring
    ``mark_ingested``'s legality) and moves it to ``ingested`` with a fresh
    ``ingested_at``.
    """
    if verdict == GATE_VERDICT_MERGED:
        if record.status != "approved":
            raise _not_mergeable(record.suggestion_id, record.status)
        return replace(record, status="ingested", ingested_at=stamp(), gate_outcome=gate_outcome)
    return replace(record, gate_outcome=gate_outcome)


def require_gate_mergeable(store: VaultStore, topic: str, suggestion_id: str) -> None:
    """Raise unless ``suggestion_id`` is still ``approved`` -- the pre-merge check.

    :func:`apply_gate_outcome` refuses a ``merged`` verdict on a non-``approved``
    record, but it runs *after* the branch has been fast-forwarded onto the
    default branch, so that refusal alone leaves the source merged and the
    record unstamped. The gate calls this before the merge, where the same
    refusal costs nothing. Raises ``ValueError`` when no record has
    ``suggestion_id`` (the same signal :func:`apply_gate_outcome` gives).
    """
    records = _read_suggestions(store, topic)
    index = _index_of(records, suggestion_id)
    if index is None:
        raise ValueError(f"no suggestion {suggestion_id!r} in topic {topic!r}")
    if records[index].status != "approved":
        raise _not_mergeable(suggestion_id, records[index].status)


def _not_mergeable(suggestion_id: str, status: str) -> KnoticaError:
    """The refusal both gate checkpoints raise, worded once."""
    return _invalid(
        f"suggestion {suggestion_id!r} is {status!r}; the gate can only "
        "merge (auto-ingest) an approved source candidate",
        "Only an approved suggestion's source candidate is auto-ingested on a gate pass.",
    )


def apply_gap_decision(
    store: VaultStore,
    root: str | Path,
    topic: str,
    gap_id: str,
    *,
    decision: str,
    reason: str | None = None,
    clock: Callable[[], str] | None = None,
) -> GapDecisionResult:
    """Apply one human ``dismiss`` / ``reopen`` gap transition, one commit.

    The gap queue's human path, and the sibling of :func:`apply_decision`: where
    that mediates the *suggestion* lifecycle, this mediates the *gap* lifecycle
    the suggestions are derived from. Validates the transition against
    :data:`_GAP_ALLOWED_FROM` -- ``dismiss`` legal only from ``open``, ``reopen``
    only from ``dismissed``, anything else a typed ``INVALID_ARGUMENT`` raised
    before any write -- then rewrites that one record's ``status`` and
    ``decided_reason`` and commits the whole queue once in its own
    :class:`VaultTransaction`. A ``dismiss`` also closes the gap's still-open
    suggestions in the same transaction (see :func:`_plan_dismiss_cascade`) --
    the human mirror of the gate's merge closing its originating gap; a
    ``reopen`` resurrects nothing, but it does un-block re-sourcing: a
    cascade-rejected record no longer dedups discovery, so re-draining the
    reopened gap re-proposes its sources.
    ``dismiss`` requires a non-empty ``reason``;
    ``reopen``'s is optional (see :class:`GapDecisionResult`). ``clock`` injects
    the reported ``decided_at`` stamp for deterministic tests. Raises
    ``ValueError`` when no record has ``gap_id``.

    Both queue reads happen inside the same
    :func:`~knotica.core.lock.vault_span_lock` the write's transaction reuses,
    so a concurrent ``suggestions_review`` commit landing mid-plan cannot be
    overwritten by the full-file body this rewrites.
    """
    stamp = clock or _utc_now_iso
    with vault_span_lock(Path(root)):
        return _apply_gap_decision_locked(
            store, root, topic, gap_id, decision=decision, reason=reason, stamp=stamp
        )


def _apply_gap_decision_locked(
    store: VaultStore,
    root: str | Path,
    topic: str,
    gap_id: str,
    *,
    decision: str,
    reason: str | None,
    stamp: Callable[[], str],
) -> GapDecisionResult:
    """Plan and commit one gap transition; the vault lock is already held."""
    gaps = _read_gaps(store, topic)
    index = _gap_index_of(gaps, gap_id)
    if index is None:
        raise ValueError(f"no gap {gap_id!r} in topic {topic!r}")

    gap = gaps[index]
    to_status, decided_reason = _plan_gap_decision(gap, decision=decision, reason=reason)
    body = _gaps_body_with(
        gaps, index, replace(gap, status=to_status, decided_reason=decided_reason)
    )
    decided_at = stamp()
    cascaded, suggestions_body = _plan_dismiss_cascade(
        _read_suggestions(store, topic) if decision == "dismiss" else [],
        gap_id,
        reason=decided_reason or "",
        decided_at=decided_at,
        protected=_published_source_id8s(root, topic),
    )
    title = f"{decision} gap {gap_id[:8]}"
    with VaultTransaction(store, Path(root), _GAP_REVIEW_OP, topic, title) as txn:
        txn.write(gaps_path(topic), body)
        if suggestions_body is not None:
            txn.write(suggestions_path(topic), suggestions_body)
    return GapDecisionResult(
        gap_id=gap_id,
        topic=topic,
        decision=decision,
        from_status=gap.status,
        to_status=to_status,
        reason=decided_reason,
        decided_at=decided_at,
        question=gap.question,
        changed=txn.result.changed,
        commit_sha=txn.result.commit_sha,
        cascaded_suggestion_ids=cascaded,
    )


#: Suggestion statuses a gap dismissal closes -- the ones still waiting on a
#: human. ``ingested`` is history and keeps its status; ``rejected`` is already
#: terminal.
_CASCADE_SOURCES: frozenset[str] = frozenset({"pending", "approved", "deferred"})
#: Prefix of the ``decided_reason`` a cascade writes. Load-bearing, not
#: cosmetic: it is also how the drain tells a cascade closure (the gap speaking)
#: from a human's rejection of the source itself, and only the latter dedups a
#: later re-drain. Writer and reader share this one declaration.
_CASCADE_REASON_PREFIX = "gap dismissed: "


def _plan_dismiss_cascade(
    records: Sequence[SuggestionRecord],
    gap_id: str,
    *,
    reason: str,
    decided_at: str,
    protected: frozenset[str] = frozenset(),
) -> tuple[tuple[str, ...], str | None]:
    """Close the dismissed gap's still-open suggestions as ``rejected`` (pure).

    A dismissed gap no longer wants a source, so leaving its pending/approved/
    deferred suggestions in the queue strands them: ``approved`` in particular
    has no human exit but ``withdraw``, and nothing would ever take it. Each
    closed record carries ``gap dismissed: <reason>`` so the rejection explains
    itself, and a record closed *that* way does not dedup future discovery --
    reopening the gap and re-draining re-proposes its sources
    (:func:`_is_cascade_rejection` is the reader of that marker).

    ``protected`` holds the ``id8`` infixes of live source-candidate branches;
    an ``approved`` record already published as one is skipped, because the
    gate merges its branch before stamping the record and would otherwise land
    a merged-but-unstamped source. Returns the closed ids and the rewritten
    ``suggestions.jsonl`` body, or ``(), None`` when nothing matches (the file
    is then left out of the commit entirely).
    """
    matched = [
        record
        for record in records
        if record.gap_id == gap_id
        and record.status in _CASCADE_SOURCES
        and not _is_protected(record, protected)
    ]
    if not matched:
        return (), None
    matched_ids = {record.suggestion_id for record in matched}
    updated = [
        replace(
            record,
            status="rejected",
            decided_at=decided_at,
            decided_reason=f"{_CASCADE_REASON_PREFIX}{reason}",
        )
        if record.suggestion_id in matched_ids
        else record
        for record in records
    ]
    return tuple(record.suggestion_id for record in matched), _serialize(updated)


def _is_cascade_rejection(record: SuggestionRecord) -> bool:
    """Whether ``record`` was closed by a gap dismissal rather than by a human.

    The dedup set a re-drain builds excludes these: the gap's dismissal said
    nothing about the source's worth, so a reopened gap must be able to re-stage
    the very candidates its dismissal closed. A genuine human ``reject`` stays
    in the set -- respecting that judgement is the point of deduping at all.
    """
    return record.status == "rejected" and (record.decided_reason or "").startswith(
        _CASCADE_REASON_PREFIX
    )


def _plan_gap_decision(
    gap: GapRecord, *, decision: str, reason: str | None
) -> tuple[str, str | None]:
    """The ``(to_status, decided_reason)`` ``decision`` moves ``gap`` to (pure).

    Raises a typed ``INVALID_ARGUMENT`` for an unknown decision, an illegal
    transition, or a ``dismiss`` with an empty/blank reason -- checked in that
    order, matching :func:`plan_decision`'s illegal-transition-before-reason
    sequencing for the sibling suggestion lifecycle.
    """
    allowed_from = _GAP_ALLOWED_FROM.get(decision)
    if allowed_from is None:
        raise _invalid(
            f"decision must be one of {'|'.join(sorted(_GAP_ALLOWED_FROM))}, got {decision!r}",
            "Pass a valid gap decision: dismiss or reopen.",
        )
    if gap.status not in allowed_from:
        raise _invalid(
            f"gap {gap.gap_id!r} is {gap.status!r}; cannot {decision}",
            f"Only an {'/'.join(sorted(allowed_from))} gap can be {decision}ed. "
            + _legal_exits_hint(gap.status, _GAP_ALLOWED_FROM, noun="gap"),
        )
    cleaned = (reason or "").strip()
    if decision in _GAP_REASON_REQUIRED and not cleaned:
        raise _invalid(
            f"{decision} requires a non-empty reason",
            f'Pass reason="…" explaining why this gap is being {decision}ed.',
        )
    return _GAP_TARGET_STATUS[decision], cleaned or None


def _close_gap_body(store: VaultStore, topic: str, gap_id: str) -> str | None:
    """The whole ``gaps.jsonl`` body with ``gap_id`` flipped ``open -> resolved``.

    ``None`` -- meaning "declare no second write" -- when there is nothing to
    close: no queue file, no record under that ``gap_id``, or a record that is
    already terminal. Only an ``open`` gap is flipped, so a merge neither
    re-resolves a resolved gap nor overturns a human's ``dismiss``; and a
    ``None`` keeps the gate stamp a single-file transaction, which is what makes
    a gate on a gapless suggestion cost exactly the commit it always cost.
    """
    gaps = _read_gaps(store, topic)
    index = _gap_index_of(gaps, gap_id, status="open")
    if index is None:
        return None
    return _gaps_body_with(gaps, index, replace(gaps[index], status="resolved"))


def report_gap(
    store: VaultStore,
    root: str | Path,
    topic: str,
    question: str,
    *,
    reason: str | None = None,
    reference_pages: Sequence[str] = (),
    clock: Callable[[], str] | None = None,
) -> ReportedGapResult:
    """File one conversationally reported ``genuine_gap`` into the P1 queue.

    The client-as-brain calls this when a wiki query is answered poorly and the
    user confirms the gap. Constructs a ``genuine_gap``/``open`` :class:`GapRecord`
    with ``origin="reported"``, a ``qa_id`` derived deterministically from the
    question text (so identical reports collide), and empty eval-evidence fields
    (a reported gap carries no per-id score). Writes via the existing
    ``write_gap_records`` path in its own ``VaultTransaction`` -- whose
    ``(qa_id, fault_class)`` open-dedup drops a repeat of the same question, so a
    chatty client cannot spam the queue. ``reason`` is advisory context surfaced
    in the result and persisted verbatim (after stripping) as the record's
    ``reported_reason`` field. ``clock`` injects the
    ``detected_at`` stamp for deterministic tests. Raises a typed ``KnoticaError``
    on an empty/blank question (never fabricates content).
    """
    cleaned_question = question.strip()
    if not cleaned_question:
        raise _invalid(
            "a reported gap requires a non-empty question",
            "Pass the actual wiki query the user could not get answered.",
        )
    return _file_synthetic_gap(
        store,
        root,
        topic,
        cleaned_question,
        origin=GAP_ORIGIN_REPORTED,
        reason=reason,
        reference_pages=reference_pages,
        clock=clock,
    )


def file_retracted_gap(
    store: VaultStore,
    root: str | Path,
    topic: str,
    claim: str,
    *,
    verdict: str,
    report_path: str,
    reference_pages: Sequence[str] = (),
    clock: Callable[[], str] | None = None,
) -> ReportedGapResult:
    """File one ``origin="retracted"`` gap for a claim a guillotine verdict weakened.

    Called by the guillotine apply path after a RETRACT / DEMOTE / DISPUTE /
    DELETE_UNSUPPORTED_SYNTHESIS commit lands: the weakened claim text becomes the
    gap question verbatim (that knowledge now needs re-sourcing) and
    ``reported_reason`` records the verdict name + the guillotine report path. The
    ``qa_id`` is derived deterministically from the claim (shared with
    ``report_gap``) under a distinct ``retracted-`` prefix, so re-applying the same
    verdict dedups but a same-text *reported* gap stays separate. Writes via the
    existing ``write_gap_records`` path in its own ``VaultTransaction``. Raises a
    typed ``KnoticaError`` on an empty/blank claim (the caller isolates failures).
    """
    cleaned_claim = claim.strip()
    if not cleaned_claim:
        raise _invalid(
            "a retracted gap requires a non-empty claim",
            "Pass the weakened claim text the guillotine acted on.",
        )
    return _file_synthetic_gap(
        store,
        root,
        topic,
        cleaned_claim,
        origin=GAP_ORIGIN_RETRACTED,
        reason=f"{verdict} · {report_path}",
        reference_pages=reference_pages,
        clock=clock,
    )


def _file_synthetic_gap(
    store: VaultStore,
    root: str | Path,
    topic: str,
    text: str,
    *,
    origin: str,
    reason: str | None,
    reference_pages: Sequence[str],
    clock: Callable[[], str] | None,
) -> ReportedGapResult:
    """Shared body for filing an origin-tagged synthetic ``genuine_gap`` (no eval evidence).

    ``text`` is the already-cleaned proposer text (question or weakened claim);
    the ``origin`` selects the ``qa_id`` prefix so different-provenance gaps with
    identical text never collide. The record is written through the reused
    ``write_gap_records`` path, whose ``(qa_id, fault_class)`` open-dedup drops a
    repeat, so ``written`` reports whether this call actually appended a record.
    """
    # A synthetic gap is filed AGAINST a topic, never a way to create one — an
    # unguarded report once scaffolded a stray topic the loop began tending.
    topic = require_topic(store, topic)
    stamp = clock or _utc_now_iso
    qa_id = _synthetic_qa_id(text, origin)
    fault_class = FaultClass.GENUINE_GAP
    already_open = any(gap.qa_id == qa_id for gap in _open_genuine_gaps(store, topic))
    pages = tuple(reference_pages)
    cleaned_reason = (reason.strip() or None) if reason else None
    record = _build_synthetic_gap(
        topic,
        qa_id,
        fault_class,
        text,
        pages,
        origin=origin,
        reported_reason=cleaned_reason,
        detected_at=stamp(),
    )
    write_gap_records(store, root, topic, [record])
    return ReportedGapResult(
        topic=topic,
        gap_id=record.gap_id,
        qa_id=qa_id,
        question=text,
        fault_class=fault_class,
        status="open",
        origin=origin,
        reason=cleaned_reason,
        reference_pages=pages,
        written=not already_open,
    )


def _build_synthetic_gap(
    topic: str,
    qa_id: str,
    fault_class: str,
    question: str,
    reference_pages: tuple[str, ...],
    *,
    origin: str,
    reported_reason: str | None,
    detected_at: str,
) -> GapRecord:
    """Compose an origin-tagged synthetic gap record with empty eval evidence."""
    return GapRecord(
        gap_id=_reported_gap_id(topic, qa_id, fault_class),
        topic=topic,
        qa_id=qa_id,
        fault_class=fault_class,
        status="open",
        classifier_version=_NO_CLASSIFIER,
        detected_generation=_NO_GENERATION,
        detected_at=detected_at,
        scalar_at_detection=0.0,
        baseline_scalar=0.0,
        question=question,
        reference_pages=reference_pages,
        reference_pages_exist=False,
        evidence=GapEvidence(
            quality_delta=0.0,
            qa_accuracy_delta=0.0,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=_NO_GENERATION,
        ),
        manifest_ref="",
        origin=origin,
        reported_reason=reported_reason,
    )


# ---------------------------------------------------------------------------
# Drain internals -- gap reading, selection, dedup, one-commit write
# ---------------------------------------------------------------------------


def _read_gaps(store: VaultStore, topic: str) -> list[GapRecord]:
    """Parse a topic's whole gap queue (empty when the file is absent/blank)."""
    path = gaps_path(topic)
    if not store.exists(path):
        return []
    text = store.read_text(path)
    return parse_gaps_jsonl(text) if text.strip() else []


def _open_genuine_gaps(store: VaultStore, topic: str) -> list[GapRecord]:
    """The open ``genuine_gap`` records eligible for a drain (dilution excluded)."""
    return [
        gap
        for gap in _read_gaps(store, topic)
        if gap.fault_class == FaultClass.GENUINE_GAP and gap.status == "open"
    ]


def _gap_index_of(
    gaps: Sequence[GapRecord],
    gap_id: str,
    *,
    status: str | None = None,
) -> int | None:
    """Position of the record under ``gap_id``, optionally required to be ``status``."""
    return next(
        (
            index
            for index, gap in enumerate(gaps)
            if gap.gap_id == gap_id and (status is None or gap.status == status)
        ),
        None,
    )


def _gaps_body_with(gaps: Sequence[GapRecord], index: int, updated: GapRecord) -> str:
    """The whole ``gaps.jsonl`` body with the record at ``index`` replaced."""
    replaced = list(gaps)
    replaced[index] = updated
    return "\n".join(gap.to_json_line() for gap in replaced) + "\n"


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


#: Statuses the queue-healing pass may close; ``ingested``/``rejected`` are
#: terminal and a healed record must never overwrite a decision already made.
_HEALABLE: frozenset[str] = frozenset({"pending", "approved", "deferred"})
#: Precedence when several editions of one source survive per gap: a human
#: decision outranks the undecided, then the better-ranked candidate wins.
_HEAL_STATUS_RANK: Mapping[str, int] = {"approved": 2, "deferred": 1, "pending": 0}


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


def _is_protected(record: SuggestionRecord, protected: frozenset[str]) -> bool:
    """Whether ``record`` is an approved suggestion with a live candidate branch."""
    return record.status == "approved" and record.suggestion_id[:_ID_INFIX_LENGTH] in protected


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


def _published_source_id8s(root: str | Path, topic: str) -> frozenset[str]:
    """The ``id8`` infixes of ``topic``'s live source-candidate branches.

    A suggestion whose ingest has published ``loop/c/<topic>/source-<id8>`` (or
    still holds its private ``loop/wip/`` session branch) has work in flight
    that the gate will merge and only then stamp. Both queue writers -- the
    dismiss cascade and the heal -- use this to leave such a record alone; the
    gate is what dispositions it. Skipping is deliberate: the alternative,
    refusing the whole dismissal, would punish an operator for an ingest they
    may not know exists.
    """
    from knotica.core.vcs import VaultVcs

    vcs = VaultVcs(root)
    leaf_prefix = f"{topic}/{_SOURCE_INFIX}"
    return frozenset(
        name.removeprefix(prefix).removeprefix(leaf_prefix)
        for prefix in (CANDIDATE_BRANCH_PREFIX, WIP_BRANCH_PREFIX)
        for name, _sha in vcs.list_branch_tips(f"{prefix}{leaf_prefix}")
    )


def _write_suggestions(
    store: VaultStore,
    root: str | Path,
    topic: str,
    records: Sequence[SuggestionRecord],
    *,
    staged: int,
    closed: int,
) -> None:
    """Rewrite ``suggestions.jsonl`` as the full healed+staged list, one commit."""
    title = f"refresh suggestions for {topic} ({staged} staged, {closed} closed)"
    with VaultTransaction(store, Path(root), _PROPOSE_OP, topic, title) as txn:
        txn.write(suggestions_path(topic), _serialize(records))


# ---------------------------------------------------------------------------
# Decide internals -- record lookup, mutation, serialization
# ---------------------------------------------------------------------------


def _read_suggestions(store: VaultStore, topic: str) -> list[SuggestionRecord]:
    """Parse a topic's staged suggestions (empty when the file is absent/blank)."""
    path = suggestions_path(topic)
    if not store.exists(path):
        return []
    text = store.read_text(path)
    return parse_suggestions_jsonl(text) if text.strip() else []


def _index_of(records: Sequence[SuggestionRecord], suggestion_id: str) -> int | None:
    return next(
        (index for index, record in enumerate(records) if record.suggestion_id == suggestion_id),
        None,
    )


def _mutate(
    record: SuggestionRecord,
    plan: DecisionPlan,
    *,
    stamp: Callable[[], str],
) -> SuggestionRecord:
    """Return ``record`` moved to the planned status, stamping the right timestamp."""
    now = stamp()
    if plan.to_status == "ingested":
        return replace(record, status="ingested", ingested_at=now)
    return replace(
        record,
        status=plan.to_status,
        decided_at=now,
        decided_reason=plan.decided_reason,
    )


def _replace_at(
    records: Sequence[SuggestionRecord],
    index: int,
    updated: SuggestionRecord,
) -> list[SuggestionRecord]:
    new_records = list(records)
    new_records[index] = updated
    return new_records


def _serialize(records: Sequence[SuggestionRecord]) -> str:
    return "\n".join(record.to_json_line() for record in records) + "\n"


def _candidate_title(candidate: Mapping[str, object]) -> str:
    title = candidate.get("title")
    return title if isinstance(title, str) else ""


# ---------------------------------------------------------------------------
# Discovery-boundary helpers (lazy imports) + shared utilities
# ---------------------------------------------------------------------------


def _resolve_search_config(config_path: str | None) -> SearchConfig:
    from knotica.discovery.config import resolve_search_config

    return resolve_search_config(config_path)


def _build_provider(name: str, *, environ: Mapping[str, str] | None) -> SearchProvider | None:
    """Build the search adapter for ``name`` when its credential resolves.

    Credentials resolve through :func:`~knotica.discovery.config.resolve_api_key`
    -- the process environment (or an injected ``environ``), then ``./.env`` and
    ``~/.config/knotica/.env``. Sharing that one chain with
    ``gapfill_config._discovery_key_available``, the probe computing the
    ``discover_on_regression`` conditional default from the same "is a key
    configured?" question, is load-bearing: this factory once read ``os.environ``
    alone, so a key kept in ``.env`` (which the repo's own ``.env.example``
    invites) flipped discovery *on* at config time and then yielded no provider at
    run time -- the drain reported itself enabled and did nothing. Do not narrow
    this back to the environment; the two sites must answer identically or the
    feature lies about its own state. A missing key or an unrecognized name raises
    ``NOT_CONFIGURED``, caught here so the factory returns ``None`` rather than
    raising. you.com is the sole shipped adapter (exa was cut).
    """
    from knotica.discovery.config import resolve_api_key

    try:
        api_key = resolve_api_key(name, environ=environ)
    except KnoticaError:
        return None
    if name == "youcom":
        from knotica.discovery.youcom import YouComProvider

        return YouComProvider(api_key)
    return None


def _source_key(candidate: Mapping[str, object]) -> str:
    """The dedup + identity key of one candidate: normalized DOI, else URL.

    Delegates to ``discovery.normalize``, the single declaration of the rule, so
    the queue's dedup cannot drift from the service's. The candidate is an opaque
    dict here -- what keeps ``core/records.py`` free of an edge into
    ``discovery/`` -- so its two fields are coerced at this boundary.
    """
    from knotica.discovery.normalize import source_key

    doi, url = candidate.get("doi"), candidate.get("url")
    return source_key(doi if isinstance(doi, str) else None, url if isinstance(url, str) else "")


def _candidate_url_key(candidate: Mapping[str, object]) -> str:
    """The candidate's URL identity alone -- the vault-dedup handshake key.

    Deliberately *not* ``_source_key``: stored provenance records no DOI, so
    the vault comparison is URL-to-URL, and DOI-first keying would let a
    DOI-carrying candidate slip past a URL-recorded ingest of the same source.
    """
    from knotica.discovery.normalize import normalize_url

    url = candidate.get("url")
    return normalize_url(url if isinstance(url, str) else "")


def _suggestion_id(topic: str, gap_id: str, source_key: str) -> str:
    """Stable 16-hex identity + dedup key over ``(topic, gap_id, source_key)``."""
    import hashlib

    return hashlib.sha1(f"{topic}|{gap_id}|{source_key}".encode()).hexdigest()[:16]


def _synthetic_qa_id(text: str, origin: str) -> str:
    """A deterministic synthetic ``qa_id`` from proposer text (stable across calls).

    Mirrors ``evals.golden``'s content-addressed id hashing (sha256, 16-hex slug)
    so identical text collides on the same ``qa_id`` -- the property
    ``write_gap_records``' open-dedup relies on to reject a repeat. The origin's
    prefix keeps a ``reported`` and a ``retracted`` gap distinct even when their
    source text is byte-identical.
    """
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{_ORIGIN_QA_ID_PREFIX[origin]}{digest[:16]}"


def _reported_gap_id(topic: str, qa_id: str, fault_class: str) -> str:
    """Stable 16-hex gap id over the identifying triple (mirrors the classifier's scheme)."""
    import hashlib

    return hashlib.sha1(f"{topic}|{qa_id}|{fault_class}".encode()).hexdigest()[:16]


def _invalid(message: str, fix: str) -> KnoticaError:
    """A typed argument-validation error (the house ``INVALID_ARGUMENT`` code)."""
    return KnoticaError(ErrorCode.INVALID_ARGUMENT, message, fix=fix)


def _legal_exits_hint(
    status: str,
    table: Mapping[str, frozenset[str]] = _ALLOWED_FROM,
    *,
    noun: str = "suggestion",
) -> str:
    """The decisions legal from ``status``, for a refused transition's fix text.

    A refusal that names only the attempted decision's legal sources leaves the
    caller stuck when the record's *actual* status has a different exit --
    ``approved`` has ``withdraw``, but a refused ``reject`` never said so. Kept
    generic over the ``decision -> legal source statuses`` shape so both
    lifecycle tables (:data:`_ALLOWED_FROM` for suggestions,
    :data:`_GAP_ALLOWED_FROM` for gaps) derive their hint from the machine
    rather than a hand-written string that can drift from it.
    """
    exits = sorted(decision for decision, sources in table.items() if status in sources)
    if not exits:
        return f"A {status!r} {noun} accepts no further decision."
    return f"From {status!r} the legal decisions are: {', '.join(exits)}."


def _append_jsonl_lines(existing_text: str, lines: Sequence[str]) -> str:
    """Append JSONL lines, preserving prior records and a single trailing newline."""
    block = "\n".join(lines) + "\n"
    if not existing_text.strip():
        return block
    return existing_text.rstrip("\n") + "\n" + block


def _utc_now_iso() -> str:
    """Wall-clock stamp in ISO-8601 UTC (``…Z`` suffix), matching the gap classifier."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
