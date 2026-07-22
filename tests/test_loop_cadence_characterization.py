"""Characterization tests pinning `observe_default()`'s CURRENT behavior.

These tests pin the success-path scheduling contract that stays byte-identical
once the `[loop]` cadence knobs are wired, and the fixed failure-path behavior
(td-011): a failed observation eval no longer advances the cursor, so the next
tick re-attempts the same content instead of silently skipping it.
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


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-cadence-char-"))
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
            harness_version="fake-char",
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


def _failing_evaluate(exc: Exception):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        raise exc

    return _evaluate


class _FakeClock:
    """Injectable ``now_fn``: starts at a fixed instant, advances on demand."""

    def __init__(self) -> None:
        self._now = datetime(2026, 7, 22, 0, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _commit_content_change(vault: Path, note: str) -> None:
    """Land a content commit on the default branch (an 'ingest' stand-in)."""
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "cadence-char-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def test_successful_observation_advances_cursor_to_merged_head(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
    )

    _commit_content_change(template_vault, "first observed content")
    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_

    state = read_loop_state(store, TOPIC)
    assert state is not None
    default = VaultVcs(template_vault).default_branch()
    # Today's contract: a successful eval advances the cursor past the
    # observed head — a repeated call on the (now-quiesced) branch is a
    # no-op, proven directly by the sibling repeated-observation test.
    assert state.cursors.get(default) is not None
    second_call = runner.observe_default()
    assert second_call.acted is False
    assert second_call.decision is LoopDecision.none


def test_loop_state_gained_additive_cadence_fields(template_vault: Path) -> None:
    """Pins the post-change `LoopState` shape: cadence-timing fields now exist additively.

    Superseded from the original pre-change characterization (which pinned the
    fields' absence) once Steps 5/6 landed `last_eval_started_at`/`pending_retry`
    as additive `LoopState` fields — this now documents the intended after-state:
    a successful eval sets `last_eval_started_at` and leaves `pending_retry=False`.
    """
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
    )
    _commit_content_change(template_vault, "content for shape check")
    runner.observe_default()

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.last_eval_started_at is not None
    assert state.pending_retry is False


def test_repeated_observation_on_unchanged_head_is_a_noop(template_vault: Path) -> None:
    """Today, calling `observe_default()` twice on the same head only acts once."""
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
    )
    _commit_content_change(template_vault, "content observed once")
    first = runner.observe_default()
    assert first.acted is True

    second = runner.observe_default()
    assert second.acted is False
    assert second.decision is LoopDecision.none


def test_failed_observation_eval_leaves_cursor_unadvanced_for_retry(template_vault: Path) -> None:
    """td-011 fix: a failed eval no longer consumes the cursor.

    The failure branch must not call ``mark_processed`` — the cursor stays
    put so a later tick still sees content-changed against this same head
    and re-attempts the eval, gated by the always-on failure-retry floor
    (independent of `eval_min_interval_hours`/`eval_window`).
    """
    store = LocalFSStore(template_vault)
    clock = _FakeClock()
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_failing_evaluate(RuntimeError("simulated eval failure")),
        arena_enabled=False,
        now_fn=clock,
    )

    default = VaultVcs(template_vault).default_branch()
    _commit_content_change(template_vault, "content that fails eval")
    head_before_eval = VaultVcs(template_vault).head_sha()

    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.fail
    assert "simulated eval failure" in result.message

    state = read_loop_state(store, TOPIC)
    assert state is not None
    # Fixed: the failure branch leaves the cursor unadvanced so a transient
    # failure does not permanently skip this content.
    assert state.cursors.get(default) != head_before_eval
    assert state.pending_retry is True

    # The failure-retry floor holds an immediate same-content retry (same tick
    # cadence, no time elapsed) rather than re-attempting it right away.
    immediate_retry = runner.observe_default()
    assert immediate_retry.acted is False
    assert immediate_retry.decision is LoopDecision.none
    assert "failure retry held" in immediate_retry.message

    # Once the floor elapses, the retry proceeds as before.
    clock.advance(seconds=61)
    retry = runner.observe_default()
    assert retry.acted is True
    assert retry.decision is LoopDecision.fail


def test_new_content_during_pending_retry_is_not_held_by_failure_floor(
    template_vault: Path,
) -> None:
    """A genuinely new content change is evaluated immediately, even while an
    unrelated prior failure's ``pending_retry``/floor window is still active.

    The failure-retry floor only holds a retry of the SAME head that failed —
    it must not block progress on different, newly-arrived content.
    """
    store = LocalFSStore(template_vault)
    clock = _FakeClock()
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_failing_evaluate(RuntimeError("simulated eval failure")),
        arena_enabled=False,
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content that fails eval")
    first = runner.observe_default()
    assert first.acted is True
    assert first.decision is LoopDecision.fail

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.pending_retry is True

    # No time elapses (still inside the 60s floor window), but new content
    # arrives — a different head than the one that failed.
    _commit_content_change(template_vault, "unrelated new content")
    second = runner.observe_default()
    assert second.acted is True
    assert second.decision is LoopDecision.fail
    assert "failure retry held" not in second.message
