"""Deterministic branch / variant scoreboard for dashboard compare + promote UX.

Aggregates scalars from compile-state, loop-state, metrics.jsonl, arena-state,
arena-history, and local git branch tips — no LLM, no mutation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from knotica.core.arena import read_arena_history, read_arena_state
from knotica.core.compiled import load_compiled
from knotica.core.compile_state import (
    CompileState,
    compile_history_id,
    find_compile_history,
    read_compile_state,
)
from knotica.core.loop import DEFAULT_BRANCH_PREFIX, RESULT_BRANCH_PREFIX
from knotica.core.loop_state import LoopState, loop_state_path, read_loop_state
from knotica.core.metrics import read_last_metrics, read_metrics_window
from knotica.core.page import TopicNotFoundError
from knotica.core.status import _is_topic, _pending_loop_candidates
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = [
    "SCOREBOARD_SCHEMA_VERSION",
    "gather_branch_scoreboard",
]

SCOREBOARD_SCHEMA_VERSION = 3

EntryKind = Literal["default", "compile", "loop_candidate", "loop_result", "arena_variant"]
CompileSlot = Literal["open", "history", "archived"]


def gather_branch_scoreboard(
    store: VaultStore,
    vault_path: Path,
    topic: str,
) -> dict[str, Any]:
    """Build the ``branch_scoreboard`` payload for one topic.

    Raises :class:`~knotica.core.page.TopicNotFoundError` when ``topic`` is invalid.
    """
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or not _is_topic(store, cleaned):
        raise TopicNotFoundError(topic or "(empty)")

    loop_state = read_loop_state(store, cleaned)
    compile_state = read_compile_state(store, cleaned)
    arena_state = read_arena_state(store, cleaned)
    last_metrics = read_last_metrics(store, cleaned)
    compiled = load_compiled(store, cleaned)

    baseline = _baseline_scalar(loop_state, last_metrics.scalar if last_metrics else None)
    baseline_meta = _baseline_meta(cleaned, loop_state, last_metrics)

    vcs: VaultVcs | None = None
    current_branch: str | None = None
    try:
        vcs = VaultVcs(vault_path)
        default_branch = vcs.default_branch()
        current_branch = vcs.current_branch()
        compile_tips = _tips_for_prefix(vcs, f"compile/{cleaned}/")
        loop_candidate_tips = _tips_for_prefix(vcs, DEFAULT_BRANCH_PREFIX)
        loop_result_tips = _tips_for_prefix(vcs, RESULT_BRANCH_PREFIX)
    except GitError:
        default_branch = "main"
        compile_tips = []
        loop_candidate_tips = []
        loop_result_tips = []

    pending_rows = _pending_loop_candidates(vault_path, loop_state)
    pending_by_branch = {row["branch"]: row for row in pending_rows}

    open_compile_branch = _resolve_open_compile_branch(compile_tips, compile_state, vcs)

    entries: list[dict[str, Any]] = []

    # Default branch baseline row.
    default_scalar = _default_scalar(last_metrics, compiled, loop_state)
    entries.append(
        _entry(
            kind="default",
            name=default_branch,
            sha=_short_sha(vcs.head_sha()) if vcs is not None else None,
            scalar=default_scalar,
            baseline=baseline,
            status="baseline",
            note=_default_note(compiled, loop_state),
            promotable=False,
        )
    )

    # Compile branches for this topic — at most one ``open`` slot.
    for branch, sha in compile_tips:
        slot: CompileSlot = "open" if branch == open_compile_branch else "history"
        scalar = None
        scalar_before = None
        status = "unknown"
        note = None
        created = None
        merged = vcs is not None and _compile_branch_merged_into_default(vcs, sha, default_branch)
        if compile_state is not None and compile_state.branch == branch:
            scalar = compile_state.scalar_after
            scalar_before = compile_state.scalar_before
            status = _compile_status(
                compile_state.stage.value,
                scalar,
                scalar_before,
                baseline,
                slot=slot,
                merged=merged,
            )
            created = compile_state.updated_at or None
            if scalar_before is not None and scalar is not None:
                note = f"compile {scalar_before:.4f} → {scalar:.4f}"
        elif vcs is not None:
            created = vcs.tip_committer_iso(sha)
            if merged:
                status = "promoted" if slot == "open" else "history"

        beats = _beats_baseline(scalar, baseline)
        promotable = (
            slot == "open" and status == "ready-to-promote" and beats is True and not merged
        )
        deletable = _compile_deletable(
            vcs=vcs,
            branch=branch,
            default_branch=default_branch,
            current_branch=current_branch,
            slot=slot,
            beats=beats,
            merged=merged,
        )
        entries.append(
            _entry(
                kind="compile",
                name=branch,
                sha=sha[:12],
                scalar=scalar,
                baseline=baseline,
                status=status,
                created=created,
                note=note,
                promotable=promotable,
                slot=slot,
                scalar_before=scalar_before,
                deletable=deletable,
                base_sha=_history_field(compile_state, branch, "base_sha"),
                head_sha=_history_field(compile_state, branch, "head_sha"),
                merge_sha=_history_field(compile_state, branch, "merge_sha"),
                history_id=_history_id(compile_state, branch),
                diff_available=_diff_available(compile_state, branch, vcs),
            )
        )

    _append_archived_compile_history(
        entries,
        compile_state=compile_state,
        compile_tips=compile_tips,
        baseline=baseline,
        vcs=vcs,
        topic=cleaned,
    )

    # Loop candidates (loop/c/*).
    for branch, sha in loop_candidate_tips:
        pending_row = pending_by_branch.get(branch)
        pending = pending_row["pending"] if pending_row else True
        scalar = _loop_candidate_scalar(branch, sha, loop_state, pending)
        status = "pending" if pending else _loop_candidate_status(branch, sha, loop_state)
        entries.append(
            _entry(
                kind="loop_candidate",
                name=branch,
                sha=sha[:12],
                scalar=scalar,
                baseline=baseline,
                status=status,
                note="awaiting loop_runner" if pending else "processed by runner",
                promotable=_loop_candidate_promotable(branch, sha, loop_result_tips, status),
            )
        )

    # Loop result branches (loop/r/*) — eval clone tips fetched for keep-merge.
    for branch, sha in loop_result_tips:
        entries.append(
            _entry(
                kind="loop_result",
                name=branch,
                sha=sha[:12],
                scalar=_loop_result_scalar(sha, loop_state),
                baseline=baseline,
                status="ready-to-promote",
                note="eval clone tip — merge with loop_promote",
                promotable=True,
            )
        )

    # Arena variants (in-memory race, not git branches).
    if arena_state is not None and arena_state.variants:
        race_note = arena_state.race_id or arena_state.stage.value
        for variant in arena_state.variants:
            entries.append(
                _entry(
                    kind="arena_variant",
                    name=f"{variant.id} ({variant.label})",
                    sha=None,
                    scalar=variant.scalar,
                    baseline=arena_state.baseline_scalar or baseline,
                    status=variant.status,
                    note=f"race {race_note}",
                    promotable=False,
                )
            )

    # Historical arena races (variants may outlive deleted loop/c branches).
    for row in read_arena_history(store, cleaned, limit=10):
        race_id = str(row.get("race_id", ""))
        for variant in row.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            vid = str(variant.get("id", "variant"))
            if _arena_variant_present(entries, race_id, vid):
                continue
            entries.append(
                _entry(
                    kind="arena_variant",
                    name=f"{vid} ({variant.get('label', vid)})",
                    sha=None,
                    scalar=_maybe_float(variant.get("scalar")),
                    baseline=_maybe_float(row.get("baseline_scalar")) or baseline,
                    status=str(variant.get("status", "scored")),
                    created=str(row.get("finished_at", "")) or None,
                    note=f"history race {race_id}",
                    promotable=False,
                )
            )

    # Metrics history rows tied to a candidate branch via loop-state cursor keys.
    _append_metrics_history(entries, store, cleaned, baseline, loop_state)

    entries.sort(key=_sort_key)
    return {
        "schema_version": SCOREBOARD_SCHEMA_VERSION,
        "topic": cleaned,
        "baseline": baseline,
        "baseline_meta": baseline_meta,
        "default_branch": default_branch,
        "open_compile_branch": open_compile_branch,
        "entries": entries,
    }


def _entry(
    *,
    kind: EntryKind,
    name: str,
    sha: str | None,
    scalar: float | None,
    baseline: float | None,
    status: str,
    created: str | None = None,
    note: str | None = None,
    promotable: bool = False,
    slot: CompileSlot | None = None,
    scalar_before: float | None = None,
    deletable: bool = False,
    base_sha: str | None = None,
    head_sha: str | None = None,
    merge_sha: str | None = None,
    history_id: str | None = None,
    diff_available: bool | None = None,
    branch_deleted: bool = False,
) -> dict[str, Any]:
    delta = None
    if scalar is not None and baseline is not None:
        delta = round(float(scalar) - float(baseline), 6)
    delta_before = None
    if scalar is not None and scalar_before is not None:
        delta_before = round(float(scalar) - float(scalar_before), 6)
    beats = _beats_baseline(scalar, baseline)
    row: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "sha": sha,
        "scalar": scalar,
        "baseline": baseline,
        "delta": delta,
        "delta_before": delta_before,
        "beats_baseline": beats,
        "status": status,
        "created": created,
        "note": note,
        "promotable": promotable,
    }
    if slot is not None:
        row["slot"] = slot
    if deletable:
        row["deletable"] = True
    if base_sha:
        row["base_sha"] = base_sha
    if head_sha:
        row["head_sha"] = head_sha
    if merge_sha:
        row["merge_sha"] = merge_sha
    if history_id:
        row["history_id"] = history_id
    if diff_available is not None:
        row["diff_available"] = diff_available
    if branch_deleted:
        row["branch_deleted"] = True
    return row


def _baseline_meta(
    topic: str,
    loop_state: LoopState | None,
    last_metrics: Any,
) -> dict[str, Any]:
    """Document that the gate baseline is per-topic, not vault-wide."""
    frozen = loop_state is not None and loop_state.baseline_scalar is not None
    last_scalar = float(last_metrics.scalar) if last_metrics is not None else None
    return {
        "scope": "topic",
        "source": "loop-state.json",
        "path": loop_state_path(topic),
        "frozen": frozen,
        "last_metrics_scalar": last_scalar,
    }


def _resolve_open_compile_branch(
    compile_tips: list[tuple[str, str]],
    compile_state: CompileState | None,
    vcs: VaultVcs | None,
) -> str | None:
    """Pick the single open compile branch (active pointer or newest tip)."""
    if not compile_tips:
        return None
    if compile_state is not None and compile_state.branch:
        active = compile_state.branch
        if any(branch == active for branch, _ in compile_tips):
            return active

    def sort_key(item: tuple[str, str]) -> tuple[int, str]:
        branch, sha = item
        if vcs is None:
            return (0, branch)
        iso = vcs.tip_committer_iso(sha) or ""
        return (0, iso)

    ranked = sorted(compile_tips, key=sort_key, reverse=True)
    return ranked[0][0]


def _beats_baseline(scalar: float | None, baseline: float | None) -> bool | None:
    if scalar is None or baseline is None:
        return None
    return float(scalar) > float(baseline)


def _sort_key(row: dict[str, Any]) -> tuple[int, float, str]:
    kind = row.get("kind")
    if kind == "default":
        return (3, float(row.get("scalar") or 0.0), row["name"])
    if kind == "compile":
        slot = row.get("slot")
        slot_rank = 0 if slot == "open" else 1 if slot == "history" else 2
        created = str(row.get("created") or "")
        return (slot_rank, -_iso_sortable(created), row["name"])
    scalar = row.get("scalar")
    if scalar is None:
        return (2, -1.0, row["name"])
    return (1, -float(scalar), row["name"])


def _iso_sortable(value: str) -> float:
    """Best-effort numeric sort key for ISO timestamps."""
    if not value:
        return 0.0
    cleaned = value.replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        return 0.0


def _baseline_scalar(loop_state: LoopState | None, last_scalar: float | None) -> float | None:
    if loop_state is not None and loop_state.baseline_scalar is not None:
        return float(loop_state.baseline_scalar)
    return last_scalar


def _default_scalar(
    last_metrics: Any,
    compiled: Any,
    loop_state: LoopState | None,
) -> float | None:
    if last_metrics is not None:
        return float(last_metrics.scalar)
    if compiled is not None and compiled.metrics.get("compiled") is not None:
        return float(compiled.metrics["compiled"])
    if loop_state is not None and loop_state.last_scalar is not None:
        return float(loop_state.last_scalar)
    return None


def _default_note(compiled: Any, loop_state: LoopState | None) -> str | None:
    parts: list[str] = []
    if compiled is not None:
        parts.append(f"compiled v{compiled.version}")
    if loop_state is not None and loop_state.baseline_scalar is not None:
        parts.append(f"gate baseline {loop_state.baseline_scalar:.4f}")
    return " · ".join(parts) if parts else "live default branch"


def _compile_status(
    stage: str,
    scalar: float | None,
    scalar_before: float | None,
    baseline: float | None,
    *,
    slot: CompileSlot,
    merged: bool = False,
) -> str:
    if stage in {"running", "optimizing", "evaluating"}:
        return "running"
    if stage == "failed":
        return "failed"
    if stage != "completed":
        return stage
    if merged:
        return "promoted" if slot == "open" else "history"
    if slot == "history":
        return "history"
    if scalar is not None and baseline is not None and float(scalar) <= float(baseline):
        return "under-baseline"
    if scalar is not None and scalar_before is not None and float(scalar) <= float(scalar_before):
        return "under-baseline"
    return "ready-to-promote"


def _compile_branch_merged_into_default(
    vcs: VaultVcs,
    branch_sha: str,
    default_branch: str,
) -> bool:
    """Return whether the compile branch tip is strictly contained in ``default_branch``."""
    try:
        default_sha = vcs.ref_sha(default_branch)
    except GitError:
        return False
    if branch_sha == default_sha:
        return False
    return vcs.is_ancestor(branch_sha, default_sha)


def _compile_deletable(
    *,
    vcs: VaultVcs | None,
    branch: str,
    default_branch: str,
    current_branch: str | None,
    slot: CompileSlot,
    beats: bool | None,
    merged: bool,
) -> bool:
    """Whether ``branch_delete`` should succeed for this compile branch."""
    if vcs is None:
        return False
    if branch == default_branch:
        return False
    if current_branch == branch:
        return False
    if not vcs.branch_exists(branch):
        return False
    if slot == "history":
        return True
    if merged:
        return True
    return beats is False


def _loop_candidate_scalar(
    branch: str,
    sha: str,
    loop_state: LoopState | None,
    pending: bool,
) -> float | None:
    if loop_state is None:
        return None
    if loop_state.candidate_branch == branch and loop_state.candidate_sha == sha:
        return loop_state.last_scalar
    if not pending and loop_state.cursors.get(branch) == sha:
        return loop_state.last_scalar if loop_state.candidate_branch == branch else None
    return None


def _loop_candidate_status(branch: str, sha: str, loop_state: LoopState | None) -> str:
    if loop_state is None:
        return "processed"
    if loop_state.candidate_branch == branch and loop_state.candidate_sha == sha:
        if loop_state.last_decision.value == "pass":
            return "won"
        if loop_state.last_decision.value == "fail":
            return "failed"
        return "evaluated"
    return "processed"


def _loop_candidate_promotable(
    branch: str,
    sha: str,
    loop_result_tips: list[tuple[str, str]],
    status: str,
) -> bool:
    if status not in {"won", "evaluated", "processed"}:
        return False
    short = sha[:12]
    return any(
        tip_sha.startswith(short) or branch.endswith(short) for _, tip_sha in loop_result_tips
    )


def _loop_result_scalar(sha: str, loop_state: LoopState | None) -> float | None:
    if loop_state is None:
        return None
    if loop_state.candidate_sha and loop_state.candidate_sha.startswith(sha[:12]):
        return loop_state.last_scalar
    return loop_state.last_scalar


def _append_metrics_history(
    entries: list[dict[str, Any]],
    store: VaultStore,
    topic: str,
    baseline: float | None,
    loop_state: LoopState | None,
) -> None:
    """Surface recent eval scalars when loop branches were already deleted."""
    if loop_state is None or not loop_state.cursors:
        return
    window = read_metrics_window(store, topic, limit=20)
    known_scalars = {row.get("scalar") for row in entries if row.get("scalar") is not None}
    for record in reversed(window["records"]):
        if record.scalar in known_scalars:
            continue
        # Only add when we have processed branches but no live tip with this scalar.
        if (
            loop_state.last_scalar is not None
            and abs(record.scalar - loop_state.last_scalar) < 1e-9
        ):
            branch_name = loop_state.candidate_branch or "loop/c/(deleted)"
            if any(e["name"] == branch_name and e["kind"] == "loop_candidate" for e in entries):
                continue
            entries.append(
                _entry(
                    kind="loop_candidate",
                    name=branch_name,
                    sha=(loop_state.candidate_sha or "")[:12] or None,
                    scalar=float(record.scalar),
                    baseline=baseline,
                    status=loop_state.last_decision.value
                    if loop_state.last_decision.value != "none"
                    else "evaluated",
                    note="from metrics.jsonl (branch may be deleted)",
                    promotable=False,
                )
            )
            break


def _arena_variant_present(entries: list[dict[str, Any]], race_id: str, vid: str) -> bool:
    needle = f"{vid}"
    for row in entries:
        if row.get("kind") != "arena_variant":
            continue
        if needle in row.get("name", "") and race_id in (row.get("note") or ""):
            return True
    return False


def _tips_for_prefix(vcs: VaultVcs, prefix: str) -> list[tuple[str, str]]:
    return vcs.list_branch_tips(prefix)


def _short_sha(full: str | None) -> str:
    if not full:
        return ""
    return full[:12] if len(full) > 12 else full


def _history_id(compile_state: CompileState | None, branch: str) -> str | None:
    entry = find_compile_history(compile_state, branch=branch)
    return entry.history_id if entry is not None else None


def _history_field(
    compile_state: CompileState | None,
    branch: str,
    field: str,
) -> str | None:
    entry = find_compile_history(compile_state, branch=branch)
    if entry is None:
        return None
    value = getattr(entry, field, None)
    return str(value) if value else None


def _diff_available(
    compile_state: CompileState | None,
    branch: str,
    vcs: VaultVcs | None,
) -> bool:
    entry = find_compile_history(compile_state, branch=branch)
    if entry is not None and entry.base_sha and entry.head_sha:
        return True
    if vcs is not None:
        merge_sha = entry.merge_sha if entry is not None else None
        if not merge_sha:
            merge_sha = vcs.find_merge_commit_for_branch(branch)
        if merge_sha and vcs.merge_parents(merge_sha) is not None:
            return True
    return False


def _append_archived_compile_history(
    entries: list[dict[str, Any]],
    *,
    compile_state: CompileState | None,
    compile_tips: list[tuple[str, str]],
    baseline: float | None,
    vcs: VaultVcs | None,
    topic: str,
) -> None:
    """Surface deleted compile branches from compile-state history."""
    if compile_state is None or not compile_state.history:
        pass
    live_names = {branch for branch, _ in compile_tips}
    present = {row["name"] for row in entries if row.get("kind") == "compile"}
    if compile_state is not None:
        for entry in compile_state.history:
            if entry.branch in live_names or entry.branch in present:
                continue
            _append_archived_row(
                entries,
                compile_state=compile_state,
                baseline=baseline,
                vcs=vcs,
                branch=entry.branch,
                scalar=entry.scalar_after,
                scalar_before=entry.scalar_before,
                promoted=entry.promoted,
                branch_deleted=entry.branch_deleted,
                created=entry.updated_at or entry.created_at or None,
                base_sha=entry.base_sha,
                head_sha=entry.head_sha,
                merge_sha=entry.merge_sha,
                history_id=entry.history_id,
            )
            present.add(entry.branch)

    if vcs is None:
        return
    for branch, merge_sha in vcs.list_compile_merge_commits(topic):
        if branch in live_names or branch in present:
            continue
        parents = vcs.merge_parents(merge_sha)
        base_sha = parents[0] if parents else None
        head_sha = parents[1] if parents else None
        history = find_compile_history(compile_state, branch=branch)
        _append_archived_row(
            entries,
            compile_state=compile_state,
            baseline=baseline,
            vcs=vcs,
            branch=branch,
            scalar=history.scalar_after if history else None,
            scalar_before=history.scalar_before if history else None,
            promoted=True,
            branch_deleted=True,
            created=None,
            base_sha=base_sha or (history.base_sha if history else None),
            head_sha=head_sha or (history.head_sha if history else None),
            merge_sha=merge_sha or (history.merge_sha if history else None),
            history_id=history.history_id if history else compile_history_id(branch),
            note_suffix="recovered from merge commit on main",
        )
        present.add(branch)


def _append_archived_row(
    entries: list[dict[str, Any]],
    *,
    compile_state: CompileState | None,
    baseline: float | None,
    vcs: VaultVcs | None,
    branch: str,
    scalar: float | None,
    scalar_before: float | None,
    promoted: bool,
    branch_deleted: bool,
    created: str | None,
    base_sha: str | None,
    head_sha: str | None,
    history_id: str,
    merge_sha: str | None = None,
    note_suffix: str = "",
) -> None:
    note_parts: list[str] = []
    if scalar_before is not None and scalar is not None:
        note_parts.append(f"compile {scalar_before:.4f} → {scalar:.4f}")
    if branch_deleted:
        note_parts.append("from history (branch deleted)")
    elif promoted:
        note_parts.append("promoted — branch removed")
    if note_suffix:
        note_parts.append(note_suffix)
    entries.append(
        _entry(
            kind="compile",
            name=branch,
            sha=_short_sha(head_sha),
            scalar=scalar,
            baseline=baseline,
            status="promoted" if promoted else "history",
            created=created,
            note=" · ".join(note_parts) if note_parts else None,
            promotable=False,
            slot="archived",
            scalar_before=scalar_before,
            deletable=False,
            base_sha=base_sha,
            head_sha=head_sha,
            merge_sha=merge_sha,
            history_id=history_id,
            diff_available=_diff_available(compile_state, branch, vcs),
            branch_deleted=branch_deleted,
        )
    )


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
