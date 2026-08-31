"""RISK-10 budget-gate fitness tests: ``wiki_status view="attention"`` at many-topic scale.

The design outline's own cost estimate for Home's cross-topic poll was "4-6 whole-vault
git calls + O(1) file reads per topic" -- and it is **wrong**.
Reading `core/status.py::_attention_status` (and its per-topic siblings) shows it makes
**zero** git subprocesses: every field it reports (pending suggestions,
refused-awaiting-rework, compile-readiness, runner liveness) is a small local file read
or an existence check. What actually scales with topic count is the *file-read fan-out*
-- one small batch of filesystem touches per topic, growing linearly. RISK-10 was
"unmeasured above 20 topics"; this file turns it into a checked, budget-gated property.

Unlike `test_status_attention_view.py`'s 3-vs-6-topic sibling suite (which asserts the
*shape* of the budget rules -- no lint walk, no anchor resolution, a constant git-call
count), this file measures the *scale* property at the sizes RISK-10 actually worried
about: 20, 50, and 100 topics. The three scales share one module-scoped fixture
(`attention_budget_measurements`) because building a 100-topic vault costs real wall
time -- each `create_topic` call opens its own `VaultTransaction` and git commit, so
scaffolding all three scales independently per test would triple that setup cost for no
additional signal.

Tests call `gather_wiki_status` directly, matching the sibling file's own reasoning:
these are internal call-graph and cost-budget assertions, not wire-contract tests.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from knotica.core.operations.create_topic import create_topic
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore

# Total topic counts measured, matching RISK-10's own "20+ topics" framing. The
# vault-template ships with exactly one topic (`agentic-systems`), so each scale
# scaffolds `count - 1` additional ones.
TOPIC_COUNTS = (20, 50, 100)

# How much slack a linear extrapolation from the smallest scale gets before a growth
# in filesystem touches (or wall-clock at the largest scale) is treated as a
# regression. Generous on purpose: the measured law is a near-exact `6*N + 2` (a
# constant term from the one whole-vault `last_lint` read, plus six per-topic
# touches), but pinning the exact constants would make this suite brittle to any
# incidental change in field count -- 1.5x catches a real superlinear blowup
# (e.g. an accidental O(N^2) pass) while tolerating implementation drift in the
# per-topic constant itself.
LINEAR_GROWTH_SLACK = 1.5

# An absolute wall-clock ceiling at the largest measured scale. Generous by roughly
# two orders of magnitude versus the measured ~30ms on a dev machine -- this is a
# budget gate against a real regression (e.g. a reintroduced git subprocess per
# topic), not a tight performance benchmark that would flake on a loaded CI runner.
WALL_CLOCK_BUDGET_SECONDS_AT_LARGEST_SCALE = 2.0


def _create_topic(store: LocalFSStore, vault: Path, topic: str) -> None:
    result = create_topic(store, vault, topic)
    assert "error" not in result, f"fixture setup failed to create topic {topic!r}: {result!r}"


@dataclass(frozen=True)
class AttentionBudgetMeasurement:
    """One `gather_wiki_status(view="attention")` call's measured cost at one scale."""

    topic_count: int
    git_subprocess_calls: list[str]
    filesystem_touches: int
    elapsed_seconds: float


