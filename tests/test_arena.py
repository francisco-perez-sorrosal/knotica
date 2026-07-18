"""Arena race — promote winner that clears baseline; else revert."""

from __future__ import annotations

from pathlib import Path

from knotica.core.arena import (
    ArenaStage,
    VariantSpec,
    load_base_query_body,
    query_prompt_path,
    race_variants,
    read_arena_history,
    read_arena_state,
)
from knotica.store import LocalFSStore


def test_race_promotes_winner_and_writes_override(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    topic = "agentic-systems"
    base = load_base_query_body(store, topic)
    variants = [
        VariantSpec(id="v1", label="weak", body=base + "\n# weak\n"),
        VariantSpec(id="v2", label="strong", body=base + "\n# strong\n"),
    ]

    def score(_topic: str, _root: Path, body: str) -> float:
        return 0.9 if "# strong" in body else 0.1

    state = race_variants(
        store,
        template_vault,
        topic,
        variants,
        baseline_scalar=0.5,
        score=score,
    )
    assert state.stage == ArenaStage.completed
    assert state.winner_id == "v2"
    override = query_prompt_path(topic)
    assert store.exists(override)
    assert "# strong" in store.read_text(override)
    history = read_arena_history(store, topic, limit=5)
    assert history and history[-1]["winner_id"] == "v2"


def test_race_reverts_when_no_variant_clears_baseline(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    topic = "agentic-systems"
    base = load_base_query_body(store, topic)
    variants = [
        VariantSpec(id="v1", label="a", body=base + "\n# a\n"),
        VariantSpec(id="v2", label="b", body=base + "\n# b\n"),
    ]
    prior = (
        store.read_text(f"{topic}/.knotica/prompts/query.md")
        if store.exists(f"{topic}/.knotica/prompts/query.md")
        else None
    )

    state = race_variants(
        store,
        template_vault,
        topic,
        variants,
        baseline_scalar=0.99,
        score=lambda *_a: 0.2,
    )
    assert state.stage == ArenaStage.reverted
    assert state.winner_id is None
    # No promote — override either absent or unchanged from prior.
    path = query_prompt_path(topic)
    if prior is None:
        assert not store.exists(path) or "# a" not in store.read_text(path)
    else:
        assert store.read_text(path) == prior
    loaded = read_arena_state(store, topic)
    assert loaded is not None
    assert loaded.stage == ArenaStage.reverted
