"""Cadence-knob scheduling behavior of the wired `LoopRunner`.

Byte-identical default-0 proof (diffed against the same fixture helpers used
by ``tests/test_loop_cadence_characterization.py``'s recorded default-path
scenario), the ``eval_min_interval_hours``/``eval_window`` throttle behavior,
and confirmation that the candidate-gate path (`poll_once`) never consults
cadence at all.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch


from knotica.core.loop import LoopDecision, LoopRunner
from knotica.core.vcs import VaultVcs
from support.vault import run_git
from test_loop_cadence_characterization import (
    TOPIC,
    _commit_content_change,
    _fake_evaluate,
)

CANDIDATE = "loop/c/cadence-gate"


class _FakeClock:
    """Injectable ``now_fn``: starts at a fixed instant, advances on demand."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 7, 22, 12, 0, 0)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, hours: float) -> None:
        self._now += timedelta(hours=hours)


def _second_vault(vault_seed: Path, tmp_path: Path, name: str) -> Path:
    """A second independent vault instance cloned from the same session seed.

    ``template_vault`` only yields one instance per test; the byte-identical
    proof needs two runners on two content-identical vaults to compare.
    """
    import shutil

    vault = tmp_path / name
    shutil.copytree(vault_seed, vault)
    return vault


def test_default_construction_matches_explicit_zero_none_scheduling(
    vault_seed: Path, tmp_path: Path
) -> None:
    """A `LoopRunner` built with no cadence kwargs schedules identically to one
    built with ``eval_min_interval_hours=0, eval_window=None`` passed explicitly.

    This is the true byte-identical claim: two runner *instances*, not one
    runner compared against a written-down expectation. Both drive the exact
    scenario recorded in ``test_loop_cadence_characterization.py`` (same
    helper functions, same content commit), so any divergence in acted/
    decision/message between the bare and explicit-default construction would
    catch a regression the characterization test alone cannot.
    """
    vault_bare = _second_vault(vault_seed, tmp_path, "vault-bare")
    vault_explicit = _second_vault(vault_seed, tmp_path, "vault-explicit")

    runner_bare = LoopRunner(
        vault_bare,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
    )
    runner_explicit = LoopRunner(
        vault_explicit,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
        eval_min_interval_hours=0,
        eval_window=None,
    )

    _commit_content_change(vault_bare, "byte-identical scenario content")
    _commit_content_change(vault_explicit, "byte-identical scenario content")

    result_bare = runner_bare.observe_default()
    result_explicit = runner_explicit.observe_default()

    assert result_bare.acted == result_explicit.acted is True
    assert result_bare.decision == result_explicit.decision is LoopDecision.pass_
    assert result_bare.message == result_explicit.message

    # Second call on each (now-quiesced) branch is a no-op either way.
    second_bare = runner_bare.observe_default()
    second_explicit = runner_explicit.observe_default()
    assert second_bare.acted == second_explicit.acted is False
    assert second_bare.decision == second_explicit.decision is LoopDecision.none
    assert second_bare.message == second_explicit.message


def test_min_interval_hours_defers_reobservation_until_elapsed(template_vault: Path) -> None:
    """A would-be-successful re-observation is held until the configured
    interval elapses, then proceeds.
    """
    clock = _FakeClock()
    # Distinct scalars per call: the fake evaluate stamps a marker file with the
    # scalar value, so a repeated identical scalar leaves nothing to commit on
    # the second clone (a fixture artifact, not cadence behavior under test).
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.60),
        arena_enabled=False,
        eval_min_interval_hours=2.0,
        now_fn=clock,
    )
    runner_first = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
        eval_min_interval_hours=2.0,
        now_fn=clock,
    )

    _commit_content_change(template_vault, "first interval content")
    first = runner_first.observe_default()
    assert first.acted is True
    assert first.decision is LoopDecision.pass_

    # New content arrives before the interval elapses — held.
    _commit_content_change(template_vault, "second interval content, too soon")
    clock.advance(hours=1.0)
    held = runner.observe_default()
    assert held.acted is False
    assert held.decision is LoopDecision.none
    assert "cadence held" in held.message
    assert "interval" in held.message

    # Once the interval fully elapses, the same pending content is evaluated.
    clock.advance(hours=1.5)
    proceeded = runner.observe_default()
    assert proceeded.acted is True
    assert proceeded.decision is LoopDecision.pass_


