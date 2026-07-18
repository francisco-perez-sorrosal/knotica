"""Prompt Evolution Arena — race ``query.md`` variants, promote winner or revert.

Phase-3a-lite without DSPy: N prompt bodies are scored (injectable evaluator),
leaderboard state is persisted for the dashboard, and a winner that clears the
baseline is committed as the topic (or root) ``query.md`` override.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from knotica.core.prompts import PROMPTS_DIR, resolve_prompt
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = [
    "ARENA_HISTORY_FILENAME",
    "ARENA_STATE_FILENAME",
    "ArenaStage",
    "ArenaState",
    "ArenaVariant",
    "ScoreFn",
    "VariantSpec",
    "append_arena_history",
    "arena_history_path",
    "arena_state_path",
    "generate_variant_bodies",
    "heuristic_arena_score",
    "load_base_query_body",
    "query_prompt_path",
    "race_variants",
    "read_arena_history",
    "read_arena_state",
    "write_arena_state",
]

_KNOTICA_DIR = ".knotica"
ARENA_STATE_FILENAME = "arena-state.json"
ARENA_HISTORY_FILENAME = "arena-history.jsonl"
ARENA_STATE_SCHEMA_VERSION: Literal[1] = 1

#: Score a (topic, vault_root, prompt_body) → scalar. Tests inject fakes.
ScoreFn = Callable[[str, Path, str], float]


class ArenaStage(StrEnum):
    """Coarse arena stage for dashboard / wiki_status."""

    idle = "idle"
    racing = "racing"
    promoting = "promoting"
    completed = "completed"
    reverted = "reverted"


class ArenaVariant(BaseModel):
    """One raced prompt variant and its score (null while pending)."""

    id: str
    label: str
    scalar: float | None = None
    status: Literal["pending", "scored", "winner", "lost"] = "pending"


class ArenaState(BaseModel):
    """Current/last arena race for a topic (JSON on disk)."""

    schema_version: Literal[1] = ARENA_STATE_SCHEMA_VERSION
    topic: str
    race_id: str | None = None
    stage: ArenaStage = ArenaStage.idle
    baseline_scalar: float | None = None
    variants: list[ArenaVariant] = Field(default_factory=list)
    winner_id: str | None = None
    winner_scalar: float | None = None
    candidate_branch: str | None = None
    message: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def touch(self) -> ArenaState:
        return self.model_copy(update={"updated_at": datetime.now(UTC)})

    def render(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class VariantSpec(BaseModel):
    """Input variant before scoring."""

    id: str
    label: str
    body: str


def arena_state_path(topic: str) -> str:
    cleaned = _clean_topic(topic)
    return f"{cleaned}/{_KNOTICA_DIR}/{ARENA_STATE_FILENAME}"


def arena_history_path(topic: str) -> str:
    cleaned = _clean_topic(topic)
    return f"{cleaned}/{_KNOTICA_DIR}/{ARENA_HISTORY_FILENAME}"


def query_prompt_path(topic: str, *, prefer_override: bool = True) -> str:
    """Vault-relative path written on promote (topic override when preferred)."""
    cleaned = _clean_topic(topic)
    if prefer_override:
        return f"{cleaned}/{PROMPTS_DIR}/query.md"
    return f"{PROMPTS_DIR}/query.md"


def read_arena_state(store: VaultStore, topic: str) -> ArenaState | None:
    path = arena_state_path(topic)
    if not store.exists(path):
        return None
    try:
        return ArenaState.model_validate_json(store.read_text(path))
    except Exception:
        return None


def write_arena_state(
    store: VaultStore,
    vault_root: str | Path,
    state: ArenaState,
    *,
    title: str,
) -> ArenaState:
    """Persist arena state under the vault lock (one commit)."""
    touched = state.touch()
    path = arena_state_path(touched.topic)
    payload = touched.model_dump_json(indent=2) + "\n"
    with VaultTransaction(store, Path(vault_root), "arena", touched.topic, title) as txn:
        txn.write(path, payload)
    return touched


def read_arena_history(store: VaultStore, topic: str, *, limit: int = 20) -> list[dict[str, Any]]:
    path = arena_history_path(topic)
    if not store.exists(path):
        return []
    lines = [line for line in store.read_text(path).splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_arena_history(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    entry: dict[str, Any],
) -> None:
    """Append one JSONL history row (commit)."""
    path = arena_history_path(topic)
    existing = store.read_text(path) if store.exists(path) else ""
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with VaultTransaction(store, Path(vault_root), "arena", topic, "arena history") as txn:
        txn.write(path, existing + line + "\n")


def race_variants(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    variants: Sequence[VariantSpec],
    *,
    baseline_scalar: float,
    score: ScoreFn,
    candidate_branch: str | None = None,
    promote_on_win: bool = True,
) -> ArenaState:
    """Score variants, persist leaderboard, promote winner or mark reverted.

    Promotion writes the winning body to the topic ``query.md`` override when
    ``promote_on_win`` and the best scalar clears ``baseline_scalar``.
    """
    cleaned = _clean_topic(topic)
    root = Path(vault_root)
    race_id = uuid.uuid4().hex[:12]
    board = [ArenaVariant(id=spec.id, label=spec.label, status="pending") for spec in variants]
    state = ArenaState(
        topic=cleaned,
        race_id=race_id,
        stage=ArenaStage.racing,
        baseline_scalar=float(baseline_scalar),
        variants=board,
        candidate_branch=candidate_branch,
        message="racing variants",
    )
    state = write_arena_state(store, root, state, title=f"arena race {race_id} start")

    bodies = {spec.id: spec.body for spec in variants}
    scored: list[ArenaVariant] = []
    for spec in variants:
        scalar = float(score(cleaned, root, spec.body))
        scored.append(ArenaVariant(id=spec.id, label=spec.label, scalar=scalar, status="scored"))
        state = state.model_copy(update={"variants": list(scored) + board[len(scored) :]})
        state = write_arena_state(
            store, root, state, title=f"arena race {race_id} scored {spec.id}"
        )

    if not scored:
        state = state.model_copy(
            update={
                "stage": ArenaStage.reverted,
                "message": "no variants to race",
                "variants": [],
            }
        )
        return write_arena_state(store, root, state, title=f"arena race {race_id} empty")

    best = max(scored, key=lambda row: float(row.scalar or 0.0))
    cleared = float(best.scalar or 0.0) >= float(baseline_scalar)
    final_variants: list[ArenaVariant] = []
    for row in scored:
        if cleared and row.id == best.id:
            final_variants.append(row.model_copy(update={"status": "winner"}))
        else:
            final_variants.append(row.model_copy(update={"status": "lost"}))

    if cleared and promote_on_win:
        state = state.model_copy(
            update={
                "stage": ArenaStage.promoting,
                "variants": final_variants,
                "winner_id": best.id,
                "winner_scalar": best.scalar,
                "message": f"promoting {best.id}",
            }
        )
        state = write_arena_state(store, root, state, title=f"arena race {race_id} promoting")
        _promote_prompt(store, root, cleaned, bodies[best.id])
        state = state.model_copy(
            update={
                "stage": ArenaStage.completed,
                "message": f"winner {best.id} cleared baseline",
            }
        )
    else:
        state = state.model_copy(
            update={
                "stage": ArenaStage.reverted,
                "variants": final_variants,
                "winner_id": None,
                "winner_scalar": best.scalar,
                "message": (
                    f"best {best.id}={best.scalar:.4f} did not clear baseline {baseline_scalar:.4f}"
                ),
            }
        )

    state = write_arena_state(store, root, state, title=f"arena race {race_id} done")
    append_arena_history(
        store,
        root,
        cleaned,
        {
            "race_id": race_id,
            "topic": cleaned,
            "stage": state.stage.value,
            "baseline_scalar": baseline_scalar,
            "winner_id": state.winner_id,
            "winner_scalar": state.winner_scalar,
            "variants": [row.model_dump(mode="json") for row in state.variants],
            "candidate_branch": candidate_branch,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    return state


def generate_variant_bodies(
    base_body: str,
    *,
    n: int = 4,
    mutator: Callable[[str, int], str] | None = None,
) -> list[VariantSpec]:
    """Build N variant specs from ``base_body``.

    Default mutator appends a small numbered instruction tweak (deterministic,
    no LLM) so tests and demos can race without network. Production passes an
    LLM-backed mutator.
    """
    mutate = mutator or _default_mutator
    return [
        VariantSpec(id=f"v{i + 1}", label=f"variant-{i + 1}", body=mutate(base_body, i))
        for i in range(max(1, n))
    ]


def load_base_query_body(store: VaultStore, topic: str) -> str:
    """Resolved ``query.md`` body for ``topic`` (override or root default)."""
    return resolve_prompt(store, "query", topic).body


def _promote_prompt(store: VaultStore, vault_root: Path, topic: str, body: str) -> None:
    path = query_prompt_path(topic, prefer_override=True)
    payload = body if body.endswith("\n") else body + "\n"
    with VaultTransaction(store, vault_root, "arena", topic, "arena promote query.md") as txn:
        txn.write(path, payload)


def _default_mutator(body: str, index: int) -> str:
    tweak = (
        f"\n\n## Arena variant tweak {index + 1}\n"
        "Prefer shorter answers and keep citation discipline strict.\n"
    )
    return body.rstrip() + tweak


def heuristic_arena_score(topic: str, root: Path, body: str) -> float:
    """Default arena scorer — no LLM; citation-preserving prompts score higher.

    Demo-safe and deterministic. Real eval-backed scoring can replace this later
    without changing the LoopRunner / dashboard contract.
    """
    del topic, root
    text = body.lower()
    score = 0.40
    if "citation discipline is mandatory" in text:
        score += 0.28
    if "prefer shorter answers" in text and "citation" in text:
        score += 0.06
    if "do not invent sources" in text or "cite" in text:
        score += 0.04
    for index in range(1, 9):
        if f"arena variant tweak {index}" in text:
            score += 0.01 * index
            break
    if "citation discipline is mandatory" not in text and "citations" not in text:
        score = min(score, 0.42)
    return min(0.99, score)


def _clean_topic(topic: str) -> str:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"topic must be a single path segment, got {topic!r}")
    return cleaned
