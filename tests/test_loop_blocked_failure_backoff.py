"""A failure that cannot succeed on retry backs off far further than a transient one.

The re-arm design (``test_td011_eval_rearm``) deliberately keeps the cursor
unadvanced after a failed observation eval so the content is re-attempted. That
is right for a *transient* failure -- a 429, a flaky clone. Applied to a
**precondition** failure it degenerates: a topic with no frozen golden set can
never pass, so the loop re-attempted every minute forever, and every attempt
wrote two bookkeeping commits (``observing …`` then ``observation eval error
…``). One vault reached 14.8k commits, of which ~14 were content.

The signal was already there and simply unread: ``KnoticaError`` carries a
``retryable`` contract, and ``GoldenSetMissingError`` is ``NOT_CONFIGURED``,
which is not retryable. These tests pin that the loop now reads that contract
and backs off accordingly, while leaving genuinely transient failures on the
fast floor.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import LoopDecision, LoopRunner
from knotica.core.loop_retry_backoff import (
    BLOCKED_RETRY_FLOOR_SECONDS,
    FAILURE_RETRY_FLOOR_SECONDS,
)
from knotica.core.loop_state import read_loop_state
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 8, 2, 0, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _always_fails(exc: Exception):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        raise exc

    return _evaluate


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "backoff-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def _missing_golden_error() -> KnoticaError:
    # Shape-equivalent to evals.golden.GoldenSetMissingError without importing
    # the evals layer: NOT_CONFIGURED is non-retryable by the error contract.
    return KnoticaError(ErrorCode.NOT_CONFIGURED, "No golden set exists for topic")


def _runner(vault: Path, exc: Exception, clock: _FakeClock) -> LoopRunner:
    return LoopRunner(
        vault,
        TOPIC,
        evaluate=_always_fails(exc),
        arena_enabled=False,
        now_fn=clock,
    )


def test_a_non_retryable_failure_is_recorded_as_such(template_vault: Path) -> None:
    clock = _FakeClock()
    runner = _runner(template_vault, _missing_golden_error(), clock)
    _commit_content_change(template_vault, "content the eval cannot score")

    runner.observe_default()

    state = read_loop_state(LocalFSStore(template_vault), TOPIC)
    assert state is not None
    assert state.pending_retry is True, "the content is still unevaluated, so it stays pending"
    assert state.last_failure_retryable is False, (
        "the error reports itself non-retryable; the loop must record that rather "
        "than treat every failure as transient"
    )


def test_a_transient_failure_is_still_recorded_as_retryable(template_vault: Path) -> None:
    # Non-vacuity guard: the field must discriminate, not be constant False.
    clock = _FakeClock()
    runner = _runner(template_vault, RuntimeError("transient boom"), clock)
    _commit_content_change(template_vault, "content that fails transiently")

    runner.observe_default()

    state = read_loop_state(LocalFSStore(template_vault), TOPIC)
    assert state is not None
    assert state.last_failure_retryable is True, (
        "a bare Exception claims nothing about retryability, so the safe default "
        "(transient) must hold"
    )


def test_a_blocked_topic_is_still_held_long_after_the_transient_floor_elapses(
    template_vault: Path,
) -> None:
    """The regression this fixes: at 61s a blocked topic used to re-attempt, and
    would keep doing so every minute forever, two commits at a time."""
    clock = _FakeClock()
    runner = _runner(template_vault, _missing_golden_error(), clock)
    _commit_content_change(template_vault, "content the eval cannot score")
    runner.observe_default()

    clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    result = runner.observe_default()

    assert result.acted is False, (
        "a precondition failure cannot succeed until an operator acts, so passing "
        "the transient floor must not license another attempt"
    )
    assert "blocked retry held" in result.message


def test_a_transient_failure_still_retries_once_the_short_floor_elapses(
    template_vault: Path,
) -> None:
    """The backoff must not have slowed down ordinary transient recovery."""
    clock = _FakeClock()
    runner = _runner(template_vault, RuntimeError("transient boom"), clock)
    _commit_content_change(template_vault, "content that fails transiently")
    runner.observe_default()

    clock.advance(seconds=FAILURE_RETRY_FLOOR_SECONDS + 1)
    result = runner.observe_default()

    assert result.acted is True, (
        "a transient failure must still re-attempt on the fast floor -- the whole "
        "point of the re-arm design"
    )
    assert result.decision is LoopDecision.fail


def test_a_blocked_topic_re_attempts_once_the_blocked_floor_elapses(
    template_vault: Path,
) -> None:
    """The backoff is long, not infinite.

    The operator action that unblocks a topic -- freezing a golden set -- lands
    under ``<topic>/.knotica/``, which the content-change check deliberately
    ignores. If the block never expired, the loop would stay blocked after the
    fix landed and only a restart would clear it.
    """
    clock = _FakeClock()
    runner = _runner(template_vault, _missing_golden_error(), clock)
    _commit_content_change(template_vault, "content the eval cannot score")
    runner.observe_default()

    clock.advance(seconds=BLOCKED_RETRY_FLOOR_SECONDS + 1)
    result = runner.observe_default()

    assert result.acted is True, (
        "the blocked floor must expire so a topic unblocked by a bookkeeping-only "
        "write (a frozen golden set) recovers without a restart"
    )


def test_new_content_re_attempts_a_blocked_topic_immediately(template_vault: Path) -> None:
    """A block holds a *same-content* retry, never a genuine content change."""
    clock = _FakeClock()
    runner = _runner(template_vault, _missing_golden_error(), clock)
    _commit_content_change(template_vault, "first content")
    runner.observe_default()

    _commit_content_change(template_vault, "second, different content")
    result = runner.observe_default()

    assert result.acted is True, (
        "new content is a new question for the evaluator; the block on the prior "
        "content must not suppress it"
    )
