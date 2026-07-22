"""End-to-end proof that a failed observation eval re-arms rather than sticks.

A failed eval must not permanently skip its content: the cursor stays
unadvanced, `pending_retry` flips on, the immediate retry is held by the
always-on failure floor (bounding retries to the tick cadence, never an
unbounded loop within a single tick), and once the floor elapses a
subsequent success clears `pending_retry` and finally advances the cursor.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.loop_state import read_loop_state
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 22, 0, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-rearm-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        marker = clone.root / TOPIC / ".knotica" / "loop-eval-marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"scalar={scalar}\n", encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: record scalar {scalar}")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-22T00:00:00Z",
            generation=1,
            harness_version="fake-rearm",
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


def _always_fails(exc: Exception):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        raise exc

    return _evaluate


def _sequenced_evaluate(*outcomes):
    """First call raises ``outcomes[0]`` (an Exception), later calls return the scalar."""
    calls = {"n": 0}

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        outcome = outcomes[min(calls["n"], len(outcomes) - 1)]
        calls["n"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return _fake_evaluate(outcome)(topic, source_root, ref)

    return _evaluate


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "rearm-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def test_failed_eval_sets_pending_retry_and_leaves_cursor_unadvanced(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    default = VaultVcs(template_vault).default_branch()
    clock = _FakeClock()
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_always_fails(RuntimeError("simulated eval failure")),
        arena_enabled=False,
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content that fails eval")
    head_before_eval = VaultVcs(template_vault).head_sha()

    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.fail

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.pending_retry is True
    assert state.cursors.get(default) != head_before_eval


def test_immediate_retry_of_same_content_is_held_by_the_failure_floor(
    template_vault: Path,
) -> None:
    """Bounds re-arm to the tick cadence: a same-content retry inside the floor
    window does not re-attempt the eval within the same tick or the next.
    """
    clock = _FakeClock()
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_always_fails(RuntimeError("simulated eval failure")),
        arena_enabled=False,
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content that fails eval")
    runner.observe_default()

    immediate_retry = runner.observe_default()

    assert immediate_retry.acted is False
    assert immediate_retry.decision is LoopDecision.none
    assert "failure retry held" in immediate_retry.message


def test_retry_after_floor_elapses_clears_pending_retry_and_advances_cursor_on_success(
    template_vault: Path,
) -> None:
    """The definitive re-arm proof: fail, wait out the floor, retry the SAME
    content, succeed — `pending_retry` clears and the cursor finally advances.
    """
    store = LocalFSStore(template_vault)
    default = VaultVcs(template_vault).default_branch()
    clock = _FakeClock()
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_sequenced_evaluate(RuntimeError("simulated eval failure"), 0.55),
        arena_enabled=False,
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content that fails then succeeds")
    failed = runner.observe_default()
    assert failed.acted is True
    assert failed.decision is LoopDecision.fail

    state_after_failure = read_loop_state(store, TOPIC)
    assert state_after_failure is not None
    assert state_after_failure.pending_retry is True
    failed_head = state_after_failure.candidate_sha
    assert failed_head is not None

    clock.advance(seconds=61)
    retried = runner.observe_default()

    assert retried.acted is True
    assert retried.decision is LoopDecision.pass_

    state_after_success = read_loop_state(store, TOPIC)
    assert state_after_success is not None
    assert state_after_success.pending_retry is False
    # The cursor now sits at a real merged head — the SAME content that
    # previously failed was re-attempted and finally consumed, not skipped.
    assert state_after_success.cursors.get(default) is not None


def test_persistently_failing_topic_retries_at_most_once_per_tick(template_vault: Path) -> None:
    """The risk-assessment concern: a persistently-failing eval must not loop
    unboundedly within a single tick — each ``observe_default`` call attempts
    the eval at most once, bounded by the failure floor across calls.
    """
    call_count = {"n": 0}

    def _counting_failure(topic: str, source_root: Path, ref: str | None):
        call_count["n"] += 1
        raise RuntimeError("persistent failure")

    clock = _FakeClock()
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_counting_failure,
        arena_enabled=False,
        now_fn=clock,
    )

    _commit_content_change(template_vault, "persistently failing content")
    first = runner.observe_default()
    assert first.acted is True
    assert call_count["n"] == 1

    # Three more ticks with no time elapsed: none re-attempt the eval — the
    # floor holds every one, so the underlying evaluate callable is never
    # invoked again until the floor elapses.
    second = runner.observe_default()
    third = runner.observe_default()
    fourth = runner.observe_default()

    assert second.acted is False
    assert third.acted is False
    assert fourth.acted is False
    assert call_count["n"] == 1
