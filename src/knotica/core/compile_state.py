"""Persisted compile progress for dashboard polling (``compile-state.json``)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = [
    "CompileHistoryEntry",
    "CompileStage",
    "CompileState",
    "compile_history_id",
    "compile_state_path",
    "find_compile_history",
    "mark_compile_branch_deleted",
    "read_compile_state",
    "record_compile_finished",
    "record_compile_promoted",
    "write_compile_state",
    "empty_compile_state",
]


class CompileStage(StrEnum):
    idle = "idle"
    running = "running"
    optimizing = "optimizing"
    evaluating = "evaluating"
    completed = "completed"
    failed = "failed"


class CompileHistoryEntry(BaseModel):
    """One finished compile run — survives branch deletion for scoreboard + diffs."""

    history_id: str
    branch: str
    head_sha: str | None = None
    base_sha: str | None = None
    merge_sha: str | None = None
    scalar_before: float | None = None
    scalar_after: float | None = None
    promoted: bool = False
    branch_deleted: bool = False
    created_at: str = ""
    updated_at: str = ""


class CompileState(BaseModel):
    """Per-topic compile progress snapshot."""

    schema_version: int = 2
    topic: str
    stage: CompileStage = CompileStage.idle
    branch: str | None = None
    message: str | None = None
    trial: int = 0
    trial_total: int = 0
    scalar_before: float | None = None
    scalar_after: float | None = None
    error: str | None = None
    updated_at: str = Field(default="")
    history: list[CompileHistoryEntry] = Field(default_factory=list)

    def render(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic": self.topic,
            "stage": self.stage.value,
            "branch": self.branch,
            "message": self.message,
            "trial": self.trial,
            "trial_total": self.trial_total,
            "scalar_before": self.scalar_before,
            "scalar_after": self.scalar_after,
            "error": self.error,
            "updated_at": self.updated_at,
            "history": [entry.model_dump(mode="json") for entry in self.history],
        }


def compile_state_path(topic: str) -> str:
    return f"{topic.strip().strip('/')}/.knotica/compile-state.json"


def compile_history_id(branch: str) -> str:
    """Stable id for a compile branch — the short suffix after the last ``/``."""
    cleaned = branch.strip().rstrip("/")
    if "/" not in cleaned:
        return cleaned
    return cleaned.rsplit("/", 1)[-1]


def empty_compile_state(topic: str) -> CompileState:
    return CompileState(topic=topic.strip().strip("/"))


def read_compile_state(store: VaultStore, topic: str) -> CompileState | None:
    path = compile_state_path(topic)
    if not store.exists(path):
        return None
    try:
        data = json.loads(store.read_text(path))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CompileState.model_validate(data)
    except Exception:  # noqa: BLE001
        return None


def write_compile_state(
    store: VaultStore,
    vault_root: str | Path,
    state: CompileState,
    *,
    title: str = "compile state",
) -> CompileState:
    """Persist compile state under the vault lock (one commit)."""
    stamped = state.model_copy(
        update={"updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    body = json.dumps(stamped.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    with VaultTransaction(store, Path(vault_root), "compile", stamped.topic, title) as txn:
        txn.write(compile_state_path(stamped.topic), body)
    return stamped


def find_compile_history(
    state: CompileState | None,
    *,
    branch: str | None = None,
    history_id: str | None = None,
) -> CompileHistoryEntry | None:
    """Look up a history row by branch name or ``history_id``."""
    if state is None or not state.history:
        return None
    cleaned_branch = branch.strip() if branch else None
    cleaned_id = history_id.strip() if history_id else None
    for entry in state.history:
        if cleaned_id and entry.history_id == cleaned_id:
            return entry
        if cleaned_branch and entry.branch == cleaned_branch:
            return entry
    return None


def _upsert_history(state: CompileState, entry: CompileHistoryEntry) -> CompileState:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamped = entry.model_copy(update={"updated_at": now})
    if not stamped.created_at:
        stamped = stamped.model_copy(update={"created_at": now})
    rows = [row for row in state.history if row.history_id != stamped.history_id]
    rows.append(stamped)
    rows.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
    return state.model_copy(update={"history": rows})


def record_compile_finished(
    store: VaultStore,
    vault_root: str | Path,
    state: CompileState,
    *,
    branch: str,
    head_sha: str,
    base_sha: str,
    scalar_before: float | None,
    scalar_after: float | None,
) -> CompileState:
    """Append or refresh history when a compile branch is published."""
    entry = CompileHistoryEntry(
        history_id=compile_history_id(branch),
        branch=branch,
        head_sha=head_sha,
        base_sha=base_sha,
        scalar_before=scalar_before,
        scalar_after=scalar_after,
    )
    updated = _upsert_history(state, entry)
    return write_compile_state(
        store,
        vault_root,
        updated,
        title="record compile history",
    )


def record_compile_promoted(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    branch: str,
    *,
    merge_sha: str,
    base_sha: str,
    head_sha: str,
) -> CompileState | None:
    """Persist merge parents so prompt diffs survive branch deletion."""
    state = read_compile_state(store, topic) or empty_compile_state(topic)
    existing = find_compile_history(state, branch=branch)
    entry = existing or CompileHistoryEntry(
        history_id=compile_history_id(branch),
        branch=branch,
    )
    entry = entry.model_copy(
        update={
            "merge_sha": merge_sha,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "promoted": True,
        }
    )
    if existing is None:
        entry = entry.model_copy(
            update={
                "scalar_before": state.scalar_before,
                "scalar_after": state.scalar_after,
            }
        )
    updated = _upsert_history(state, entry)
    return write_compile_state(
        store,
        vault_root,
        updated,
        title="record compile promote",
    )


def mark_compile_branch_deleted(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    branch: str,
    *,
    head_sha: str | None = None,
    base_sha: str | None = None,
    merge_sha: str | None = None,
) -> CompileState | None:
    """Mark a compile branch deleted while keeping preserved SHAs."""
    state = read_compile_state(store, topic)
    if state is None:
        return None
    existing = find_compile_history(state, branch=branch)
    if existing is None:
        entry = CompileHistoryEntry(
            history_id=compile_history_id(branch),
            branch=branch,
            head_sha=head_sha,
            base_sha=base_sha,
            merge_sha=merge_sha,
            scalar_before=state.scalar_before,
            scalar_after=state.scalar_after,
            branch_deleted=True,
        )
    else:
        updates: dict[str, Any] = {"branch_deleted": True}
        if head_sha and not existing.head_sha:
            updates["head_sha"] = head_sha
        if base_sha and not existing.base_sha:
            updates["base_sha"] = base_sha
        if merge_sha and not existing.merge_sha:
            updates["merge_sha"] = merge_sha
        entry = existing.model_copy(update=updates)
    updated = _upsert_history(state, entry)
    return write_compile_state(
        store,
        vault_root,
        updated,
        title="mark compile branch deleted",
    )
