"""The gap-fill drain + decide leaf -- join gaps to discovered sources, gate them.

Two responsibilities, one committed queue (``<topic>/.knotica/suggestions/
suggestions.jsonl``):

* **Drain** (:func:`refresh_suggestions_for_gaps`) reads the P1 gap queue, keeps
  only ``genuine_gap`` records still ``open``, formulates one deterministic query
  per gap, runs an injected ``DiscoveryService``, and stages one ``pending``
  :class:`~knotica.core.records.SuggestionRecord` per (gap, ranked candidate) --
  deduped on ``(gap_id, source_key)`` so a persistent regression never spams the
  queue -- writing once per drain in its own :class:`VaultTransaction`.
* **Decide** (:func:`apply_decision`) mediates the human approve / reject / defer /
  mark-ingested transition over the D2 lifecycle state machine, requiring a
  non-empty reason on reject, rewriting exactly one record in its own transaction.

This is the *only* P3 code that touches ``discovery/`` -- and it does so **lazily,
inside the function that needs it**, never at module top level. That keeps the
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

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gap_classifier import FaultClass, gaps_path, write_gap_records
from knotica.core.records import (
    GAP_ORIGIN_REPORTED,
    GAP_ORIGIN_RETRACTED,
    GapEvidence,
    GapRecord,
    SuggestionRecord,
    parse_gaps_jsonl,
    parse_suggestions_jsonl,
)
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
    "RefreshResult",
    "ReportedGapResult",
    "apply_decision",
    "apply_gate_outcome",
    "build_default_discovery_service",
    "build_suggestion_records",
    "file_retracted_gap",
    "formulate_query",
    "plan_decision",
    "refresh_suggestions_for_gaps",
    "report_gap",
    "suggestions_path",
]

#: Directory name under each topic that owns loop/eval artifacts.
_KNOTICA_DIR = ".knotica"
#: Subdir + basename of the per-topic human-approval suggestion queue.
_SUGGESTIONS_DIRNAME = "suggestions"
_SUGGESTIONS_FILENAME = "suggestions.jsonl"
#: Op slot of the drain's suggestion-propose commit (own transaction, one commit).
_PROPOSE_OP = "suggestion_propose"
#: Op slot of the approve/reject/defer/mark-ingested commit.
_REVIEW_OP = "suggestion_review"

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
}
#: The terminal status each decision moves a record to.
_TARGET_STATUS: Mapping[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "defer": "deferred",
    "mark_ingested": "ingested",
}
#: Decisions that carry a decided_reason (required for reject, optional for defer).
_REASON_STATUSES: frozenset[str] = frozenset({"reject", "defer"})

#: Gate verdicts the machine gate-path stamps -- distinct from the human
#: approve/reject/defer/mark_ingested decisions above. A ``merged`` verdict
#: auto-advances ``approved -> ingested`` (mirroring ``mark_ingested``'s
#: legality); a ``refused`` verdict stamps the outcome and leaves ``status``
#: unchanged (the suggestion stays re-workable).
GATE_VERDICT_MERGED = "merged"
GATE_VERDICT_REFUSED = "refused"
_GATE_VERDICTS: frozenset[str] = frozenset({GATE_VERDICT_MERGED, GATE_VERDICT_REFUSED})


@dataclass(frozen=True)
class RefreshResult:
    """The outcome of one drain, for the CLI / loop hook to summarize.

    ``service_available`` is ``False`` only when the drain was called with no
    configured discovery service (a clean no-op). ``gaps_drained`` counts the
    open ``genuine_gap`` records a discovery query was issued for; a gap whose
    candidates were all already suggested still counts as drained but contributes
    zero to ``suggestions_written`` (dedup).
    """

    service_available: bool
    gaps_considered: int
    gaps_drained: int
    suggestions_written: int


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
    formulates one query per surviving gap, runs ``service.discover``, dedups the
    produced candidates against every existing suggestion on ``(gap_id,
    source_key)``, and writes the survivors once in an own ``VaultTransaction``.
    A ``None`` service (no key configured) or zero survivors is a clean no-op --
    no transaction, no commit. A failure raised by ``service.discover`` is **not**
    caught (failure isolation is the loop hook's boundary).
    """
    open_gaps = _open_genuine_gaps(store, topic)
    considered = len(open_gaps)
    if service is None or not open_gaps:
        return RefreshResult(
            service_available=service is not None,
            gaps_considered=considered,
            gaps_drained=0,
            suggestions_written=0,
        )

    selected = _select_gaps(open_gaps, max_gaps)
    seen = _existing_dedup_keys(store, topic)
    new_records: list[SuggestionRecord] = []
    for gap in selected:
        built = build_suggestion_records(gap, service.discover(formulate_query(gap)), clock=clock)
        for record in built:
            key = (record.gap_id, _source_key(record.candidate))
            if key in seen:
                continue
            seen.add(key)
            new_records.append(record)

    if new_records:
        _write_suggestions(store, root, topic, new_records)
    return RefreshResult(
        service_available=True,
        gaps_considered=considered,
        gaps_drained=len(selected),
        suggestions_written=len(new_records),
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
            "Pass a valid decision: approve, reject, defer, or mark_ingested.",
        )
    if record.status not in allowed_from:
        raise _invalid(
            f"suggestion {record.suggestion_id!r} is {record.status!r}; cannot {decision}",
            f"Only a {'/'.join(sorted(allowed_from))} suggestion can be {decision}ed.",
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
    ``gate_outcome`` and commits the whole file once in its own
    :class:`VaultTransaction`. The human-decision tables
    (:data:`_ALLOWED_FROM` / :data:`_TARGET_STATUS` / :func:`apply_decision`) are
    untouched. Raises ``ValueError`` when no record has ``suggestion_id``.
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
    title = f"gate {verdict} suggestion {suggestion_id[:8]}"
    with VaultTransaction(store, Path(root), _REVIEW_OP, topic, title) as txn:
        txn.write(path, body)
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
            raise _invalid(
                f"suggestion {record.suggestion_id!r} is {record.status!r}; the gate can only "
                "merge (auto-ingest) an approved source candidate",
                "Only an approved suggestion's source candidate is auto-ingested on a gate pass.",
            )
        return replace(record, status="ingested", ingested_at=stamp(), gate_outcome=gate_outcome)
    return replace(record, gate_outcome=gate_outcome)


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
    in the result; the v1 record has no field for it (additive-only: only
    ``origin`` was added), so it is not persisted. ``clock`` injects the
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


def _open_genuine_gaps(store: VaultStore, topic: str) -> list[GapRecord]:
    """The open ``genuine_gap`` records eligible for a drain (dilution excluded)."""
    path = gaps_path(topic)
    if not store.exists(path):
        return []
    text = store.read_text(path)
    gaps = parse_gaps_jsonl(text) if text.strip() else []
    return [
        gap for gap in gaps if gap.fault_class == FaultClass.GENUINE_GAP and gap.status == "open"
    ]


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


def _existing_dedup_keys(store: VaultStore, topic: str) -> set[tuple[str, str]]:
    """Every ``(gap_id, source_key)`` already staged, at any status.

    Dedup is against *all* existing suggestions (not just non-terminal ones): a
    source already surfaced for a gap -- pending, approved, ingested, deferred, or
    already rejected -- is never re-proposed, so a persistent regression cannot
    spam the queue and a human's rejection is respected. This is a superset of the
    ``pending``/``approved``/``ingested`` dedup the acceptance criterion names.
    """
    records = _read_suggestions(store, topic)
    return {(record.gap_id, _source_key(record.candidate)) for record in records}


def _write_suggestions(
    store: VaultStore,
    root: str | Path,
    topic: str,
    records: Sequence[SuggestionRecord],
) -> None:
    """Append staged suggestions to ``suggestions.jsonl`` in one own commit."""
    path = suggestions_path(topic)
    existing = store.read_text(path) if store.exists(path) else ""
    body = _append_jsonl_lines(existing, [record.to_json_line() for record in records])
    title = f"{len(records)} gap-fill suggestions for {topic}"
    with VaultTransaction(store, Path(root), _PROPOSE_OP, topic, title) as txn:
        txn.write(path, body)


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
    """Build the search adapter for ``name`` when its key is in the environment.

    The auto-factory treats the **process environment** as the sole source of
    provider credentials (``os.environ`` by default, or an injected ``environ``) --
    it does not consult the ``.env`` fallback ``resolve_api_key`` offers, so the
    drain's configured/unconfigured decision is fully controllable and matches the
    live-demo's exported-``KNOTICA_YOUCOM_API_KEY`` usage. A missing key degrades
    that provider to absent (the factory then returns ``None``), never raising.
    you.com is the sole shipped adapter (exa was cut); an unrecognized-but-keyed
    name is skipped rather than trusted.
    """
    from knotica.discovery.config import env_var_for

    env = os.environ if environ is None else environ
    try:
        env_var = env_var_for(name)
    except KnoticaError:
        return None
    api_key = env.get(env_var)
    if not api_key:
        return None
    if name == "youcom":
        from knotica.discovery.youcom import YouComProvider

        return YouComProvider(api_key)
    return None


def _source_key(candidate: Mapping[str, object]) -> str:
    """The dedup + identity key of one candidate: normalized DOI, else URL.

    Reuses ``DiscoveryService``'s own normalizers (single source of truth for DOI
    prefix/case and URL trailing-slash/fragment handling) so the dedup gate cannot
    drift from the service's own dedup semantics.
    """
    from knotica.discovery.service import _normalize_doi, _normalize_url

    doi = candidate.get("doi")
    normalized_doi = _normalize_doi(doi if isinstance(doi, str) else None)
    if normalized_doi is not None:
        return f"doi:{normalized_doi}"
    url = candidate.get("url")
    return f"url:{_normalize_url(url if isinstance(url, str) else '')}"


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


def _append_jsonl_lines(existing_text: str, lines: Sequence[str]) -> str:
    """Append JSONL lines, preserving prior records and a single trailing newline."""
    block = "\n".join(lines) + "\n"
    if not existing_text.strip():
        return block
    return existing_text.rstrip("\n") + "\n" + block


def _utc_now_iso() -> str:
    """Wall-clock stamp in ISO-8601 UTC (``…Z`` suffix), matching the gap classifier."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