def _measure_attention_view_at_scale(
    vault_seed: Path, tmp_path_factory: pytest.TempPathFactory, total_topics: int
) -> AttentionBudgetMeasurement:
    """Scaffold a vault with ``total_topics`` topics and measure one attention-view call.

    Spies on the two seams the corrected cost model actually touches: `VaultVcs._run`
    (must stay at zero -- the falsified part of the outline's estimate) and
    `pathlib.Path.read_text`/`Path.exists` (the real per-topic cost -- every status
    field, including `LocalFSStore`'s own reads and `read_runner_liveness`'s direct
    heartbeat read, ultimately resolves through one of these two `Path` methods).
    """
    vault = tmp_path_factory.mktemp(f"attention-budget-{total_topics}") / "vault"
    shutil.copytree(vault_seed, vault)
    store = LocalFSStore(vault)
    for index in range(total_topics - 1):  # the seed already ships with one topic
        _create_topic(store, vault, f"budget-topic-{index:04d}")

    git_calls: list[str] = []
    touch_count = 0
    original_vcs_run = VaultVcs._run
    original_read_text = Path.read_text
    original_exists = Path.exists

    def _spy_vcs_run(self: VaultVcs, arguments: Any, **kwargs: Any) -> Any:
        git_calls.append(arguments[0] if arguments else "")
        return original_vcs_run(self, arguments, **kwargs)

    def _spy_read_text(self: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal touch_count
        touch_count += 1
        return original_read_text(self, *args, **kwargs)

    def _spy_exists(self: Path, *args: Any, **kwargs: Any) -> Any:
        nonlocal touch_count
        touch_count += 1
        return original_exists(self, *args, **kwargs)

    VaultVcs._run = _spy_vcs_run  # type: ignore[method-assign]
    Path.read_text = _spy_read_text  # type: ignore[method-assign]
    Path.exists = _spy_exists  # type: ignore[method-assign]
    try:
        start = time.perf_counter()
        body = gather_wiki_status(store, vault, view="attention")
        elapsed = time.perf_counter() - start
    finally:
        VaultVcs._run = original_vcs_run  # type: ignore[method-assign]
        Path.read_text = original_read_text  # type: ignore[method-assign]
        Path.exists = original_exists  # type: ignore[method-assign]

    assert len(body["topics"]) == total_topics, (
        f"fixture setup produced {len(body['topics'])} topics, expected {total_topics}"
    )
    return AttentionBudgetMeasurement(
        topic_count=total_topics,
        git_subprocess_calls=git_calls,
        filesystem_touches=touch_count,
        elapsed_seconds=elapsed,
    )


@pytest.fixture(scope="module")
def attention_budget_measurements(
    vault_seed: Path, tmp_path_factory: pytest.TempPathFactory
) -> dict[int, AttentionBudgetMeasurement]:
    """One measured `attention` call per scale in `TOPIC_COUNTS`, built once per module.

    Module-scoped: scaffolding the 100-topic vault alone costs several seconds of real
    git-commit overhead (`create_topic` opens one `VaultTransaction` per call), so the
    three assertion-focused tests below share these measurements instead of each
    re-paying that setup cost.
    """
    return {
        count: _measure_attention_view_at_scale(vault_seed, tmp_path_factory, count)
        for count in TOPIC_COUNTS
    }


# ---------------------------------------------------------------------------
# Zero git subprocesses, at every measured scale
# ---------------------------------------------------------------------------


def test_attention_view_makes_zero_git_subprocess_calls_at_every_scale(
    attention_budget_measurements: dict[int, AttentionBudgetMeasurement],
) -> None:
    """Falsifies the outline's stale RISK-10 estimate ("4-6 whole-vault git calls").

    `_attention_status` makes no git subprocess at all, at any scale measured --
    every field is a small file read or existence check (dec-092)."""
    for count in TOPIC_COUNTS:
        calls = attention_budget_measurements[count].git_subprocess_calls
        assert calls == [], (
            f"view='attention' must spawn zero git subprocesses at {count} topics, "
            f"recorded: {calls!r}"
        )


def test_attention_view_git_subprocess_spy_would_catch_a_real_regression(
    template_vault: Path,
) -> None:
    """Non-vacuity for the assertion above: the same spy, on the same kind of vault,
    DOES record a git subprocess under `view="summary"` -- proving a regression that
    reintroduced a git call to `attention` would actually be caught, not silently
    passing because the spy never fires on this vault shape."""
    store = LocalFSStore(template_vault)
    git_calls: list[str] = []
    original = VaultVcs._run

    def _spy(self: VaultVcs, arguments: Any, **kwargs: Any) -> Any:
        git_calls.append(arguments[0] if arguments else "")
        return original(self, arguments, **kwargs)

    VaultVcs._run = _spy  # type: ignore[method-assign]
    try:
        gather_wiki_status(store, template_vault, view="summary")
    finally:
        VaultVcs._run = original  # type: ignore[method-assign]

    assert git_calls, (
        "sanity check failed: view='summary' is expected to spawn at least one git "
        "subprocess (e.g. a branch-tip rev-parse) on a freshly-seeded vault"
    )


# ---------------------------------------------------------------------------
# File-read fan-out: linear in topic count, not superlinear
# ---------------------------------------------------------------------------


def test_attention_view_file_read_fanout_grows_linearly_not_superlinearly(
    attention_budget_measurements: dict[int, AttentionBudgetMeasurement],
) -> None:
    """The corrected RISK-10 cost model: every topic adds a small, constant batch of
    filesystem touches (existence checks + small reads for suggestions, compile
    readiness, and runner liveness). Total touches must grow in proportion to topic
    count -- an accidental O(N^2) pass (e.g. re-scanning already-processed topics)
    would blow well past a generous linear extrapolation from the smallest scale."""
    baseline_count = TOPIC_COUNTS[0]
    baseline_touches = attention_budget_measurements[baseline_count].filesystem_touches
    assert baseline_touches > 0, (
        "sanity check failed: the filesystem spy recorded zero touches at the "
        f"smallest scale ({baseline_count} topics) -- the spy is not wired to the "
        "seam the implementation actually reads through"
    )

    previous_touches = baseline_touches
    for count in TOPIC_COUNTS[1:]:
        touches = attention_budget_measurements[count].filesystem_touches
        assert touches > previous_touches, (
            f"file-read fan-out must grow as topic count grows ({baseline_count} -> "
            f"{count} topics); got {previous_touches} -> {touches} touches (a flat "
            "count means the growth this test guards against could never trigger it)"
        )
        growth_ceiling = baseline_touches * (count / baseline_count) * LINEAR_GROWTH_SLACK
        assert touches <= growth_ceiling, (
            f"file-read fan-out at {count} topics ({touches} touches) exceeds the "
            f"linear-growth ceiling ({growth_ceiling:.0f}) extrapolated from "
            f"{baseline_count} topics ({baseline_touches} touches) -- a superlinear "
            "regression in the per-topic cost"
        )
        previous_touches = touches


# ---------------------------------------------------------------------------
# Wall-clock stays within an absolute budget at the largest measured scale
# ---------------------------------------------------------------------------


def test_attention_view_stays_within_the_wall_clock_budget_at_the_largest_scale(
    attention_budget_measurements: dict[int, AttentionBudgetMeasurement],
) -> None:
    """A budget gate, not a performance benchmark: at the largest scale RISK-10 named
    (100 topics), the call must complete well inside Home's human-paced poll cadence.
    The ceiling is generous specifically because wall-clock is noisy at millisecond
    scale -- the file-read-fanout test above is the precise complexity-regression
    guard; this test only catches a gross regression (e.g. a reintroduced
    per-topic git subprocess) that the count-based test might not by itself."""
    largest = max(TOPIC_COUNTS)
    elapsed = attention_budget_measurements[largest].elapsed_seconds
    assert elapsed < WALL_CLOCK_BUDGET_SECONDS_AT_LARGEST_SCALE, (
        f"view='attention' took {elapsed:.3f}s at {largest} topics, exceeding the "
        f"{WALL_CLOCK_BUDGET_SECONDS_AT_LARGEST_SCALE}s budget -- Home's cross-topic "
        "poll must stay responsive even at large vault sizes"
    )
