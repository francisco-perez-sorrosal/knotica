"""The gap lifecycle -- the human dismiss/reopen transition and its cascade.

A drain *starts* at an open gap; this module is where one *ends*. It owns the gap
queue's human transitions (:func:`apply_gap_decision`: ``open -> dismissed`` and
back), the dismissal's cascade onto the gap's own still-open suggestions
(:func:`_plan_dismiss_cascade`), and the machine close a merged gate verdict
performs (:func:`_close_gap_body`, called by
:mod:`~knotica.core.gapfill.review`'s gate path and declared to *its*
transaction). Filing gaps belongs to ``core.gap_classifier`` and
:mod:`~knotica.core.gapfill.synthetic`.

Two invariants are load-bearing here and easy to break by moving a line:

* **One commit.** The gap rewrite and the cascade's suggestion rewrite are
  declared to the *same* :class:`VaultTransaction` -- a dismissal is one
  operation however many files it touches.
* **Read inside the lock.** Both queue reads happen inside the
  :func:`~knotica.core.lock.vault_span_lock` the write's transaction reuses
  reentrantly, so a concurrent review commit landing mid-plan cannot be
  overwritten by the full-file body this rewrites.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill.queue_io import (
    _GAP_REVIEW_OP,
    _gap_index_of,
    _gaps_body_with,
    _invalid,
    _is_protected,
    _legal_exits_hint,
    _published_source_id8s,
    _read_gaps,
    _read_suggestions,
    _serialize,
    _utc_now_iso,
    suggestions_path,
)
from knotica.core.lock import vault_span_lock
from knotica.core.records import GapRecord, SuggestionRecord
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

#: The gap lifecycle's *human* transitions -- ``review._ALLOWED_FROM``'s sibling
#: one queue over. Each decision has exactly one legal source, so one mapping
#: carries the whole legality table. ``resolved`` is the *machine's* terminal
#: state (:func:`_close_gap_body` stamps it on a merge) and is deliberately the
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

#: Suggestion statuses a gap dismissal closes -- the ones still waiting on a
#: human. ``ingested`` is history and keeps its status; ``rejected`` is already
#: terminal.
_CASCADE_SOURCES: frozenset[str] = frozenset({"pending", "approved", "deferred"})
#: Prefix of the ``decided_reason`` a cascade writes. Load-bearing, not
#: cosmetic: it is also how the drain tells a cascade closure (the gap speaking)
#: from a human's rejection of the source itself, and only the latter dedups a
#: later re-drain. Writer and reader share this one declaration.
_CASCADE_REASON_PREFIX = "gap dismissed: "


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
