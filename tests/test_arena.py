"""Arena race — promote winner that clears baseline; else revert."""

from __future__ import annotations

from pathlib import Path

from knotica.core.arena import (
    ScorerInfo,
    ArenaStage,
    VariantSpec,
    load_base_query_body,
    query_prompt_path,
    race_variants,
    read_arena_history,
    read_arena_state,
)
from knotica.store import LocalFSStore


#: These exercise what a race *does* -- promote, revert, write history. The
#: default heuristic is not rankable against a gate baseline, so a race with it
#: aborts before scoring and none of that behavior is reachable; declaring a
#: comparable scorer is these tests stating their own premise.
_COMPARABLE_SCORER = ScorerInfo(id="fake-arena", comparable_to_eval=True)


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
        scorer=_COMPARABLE_SCORER,
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
        scorer=_COMPARABLE_SCORER,
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


def test_race_records_what_each_variant_changed_when_given_the_base(
    template_vault: Path,
) -> None:
    """The change summary/diff are DERIVED from the bodies at race start --
    the only account of a variant that survives once the bodies are gone."""
    store = LocalFSStore(template_vault)
    topic = "agentic-systems"
    base = load_base_query_body(store, topic)
    variants = [
        VariantSpec(id="v1", label="tight", body=base + "\n## Tighter answers\nBe brief.\n"),
        VariantSpec(id="v2", label="cite", body=base + "\n## Cite harder\nAlways cite.\n"),
    ]

    def score(_topic: str, _root: Path, body: str) -> float:
        return 0.9 if "Cite harder" in body else 0.1

    state = race_variants(
        store,
        template_vault,
        topic,
        variants,
        baseline_scalar=0.5,
        score=score,
        scorer=_COMPARABLE_SCORER,
        base_body=base,
    )

    by_id = {variant.id: variant for variant in state.variants}
    assert by_id["v1"].change_summary is not None
    assert "## Tighter answers" in by_id["v1"].change_summary
    assert by_id["v1"].diff is not None
    assert "+Be brief." in by_id["v1"].diff
    assert "## Cite harder" in (by_id["v2"].change_summary or "")
    # Round-trips through the persisted state, so history stays interpretable.
    reread = read_arena_state(store, topic)
    assert reread is not None
    assert reread.variants[0].change_summary == by_id["v1"].change_summary


def test_race_without_a_base_body_leaves_the_change_fields_honestly_absent(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    topic = "agentic-systems"
    base = load_base_query_body(store, topic)
    variants = [VariantSpec(id="v1", label="a", body=base + "\n# a\n")]

    state = race_variants(
        store,
        template_vault,
        topic,
        variants,
        baseline_scalar=0.5,
        score=lambda _topic, _root, _body: 0.9,
        scorer=_COMPARABLE_SCORER,
    )

    assert state.variants[0].change_summary is None
    assert state.variants[0].diff is None
