"""Per-topic lane rails for ``wiki_status`` -- server-derived state for every
non-Home lane, through :func:`~knotica.core.process_model.derive_stages`.

Split out of :mod:`knotica.core.status` by cohesion (file-size ceiling): this
module's one concern is translating already-computed per-topic fields into a
lane's position payload. None of the adapters below introduces a second
notion of progress a status read has already produced elsewhere -- Learn's
watermark is the ingest journal's own position, Fill's is the suggestion
queue :mod:`knotica.core.status` already summarizes, Tend's checks reuse
``lint_violations``/notes-drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core import process_model
from knotica.core.gapfill import suggestions_path
from knotica.core.ingest_activity import read_ingest_activity
from knotica.core.loop_state import LoopStage, read_loop_state
from knotica.core.records import RecordParseError, SuggestionRecord
from knotica.store import VaultStore

__all__ = ["is_refused", "lanes_block", "read_suggestion_records"]

#: The lanes projected onto every topic row -- ``process_model.LANES`` minus
#: the railless ``home`` lane, so the vocabulary is read off the declaration
#: rather than restated here.
_LANE_RAIL_ORDER: tuple[str, ...] = tuple(lane for lane in process_model.LANES if lane != "home")

_IMPROVE_OBSERVE_INDEX = next(
    index
    for index, stage in enumerate(process_model.LANE_STAGES["improve"])
    if stage.id == "observe"
)

_FILL_STAGES = process_model.LANE_STAGES["fill"]
_FILL_APPROVE_INDEX = next(i for i, stage in enumerate(_FILL_STAGES) if stage.id == "approve")
_FILL_INGEST_INDEX = next(i for i, stage in enumerate(_FILL_STAGES) if stage.id == "ingest")
_FILL_GATE_INDEX = next(i for i, stage in enumerate(_FILL_STAGES) if stage.id == "gate")
_FILL_TERMINAL_WATERMARK = len(_FILL_STAGES)


def lanes_block(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    *,
    lint_violations: int,
    notes_drifted: int,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Every non-Home lane's rail for ``topic``, server-derived and total."""
    fill_watermark, fill_reason = _fill_watermark(store, topic)
    sequence_positions: dict[str, tuple[int | None, str | None]] = {
        "learn": (_learn_watermark(vault_path, topic), None),
        # Answer has no persisted state at all: every stage is pending.
        "answer": (None, None),
        "improve": (_improve_watermark(store, topic), None),
        "fill": (fill_watermark, fill_reason),
    }
    lanes = {
        lane: process_model.derive_stages(lane, {"watermark": watermark, "blocked_reason": reason})
        for lane, (watermark, reason) in sequence_positions.items()
    }
    lanes["tend"] = _tend_stages(lint_violations=lint_violations, notes_drifted=notes_drifted)
    return {lane: lanes[lane] for lane in _LANE_RAIL_ORDER}


def _learn_watermark(vault_path: Path, topic: str) -> int | None:
    """Learn's rail position, read off the real ingest-activity journal.

    Never a second, independently computed notion of progress: the active
    run's ``current_stage`` is matched against ``Stage.journal_stages`` --
    the same public field the rail itself is folded from, by identity.
    """
    active = read_ingest_activity(vault_path, topic=topic)["active_run"]
    if active is None:
        return None
    current_stage = active["current_stage"]
    for index, stage in enumerate(process_model.LANE_STAGES["learn"]):
        if current_stage in stage.journal_stages:
            return index
    return None


def _improve_watermark(store: VaultStore, topic: str) -> int | None:
    """Improve's rail position, read off the persisted loop state.

    Deliberately narrow: ``LoopStage.evaluating`` is the only value that
    unambiguously names a live rail position (the eval cycle itself, at
    ``observe``). Every other value reports idle rather than guessing --
    the idle/resting outcomes (``idle``/``passed``/``failed``), the unused
    ``promoting``, and the gate cycle's own transient sub-stages
    (``racing``/``merging``/``reverting``, each held for one atomic git span
    a poll essentially never observes) name no unambiguous *blocked* or
    *terminal* rail position from ``loop.stage``/``candidate_branch`` alone.
    Full reconciliation with the paired test suite's own testability gap is
    recorded in ``LEARNINGS.md``.
    """
    state = read_loop_state(store, topic)
    if state is not None and state.stage == LoopStage.evaluating:
        return _IMPROVE_OBSERVE_INDEX
    return None


