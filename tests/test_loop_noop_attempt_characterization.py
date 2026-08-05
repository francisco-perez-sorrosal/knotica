"""What a repeated, identically-failing observation attempt costs the vault.

An observation eval that raises writes a *pair* of loop-state commits --
``observing …`` before the eval and ``observation eval error …`` after -- and
the failure path deliberately leaves the cursor unadvanced so the same content
is re-attempted once the retry floor elapses. Every re-attempt of a permanent
failure therefore paid another pair, forever: one live vault reached 14,845
commits of which roughly 14 were content.

These tests pin the *cost* of an attempt in commits, so the behavior change is
visible in the suite rather than asserted in a commit message. Measured green
against the pre-suppression code, the contract they first recorded was:

    first failure          -> 2 commits (evaluating + failed)
    identical re-attempt   -> 2 more commits, every time, unboundedly

They now pin the post-suppression contract:

    first failure          -> 2 commits (unchanged -- new content, new news)
    identical re-attempt   -> 0 commits
    re-attempt, new error  -> 1 commit (the verdict; the pair's first half is
                              itself a repeat and is skipped)
    re-attempt, recovered  -> recorded in full

The non-vacuity test is the load-bearing one -- suppression must be achieved by
not *writing*, never by not *attempting*, or a topic whose failure was transient
would never be noticed recovering.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.loop_retry_backoff import FAILURE_RETRY_FLOOR_SECONDS
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from support.vault import git_commit_count, git_commit_subjects, run_git

TOPIC = "agentic-systems"


class _FakeClock:
    """Injectable ``now_fn``: starts at a fixed instant, advances on demand."""

    def __init__(self) -> None:
        self._now = datetime(2026, 8, 4, 0, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class _CountingFailure:
    """An evaluate callable that always raises, and counts its invocations."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def __call__(self, topic: str, source_root: Path, ref: str | None):
        self.calls += 1
        raise self._exc


def _escalating_failure(first: Exception, later: Exception):
    """Raise ``first`` on the first attempt and ``later`` on every attempt after."""
    calls = {"n": 0}

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        calls["n"] += 1
        raise first if calls["n"] == 1 else later

    return _evaluate


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-noop-char-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        marker = clone.root / TOPIC / ".knotica" / "loop-eval-marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"scalar={scalar}\n", encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: record scalar {scalar}")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-08-04T00:00:00Z",
            generation=1,
            harness_version="fake-noop-char",
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


def _recovering_evaluate(exc: Exception, scalar: float):
    """Raise ``exc`` on the first attempt, then succeed with ``scalar``."""
    calls = {"n": 0}

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise exc
        return _fake_evaluate(scalar)(topic, source_root, ref)

    return _evaluate


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "noop-char-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def _runner(vault: Path, evaluate, clock: _FakeClock) -> LoopRunner:
    return LoopRunner(vault, TOPIC, evaluate=evaluate, arena_enabled=False, now_fn=clock)


def test_the_first_failing_observation_still_writes_its_full_pair(template_vault: Path) -> None:
    """An attempt on content nobody has evaluated keeps both halves, unchanged.

    Nothing on record said an eval was in flight against this head, so the
    ``observing …`` commit is genuinely new information -- and it is what a
    reader has to go on if the process dies mid-eval. This half of the contract
    is deliberately untouched: it is bounded by content changes, and content
    changes are exactly what the loop exists to measure.
    """
    clock = _FakeClock()
    runner = _runner(template_vault, _CountingFailure(RuntimeError("boom")), clock)
    _commit_content_change(template_vault, "content that fails eval")
    before = git_commit_count(template_vault)

    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.fail
    assert git_commit_count(template_vault) - before == 2
    assert any(
        "observation eval error" in subject for subject in git_commit_subjects(template_vault)
    )