def test_eval_window_outside_range_defers_observation(template_vault: Path) -> None:
    """Content observed outside the configured window is deferred."""
    clock = _FakeClock(start=datetime(2026, 7, 22, 3, 0, 0))  # 03:00, outside 09-17
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
        eval_window=(time(9, 0), time(17, 0)),
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content outside window")
    held = runner.observe_default()

    assert held.acted is False
    assert held.decision is LoopDecision.none
    assert "cadence held" in held.message
    assert "window" in held.message


def test_eval_window_inside_range_allows_observation(template_vault: Path) -> None:
    """Content observed inside the configured window evaluates immediately."""
    clock = _FakeClock(start=datetime(2026, 7, 22, 12, 0, 0))  # noon, inside 09-17
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
        eval_window=(time(9, 0), time(17, 0)),
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content inside window")
    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_


def test_eval_window_midnight_wrap_allows_time_after_start_before_midnight(
    template_vault: Path,
) -> None:
    """A window that wraps midnight (start > end) admits times after start."""
    clock = _FakeClock(start=datetime(2026, 7, 22, 23, 0, 0))  # 23:00, inside 22:00-02:00
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
        eval_window=(time(22, 0), time(2, 0)),
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content inside midnight-wrap window")
    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_


def test_eval_window_midnight_wrap_defers_time_after_end_before_start(
    template_vault: Path,
) -> None:
    """The same wrapping window defers a time that falls in the excluded midday gap."""
    clock = _FakeClock(start=datetime(2026, 7, 22, 10, 0, 0))  # 10:00, outside 22:00-02:00
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.55),
        arena_enabled=False,
        eval_window=(time(22, 0), time(2, 0)),
        now_fn=clock,
    )

    _commit_content_change(template_vault, "content outside midnight-wrap window")
    held = runner.observe_default()

    assert held.acted is False
    assert held.decision is LoopDecision.none
    assert "cadence held" in held.message


def _open_candidate(vault: Path, body: str) -> None:
    vcs = VaultVcs(vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)
    if vcs.branch_exists(CANDIDATE):
        vcs.delete_branch(CANDIDATE, force=True)
    vcs.create_branch(CANDIDATE, default)
    vcs.checkout_branch(CANDIDATE)
    prompt = vault / ".knotica" / "prompts" / "query.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(body, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: candidate prompt edit")
    vcs.checkout_branch(default)


def test_candidate_gate_poll_once_never_consults_cadence_hold(template_vault: Path) -> None:
    """`poll_once`'s candidate-gate path stays eager regardless of cadence config.

    Cadence knobs are configured to a value that WOULD hold an ``observe_default``
    call (a huge interval, and a window that excludes ``now``) — proving the
    gate path's eagerness is not an accident of favorable timing but genuine
    non-consultation of ``_cadence_hold``.
    """
    clock = _FakeClock(start=datetime(2026, 7, 22, 3, 0, 0))  # outside 09-17 window
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.90),
        arena_enabled=False,
        branch_prefix="loop/c/",
        eval_min_interval_hours=1000.0,
        eval_window=(time(9, 0), time(17, 0)),
        now_fn=clock,
    )
    runner.set_baseline(0.50, harness_version="fake-cadence-gate")
    _open_candidate(template_vault, "# candidate prompt\n")

    with patch.object(LoopRunner, "_cadence_hold", wraps=runner._cadence_hold) as spy_cadence_hold:
        result = runner.poll_once()

    spy_cadence_hold.assert_not_called()
    assert result.acted is True
    assert result.decision is LoopDecision.pass_
