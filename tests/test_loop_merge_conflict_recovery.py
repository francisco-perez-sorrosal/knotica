"""A conflicted merge must not leave the live vault mid-merge.

Hit on a real ingest. The candidate had been branched before the default branch
gained a metrics generation, so its eval wrote a *colliding* generation number
and the merge back conflicted on the loop's own bookkeeping:

    CONFLICT (add/add):  <topic>/.knotica/eval-runs/gen-3/manifest.json
    CONFLICT (content):  <topic>/.knotica/metrics.jsonl

The candidate's actual content — four pages and six source chunks — merged
cleanly. Only `metrics.jsonl` and the per-generation manifest collided.

What made it serious was not the conflict but the aftermath. ``git merge``
exits non-zero *and leaves the tree mid-merge*: ``MERGE_HEAD`` set, conflict
markers written into tracked files. The exception unwound out of the cycle and
the vault stayed that way — every later reader (Obsidian, an MCP tool, a human)
seeing a broken tree with nothing to explain it. ``heal_git_mutation_state``
would have cleared it, but only whenever some future span happened to run.

An unattended watcher must not be able to leave that behind, so the merge now
fails clean: abort, record the failed cycle, raise a typed error naming the
cause. These tests drive a genuine conflicting merge rather than a mocked
failure — the wreckage is the point, and a stubbed ``merge_branch`` would not
produce any.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop_state import LoopStage, empty_loop_state, read_loop_state
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
CONFLICTED = f"{TOPIC}/.knotica/metrics.jsonl"


class _Runner:
    """The handful of attributes ``_merge_or_leave_clean`` actually touches."""

    def __init__(self, vault: Path) -> None:
        self._root = vault
        self._store = LocalFSStore(vault)
        self._vcs = VaultVcs(vault)
        self._topic = TOPIC


def _commit(vault: Path, path: str, body: str, message: str) -> None:
    target = vault / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    run_git(vault, "add", "--", path)
    run_git(vault, "commit", "-m", message)


@pytest.fixture
def diverged(template_vault: Path) -> tuple[_Runner, str]:
    """A vault whose default branch and result branch both wrote the same file.

    Mirrors the real shape: two branches independently append a generation to
    ``metrics.jsonl``, so merging one into the other conflicts on content.
    """
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    _commit(template_vault, CONFLICTED, '{"generation": 2}\n', "seed metrics")

    base = vcs.head_sha()
    run_git(template_vault, "checkout", "-b", "loop/r/deadbeefcafe", base)
    _commit(
        template_vault,
        CONFLICTED,
        '{"generation": 2}\n{"gen": 3, "from": "candidate"}\n',
        "candidate gen 3",
    )

    run_git(template_vault, "checkout", default)
    _commit(
        template_vault,
        CONFLICTED,
        '{"generation": 2}\n{"gen": 3, "from": "default"}\n',
        "default gen 3",
    )

    return _Runner(template_vault), "loop/r/deadbeefcafe"


def test_a_conflicting_merge_leaves_the_vault_clean(diverged: tuple[_Runner, str]) -> None:
    """The load-bearing assertion: no MERGE_HEAD, no conflict markers, no wreckage."""
    from knotica.core.candidate_gate import _merge_or_leave_clean

    runner, result_branch = diverged
    state = empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.5})

    with pytest.raises(KnoticaError):
        _merge_or_leave_clean(runner, state, "loop/c/x", result_branch)

    assert runner._vcs.is_merge_in_progress() is False, "the vault must not be left mid-merge"
    assert runner._vcs.unmerged_paths() == [], "no path may be left conflicted"
    body = (runner._root / CONFLICTED).read_text(encoding="utf-8")
    assert "<<<<<<<" not in body, "conflict markers must never survive in a tracked file"
    # HEAD advances by exactly one commit -- the failed-cycle bookkeeping write.
    # What must NOT have happened is the merge itself landing: the conflicted
    # file still holds the default branch's version, not the candidate's.
    assert '"from": "default"' in body
    assert '"from": "candidate"' not in body, "the candidate's side must not have landed"


def test_the_error_names_the_conflicted_paths_and_the_way_out(
    diverged: tuple[_Runner, str],
) -> None:
    from knotica.core.candidate_gate import _merge_or_leave_clean

    runner, result_branch = diverged
    state = empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.5})

    with pytest.raises(KnoticaError) as caught:
        _merge_or_leave_clean(runner, state, "loop/c/x", result_branch)

    error = caught.value
    assert error.code is ErrorCode.GIT_ERROR
    assert CONFLICTED in error.message, "the operator must be told what actually collided"
    assert "still pending" in error.message, "and that nothing was lost"
    assert "Refresh the candidate" in (error.fix or "")


def test_the_failed_cycle_is_recorded_rather_than_left_mid_merge(
    diverged: tuple[_Runner, str],
) -> None:
    """`stage` stuck at `merging` is how the topic looked after the real failure."""
    from knotica.core.candidate_gate import _merge_or_leave_clean

    runner, result_branch = diverged
    state = empty_loop_state(TOPIC).model_copy(
        update={"baseline_scalar": 0.5, "stage": LoopStage.merging}
    )

    with pytest.raises(KnoticaError):
        _merge_or_leave_clean(runner, state, "loop/c/x", result_branch)

    recorded = read_loop_state(runner._store, TOPIC)
    assert recorded is not None
    assert recorded.stage is LoopStage.failed
    assert CONFLICTED in (recorded.last_error or "")


def test_a_clean_merge_is_untouched(template_vault: Path) -> None:
    """The recovery path must not perturb the ordinary case."""
    from knotica.core.candidate_gate import _merge_or_leave_clean

    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    _commit(template_vault, CONFLICTED, '{"generation": 2}\n', "seed metrics")
    base = vcs.head_sha()
    run_git(template_vault, "checkout", "-b", "loop/r/cleanmerge", base)
    _commit(template_vault, f"{TOPIC}/new-page.md", "# New\n", "candidate page")
    run_git(template_vault, "checkout", default)

    runner = _Runner(template_vault)
    state = empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.5})

    _merge_or_leave_clean(runner, state, "loop/c/x", "loop/r/cleanmerge")

    assert (template_vault / TOPIC / "new-page.md").exists(), "the merge must have landed"
    assert runner._vcs.is_merge_in_progress() is False