def test_a_repeat_of_an_identical_failure_costs_no_commits(template_vault: Path) -> None:
    """The defect this fixes: each re-attempt used to write another pair.

    The retry floor bounds how *often* the re-attempt happens; it cannot make a
    re-attempt informative. Nothing about the vault has changed, the recorded
    verdict is still exactly true, so the attempt must cost nothing.
    """
    clock = _FakeClock()
    runner = _runner(template_vault, _CountingFailure(RuntimeError("boom")), clock)
    _commit_content_change(template_vault, "content that fails eval")
    runner.observe_default()
    after_first = git_commit_count(template_vault)

    clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    repeat = runner.observe_default()

    assert repeat.acted is True
    assert repeat.decision is LoopDecision.fail
    assert git_commit_count(template_vault) == after_first, (
        "a re-attempt that reaches the same verdict on the same content records "
        "nothing a reader of the history does not already know"
    )


def test_a_suppressed_repeat_still_runs_the_eval(template_vault: Path) -> None:
    """Non-vacuity: suppression must skip the *write*, never the attempt.

    If the zero-commit result above were achieved by declining to re-attempt,
    a topic whose failure was transient would never be noticed recovering.
    """
    clock = _FakeClock()
    evaluate = _CountingFailure(RuntimeError("boom"))
    runner = _runner(template_vault, evaluate, clock)
    _commit_content_change(template_vault, "content that fails eval")
    runner.observe_default()
    assert evaluate.calls == 1

    clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    runner.observe_default()

    assert evaluate.calls == 2, "the eval must still run; only its no-news record is suppressed"


def test_a_repeat_that_fails_differently_is_recorded(template_vault: Path) -> None:
    """The precision half: a *new* failure on the same content is news.

    Suppression keyed too broadly would hide this, which is the failure mode
    worth fearing -- a silently swallowed new error is a debugging catastrophe,
    a redundant commit is merely noise.
    """
    clock = _FakeClock()
    runner = _runner(
        template_vault,
        _escalating_failure(RuntimeError("boom"), RuntimeError("a different boom")),
        clock,
    )
    _commit_content_change(template_vault, "content that fails eval")
    runner.observe_default()
    after_first = git_commit_count(template_vault)

    clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    runner.observe_default()

    assert git_commit_count(template_vault) == after_first + 1, (
        "a different error is a different situation and must reach the history -- "
        "once, since the pair's `observing …` half is itself a repeat"
    )


def test_a_repeat_that_finally_succeeds_is_recorded(template_vault: Path) -> None:
    """Recovery is the most important thing an observation can report."""
    clock = _FakeClock()
    runner = _runner(template_vault, _recovering_evaluate(RuntimeError("boom"), 0.55), clock)
    _commit_content_change(template_vault, "content that fails then succeeds")
    runner.observe_default()
    after_first = git_commit_count(template_vault)

    clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    recovered = runner.observe_default()

    assert recovered.acted is True
    assert recovered.decision is LoopDecision.pass_
    assert git_commit_count(template_vault) > after_first


def test_a_permanent_failure_stops_growing_history_across_daemon_ticks(
    template_vault: Path,
) -> None:
    """The measured defect, reproduced the way production actually runs it.

    ``service.manager`` builds a **fresh** ``LoopRunner`` every supervision
    cycle, so nothing about an attempt can be remembered in process memory. Ten
    ticks of a permanently-failing topic used to cost twenty commits; the history
    must now stop growing after the failure is on record, while the eval keeps
    being attempted so recovery is still noticed.
    """
    clock = _FakeClock()
    evaluate = _CountingFailure(RuntimeError("boom"))
    _commit_content_change(template_vault, "content the eval can never score")
    _runner(template_vault, evaluate, clock).observe_default()
    after_first = git_commit_count(template_vault)

    for _ in range(10):
        clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
        _runner(template_vault, evaluate, clock).observe_default()

    assert evaluate.calls == 11, "every tick past the floor must still attempt the eval"
    assert git_commit_count(template_vault) == after_first, (
        "ten identical re-attempts across ten fresh runners must add nothing to history"
    )
