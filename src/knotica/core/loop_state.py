"""Persisted loop-runner state — the dashboard/MCP exposure surface for M2+.

The loop runner is otherwise stateless: it re-derives work from git tips. The
small JSON document at ``<topic>/.knotica/loop-state.json`` is the *only*
orchestration metadata the live vault carries so :func:`wiki_status` can
surface ``gate`` / ``loop.stage`` without a side channel.

Writes go through :class:`~knotica.core.transaction.VaultTransaction` (one
``knotica(loop): …`` commit). Reads are pure :class:`~knotica.store.VaultStore`
lookups. Shape is a Pydantic model so future LLM-facing loop payloads (M5
arena) share one validation style.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = [
    "LOOP_STATE_FILENAME",
    "LoopDecision",
    "LoopStage",
    "LoopState",
    "compute_gate",
    "loop_state_path",
    "read_loop_state",
    "write_loop_state",
]

_KNOTICA_DIR = ".knotica"
LOOP_STATE_FILENAME = "loop-state.json"
LOOP_STATE_SCHEMA_VERSION: Literal[1] = 1


class LoopStage(StrEnum):
    """Coarse runner stage exposed on ``wiki_status.loop.stage``."""

    idle = "idle"
    evaluating = "evaluating"
    merging = "merging"
    reverting = "reverting"
    passed = "passed"
    failed = "failed"


class LoopDecision(StrEnum):
    """Last gate decision recorded for the topic."""

    none = "none"
    pass_ = "pass"
    fail = "fail"

    @classmethod
    def from_gate(cls, passed: bool) -> LoopDecision:
        """Map a boolean gate outcome onto the enum."""
        return cls.pass_ if passed else cls.fail


class LoopState(BaseModel):
    """Topic-scoped loop orchestration metadata (JSON on disk)."""

    schema_version: Literal[1] = LOOP_STATE_SCHEMA_VERSION
    topic: str
    stage: LoopStage = LoopStage.idle
    baseline_scalar: float | None = None
    baseline_harness_version: str | None = None
    baseline_corpus_ref: str | None = None
    candidate_branch: str | None = None
    candidate_sha: str | None = None
    last_scalar: float | None = None
    last_generation: int | None = None
    last_harness_version: str | None = None
    last_decision: LoopDecision = LoopDecision.none
    last_error: str | None = None
    #: branch name → last tip SHA the runner finished processing
    cursors: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("topic")
    @classmethod
    def _topic_is_single_segment(cls, value: str) -> str:
        cleaned = value.strip().strip("/")
        if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
            raise ValueError(f"topic must be a single path segment, got {value!r}")
        return cleaned

    def touch(self) -> LoopState:
        """Return a copy with ``updated_at`` set to now (UTC)."""
        return self.model_copy(update={"updated_at": datetime.now(UTC)})

    def mark_processed(self, branch: str, sha: str) -> LoopState:
        """Record that ``branch`` tip ``sha`` has been fully handled."""
        cursors = dict(self.cursors)
        cursors[branch] = sha
        return self.model_copy(update={"cursors": cursors}).touch()


def loop_state_path(topic: str) -> str:
    """Vault-relative path of a topic's ``loop-state.json``."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"topic must be a single path segment, got {topic!r}")
    return f"{cleaned}/{_KNOTICA_DIR}/{LOOP_STATE_FILENAME}"


def read_loop_state(store: VaultStore, topic: str) -> LoopState | None:
    """Load loop state for ``topic``, or ``None`` when absent/unparseable."""
    path = loop_state_path(topic)
    if not store.exists(path):
        return None
    try:
        return LoopState.model_validate_json(store.read_text(path))
    except Exception:
        return None


def write_loop_state(
    store: VaultStore,
    vault_root: Path,
    state: LoopState,
    *,
    title: str = "update loop state",
) -> LoopState:
    """Persist ``state`` via one ``VaultTransaction`` and return the touched copy."""
    fresh = state.touch()
    path = loop_state_path(fresh.topic)
    payload = fresh.model_dump_json(indent=2) + "\n"
    with VaultTransaction(store, vault_root, "loop", fresh.topic, title) as txn:
        txn.write(path, payload)
    return fresh


def compute_gate(
    state: LoopState | None,
    *,
    last_scalar: float | None,
    last_harness_version: str | None = None,
) -> dict[str, Any]:
    """Derive the ``wiki_status.gate`` object from persisted state + last eval.

    * No baseline → ``unknown`` (honest until the runner freezes one).
    * Harness mismatch vs baseline → ``unknown`` (incomparable scalars).
    * Otherwise ``pass`` / ``fail`` by ``last_scalar >= baseline_scalar``.
      Prefer the metrics.jsonl scalar; fall back to ``state.last_scalar``.
    """
    scalar = last_scalar
    if scalar is None and state is not None:
        scalar = state.last_scalar

    if state is None or state.baseline_scalar is None:
        return {"state": "unknown", "baseline": None, "last_scalar": scalar}

    baseline = float(state.baseline_scalar)
    harness = last_harness_version or (state.last_harness_version if state else None)
    if state.baseline_harness_version and harness and harness != state.baseline_harness_version:
        return {"state": "unknown", "baseline": baseline, "last_scalar": scalar}

    if scalar is None:
        return {"state": "unknown", "baseline": baseline, "last_scalar": None}

    return {
        "state": "pass" if float(scalar) >= baseline else "fail",
        "baseline": baseline,
        "last_scalar": float(scalar),
    }


def empty_loop_state(topic: str) -> LoopState:
    """Construct a fresh idle state document for ``topic``."""
    return LoopState(topic=topic)
