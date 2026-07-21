"""Characterization safety net for the loop's two duplicated "race variants,
then resolve" call sites (P-A consolidation, pre-extraction baseline).

``_heal_prompts_after_regression`` (``core/loop.py:626-682``, reached from
``observe_default`` after a default-branch regression) and
``_race_then_resolve`` (``core/loop.py:920-1005``, reached from ``poll_once``
after a candidate branch fails its gate) independently build variants via
``generate_variant_bodies``/``load_base_query_body``, call
``race_variants``, then branch on ``arena.stage == completed and winner_id is
not None`` -- near-identical bodies differing only in whether a
``candidate_branch`` is passed to ``race_variants`` and which state fields are
pre-set. This file drives BOTH entry points with an equivalent runner
configuration (same ``arena_n``, same deterministic score function, same
baseline) and asserts they reach the identical winner selection, the same
``ArenaStage`` transition, and the same promoted ``query.md`` body -- so the
coming ``_run_arena_and_resolve`` extraction (P-A Step 7) can be verified by
re-running this file unmodified and seeing it stay GREEN.

Derived from a direct read of ``core/loop.py`` and ``core/arena.py`` (the
shared ``race_variants`` outcome/promotion logic), not from any planned
extraction shape. Zero network: ``arena_score`` is a deterministic stub keyed
to which numbered "Arena variant tweak" the default mutator wrote into each
variant body, never a live LLM.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from knotica.core.arena import ArenaStage
from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.prompts import PROMPTS_DIR
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
CANDIDATE = "loop/c/wound"

#: A regression/gate-fail scalar and a fixed baseline shared by every
#: scenario below, so only the score function distinguishes win from no-win.
_BASELINE = 0.80
_REGRESSED_SCALAR = 0.50

#: Winner index 2 clears the baseline; every other index does not -- shared
#: by both "win" scenarios so the winner selection is directly comparable.
_WIN_SCORES = {1: 0.50, 2: 0.95, 3: 0.60, 4: 0.40}
#: No index clears the baseline -- best is index 2, but it still reverts.
_NO_WIN_SCORES = {1: 0.10, 2: 0.20, 3: 0.15, 4: 0.05}


def _scored_by_variant_tweak_index(scores: dict[int, float]) -> Callable[..., float]:
    """Deterministic, network-free score fn keyed to the default mutator's tweak marker."""

    def _score(topic: str, root: Path, body: str) -> float:
        del topic, root
        text = body.lower()
        for index, value in scores.items():
            if f"arena variant tweak {index}" in text:
                return value
        return 0.0

    return _score


def _fake_evaluate(scalar: float):
    """Plain pass/fail stub carrying no diagnostic manifest -- observation
    always falls straight through to the arena heal, never the gap redirect."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-arena-char-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-21T00:00:00Z",
            generation=1,
            harness_version="fake-arena-char",
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "observed-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def _open_prompt_candidate(vault: Path, body: str) -> None:
    """Land a plain (non-source) candidate branch -- the ``_race_then_resolve`` path."""
    vcs = VaultVcs(vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)
    if vcs.branch_exists(CANDIDATE):
        vcs.delete_branch(CANDIDATE, force=True)
    vcs.create_branch(CANDIDATE, default)
    vcs.checkout_branch(CANDIDATE)
    wound = vault / ".knotica" / "prompts" / "query.md"
    wound.parent.mkdir(parents=True, exist_ok=True)
    wound.write_text(body, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: wound query.md")
    vcs.checkout_branch(default)


def _query_override_body(store: LocalFSStore, topic: str) -> str | None:
    path = f"{topic}/{PROMPTS_DIR}/query.md"
    return store.read_text(path) if store.exists(path) else None


# ---------------------------------------------------------------------------
# _heal_prompts_after_regression (observe_default → default-branch regression)
# ---------------------------------------------------------------------------


def test_heal_after_regression_wins_promotes_the_clearing_variant_and_completes(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(_REGRESSED_SCALAR),
        arena_enabled=True,
        arena_score=_scored_by_variant_tweak_index(_WIN_SCORES),
    )
    runner.set_baseline(_BASELINE, harness_version="fake-arena-char")
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_
    from knotica.core.arena import read_arena_state

    arena = read_arena_state(store, TOPIC)
    assert arena is not None
    assert arena.stage == ArenaStage.completed
    assert arena.winner_id == "v2"
    assert arena.winner_scalar == 0.95
    promoted = _query_override_body(store, TOPIC)
    assert promoted is not None
    assert "arena variant tweak 2" in promoted.lower()


def test_heal_after_regression_no_winner_reverts_and_leaves_the_gate_failed(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(_REGRESSED_SCALAR),
        arena_enabled=True,
        arena_score=_scored_by_variant_tweak_index(_NO_WIN_SCORES),
    )
    runner.set_baseline(_BASELINE, harness_version="fake-arena-char")
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.fail
    from knotica.core.arena import read_arena_state

    arena = read_arena_state(store, TOPIC)
    assert arena is not None
    assert arena.stage == ArenaStage.reverted
    assert arena.winner_id is None
    assert _query_override_body(store, TOPIC) is None, (
        "a no-winner race must never write a query.md override"
    )


# ---------------------------------------------------------------------------
# _race_then_resolve (poll_once → a plain candidate branch fails its gate)
# ---------------------------------------------------------------------------


def test_race_then_resolve_wins_promotes_the_identical_variant_and_completes(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(_REGRESSED_SCALAR),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=_scored_by_variant_tweak_index(_WIN_SCORES),
    )
    runner.set_baseline(_BASELINE, harness_version="fake-arena-char")
    _open_prompt_candidate(template_vault, "# wounded query\n")

    result = runner.poll_once()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_
    from knotica.core.arena import read_arena_state

    arena = read_arena_state(store, TOPIC)
    assert arena is not None
    assert arena.stage == ArenaStage.completed
    assert arena.winner_id == "v2", (
        "the same deterministic scores must pick the same winner as the "
        "observe_default regression-heal path"
    )
    assert arena.winner_scalar == 0.95
    promoted = _query_override_body(store, TOPIC)
    assert promoted is not None
    assert "arena variant tweak 2" in promoted.lower()
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE), (
        "a healed candidate must still be dropped once the arena resolves it"
    )


def test_race_then_resolve_no_winner_reverts_and_discards_the_candidate(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(_REGRESSED_SCALAR),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=_scored_by_variant_tweak_index(_NO_WIN_SCORES),
    )
    runner.set_baseline(_BASELINE, harness_version="fake-arena-char")
    _open_prompt_candidate(template_vault, "# wounded query\n")

    result = runner.poll_once()

    assert result.acted is True
    assert result.decision is LoopDecision.fail
    from knotica.core.arena import read_arena_state

    arena = read_arena_state(store, TOPIC)
    assert arena is not None
    assert arena.stage == ArenaStage.reverted
    assert arena.winner_id is None
    assert _query_override_body(store, TOPIC) is None
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE), (
        "a candidate with no arena winner is still discarded, exactly like today's "
        "plain gate-fail-without-arena path"
    )
