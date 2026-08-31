"""Every status transition of a suggestion record -- human decision and machine gate.

One state machine, one home. The human half (:func:`plan_decision` /
:func:`apply_decision`) mediates approve / reject / defer / mark-ingested /
withdraw over the D2 lifecycle; the machine half (:func:`apply_gate_outcome` /
:func:`require_gate_mergeable`) stamps the source-candidate gate's verdict and
mirrors ``mark_ingested``'s legality when that verdict is ``merged``. They live
together because the second is defined by the first: a gate merge is exactly the
``approved -> ingested`` move a human would otherwise claim, and splitting the two
legality checks apart is how they drift.

Imports nothing from ``discovery`` -- deliberately, and pinned by a fitness test:
an MCP tool on the cold-start path delegates straight to :func:`apply_decision`.

A ``merged`` verdict also closes the originating gap, whose body
:mod:`~knotica.core.gapfill.gap_review` composes; it is declared to the gate
stamp's *own* transaction so the two writes are one commit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from knotica.core.errors import KnoticaError
from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill.gap_review import _close_gap_body
from knotica.core.gapfill.queue_io import (
    _REVIEW_OP,
    _candidate_title,
    _index_of,
    _invalid,
    _legal_exits_hint,
    _read_suggestions,
    _replace_at,
    _serialize,
    _utc_now_iso,
    suggestions_path,
)
from knotica.core.records import SuggestionRecord
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

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
            + _legal_exits_hint(record.status, _ALLOWED_FROM),
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