def _fill_watermark(store: VaultStore, topic: str) -> tuple[int | None, str | None]:
    """Fill's rail position, read off suggestion ``status``/``gate_outcome``.

    Single-record signals, most-urgent-first: a rework-blocked suggestion
    outranks a fresh pending one, which outranks one approved and awaiting
    ingestion, which outranks one already settled (merged).
    """
    records = read_suggestion_records(store, topic)
    refused = next((r for r in records if r.status == "approved" and is_refused(r)), None)
    if refused is not None:
        return _FILL_GATE_INDEX, _refusal_reason(refused)
    if any(r.status == "pending" for r in records):
        return _FILL_APPROVE_INDEX, None
    if any(r.status == "approved" for r in records):
        return _FILL_INGEST_INDEX, None
    if any(r.status == "ingested" and _is_merged(r) for r in records):
        return _FILL_TERMINAL_WATERMARK, None
    if any(r.status == "ingested" for r in records):
        return _FILL_GATE_INDEX, None
    return None, None


def _refusal_reason(record: SuggestionRecord) -> str:
    """The reason a rework-blocked suggestion's most recent gate pass gives."""
    outcome = record.gate_outcome or {}
    return str(outcome.get("reason") or "the gate refused this candidate; rework and resubmit")


def _is_merged(record: SuggestionRecord) -> bool:
    """Whether ``record``'s ``gate_outcome`` (if any) carries a merged verdict."""
    outcome = record.gate_outcome
    return outcome is not None and outcome.get("verdict") == "merged"


def is_refused(record: SuggestionRecord) -> bool:
    """Whether ``record``'s ``gate_outcome`` (if any) carries a refused verdict.

    Shared with :mod:`knotica.core.status`'s ``_suggestion_block``, which
    surfaces the same ``refused_awaiting_rework`` signal.
    """
    outcome = record.gate_outcome
    return outcome is not None and outcome.get("verdict") == "refused"


def read_suggestion_records(store: VaultStore, topic: str) -> list[SuggestionRecord]:
    """Parse ``suggestions.jsonl`` into records, skipping malformed lines.

    Shared with :mod:`knotica.core.status`'s ``_suggestion_block``, so the
    file is parsed once per caller rather than the same loop existing twice.
    """
    path = suggestions_path(topic)
    if not store.exists(path):
        return []
    records: list[SuggestionRecord] = []
    for line in store.read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            records.append(SuggestionRecord.from_json_line(line))
        except (ValueError, RecordParseError):
            continue
    return records


def _tend_stages(*, lint_violations: int, notes_drifted: int) -> tuple[dict[str, Any], ...]:
    """Tend's checklist, from already-computed per-topic signals only.

    ``lint`` reflects the real ``lint_violations`` count and ``drift``
    reflects the real drifted-note count this same status read already
    produces -- no second computation. ``doctor``/``okf``/``migrate`` have no
    equivalently cheap per-topic signal today (a live health-check run is not
    the cheap read this projection is scoped to), so they report ``pending``
    honestly rather than fabricate a result.
    """
    checks = {
        "doctor": "pending",
        "lint": "blocked" if lint_violations else "complete",
        "okf": "pending",
        "migrate": "pending",
        "drift": "blocked" if notes_drifted else "complete",
    }
    reasons: dict[str, str] = {}
    if lint_violations:
        reasons["lint"] = f"{lint_violations} page(s) fail schema lint"
    if notes_drifted:
        reasons["drift"] = f"{notes_drifted} note(s) have drifted from their anchor"
    return process_model.derive_stages("tend", {"checks": checks, "reasons": reasons})
