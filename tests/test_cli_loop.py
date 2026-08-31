"""``knotica loop`` CLI: baseline freeze, one-tick observe, heartbeat liveness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from knotica.cli import main
from knotica.core.loop_heartbeat import (
    clear_heartbeat,
    read_runner_liveness,
    write_heartbeat,
)
from knotica.core.loop_state import read_loop_state
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def test_set_baseline_then_once_observes_and_gates(template_vault: Path, capsys) -> None:
    assert (
        main(
            [
                "loop",
                "--topic",
                TOPIC,
                "--vault",
                str(template_vault),
                "--set-baseline",
                "0.50",
            ]
        )
        == 0
    )

    exit_code = main(
        [
            "loop",
            "--topic",
            TOPIC,
            "--vault",
            str(template_vault),
            "--once",
            "--no-arena",
            "--fake-scalar",
            "0.60",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    # A manual freeze now records the *current* instrument rather than nulling
    # it, and the `--fake-scalar` seam reports a harness label of its own -- so
    # the observation legitimately reads as an instrument change and re-freezes,
    # which is the designed handling, not a gate failure.
    assert "baseline re-frozen" in out

    state = read_loop_state(LocalFSStore(template_vault), TOPIC)
    assert state is not None
    assert state.last_scalar == 0.60


def test_once_without_baseline_auto_freezes(template_vault: Path, capsys) -> None:
    exit_code = main(
        [
            "loop",
            "--topic",
            TOPIC,
            "--vault",
            str(template_vault),
            "--once",
            "--no-arena",
            "--fake-scalar",
            "0.44",
        ]
    )
    assert exit_code == 0
    assert "auto-froze baseline" in capsys.readouterr().out

    state = read_loop_state(LocalFSStore(template_vault), TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.44


def test_heartbeat_roundtrip_and_staleness(tmp_path: Path) -> None:
    dead = read_runner_liveness(tmp_path, TOPIC)
    assert dead["alive"] is False

    write_heartbeat(tmp_path, TOPIC, interval_seconds=2.0)
    live = read_runner_liveness(tmp_path, TOPIC)
    assert live["alive"] is True
    assert live["interval_seconds"] == 2.0
    assert live["pid"] is not None

    stale_moment = datetime.now(UTC) + timedelta(seconds=60)
    stale = read_runner_liveness(tmp_path, TOPIC, now=stale_moment)
    assert stale["alive"] is False

    clear_heartbeat(tmp_path, TOPIC)
    assert read_runner_liveness(tmp_path, TOPIC)["alive"] is False


def test_progress_roundtrip_and_staleness(tmp_path: Path) -> None:
    from knotica.core.loop_progress import clear_progress, read_progress, write_progress

    assert read_progress(tmp_path, TOPIC) is None

    write_progress(tmp_path, TOPIC, phase="evaluating", current=7, total=25, detail="q7?")
    progress = read_progress(tmp_path, TOPIC)
    assert progress is not None
    assert (progress["phase"], progress["current"], progress["total"]) == ("evaluating", 7, 25)

    clear_progress(tmp_path, TOPIC)
    assert read_progress(tmp_path, TOPIC) is None


def test_watcher_resolves_the_arena_scorer_from_config_not_a_hardcode(
    template_vault: Path, monkeypatch
) -> None:
    """The dashboard's scorer switch writes `[loop] arena_scorer`; the CLI
    watcher must let the factory resolve it. It once passed the heuristic
    explicitly, which outranks config -- so the switch could never reach it."""
    import argparse

    from knotica.core import arena_eval, loop_cadence_config
    from knotica.cli.loop import _build_runner

    sentinel_info = object()

    def fake_eval_scorer(store, topic, *, num_threads):  # noqa: ANN001, ANN202
        return (lambda _topic, _root, _body: 0.9), sentinel_info

    monkeypatch.setattr(arena_eval, "build_eval_scorer", fake_eval_scorer)
    monkeypatch.setattr(
        loop_cadence_config,
        "resolve_loop_cadence_config",
        lambda: loop_cadence_config.LoopCadenceConfig(
            eval_min_interval_hours=1.0,
            eval_window="7d",
            eval_num_threads=4,
            arena_scorer="eval",
        ),
    )

    args = argparse.Namespace(
        topic=TOPIC,
        eval_threads=None,
        fake_scalar=None,
        branch_prefix="loop/c",
        push=None,
        no_arena=False,
        arena_variants=None,
        once=True,
        observe_quiet=0.0,
    )
    runner = _build_runner(args, template_vault)

    assert runner._arena_scorer_info is sentinel_info, (
        "the watcher must reach the config-resolved eval scorer, not a hardcoded heuristic"
    )


def test_watch_mode_keeps_one_runner_while_the_loop_config_is_unchanged(
    template_vault: Path, monkeypatch
) -> None:
    """A steady-state watcher must not rebuild: the runner holds the observe
    debounce in memory, and rebuilding resets it every tick."""
    import knotica.cli.loop as loop_cli

    builds = {"n": 0}
    real_build = loop_cli._build_runner

    def counting_build(args, vault):  # noqa: ANN001, ANN202
        builds["n"] += 1
        return real_build(args, vault)

    ticks = {"n": 0}

    def fake_sleep(_seconds):  # noqa: ANN001, ANN202
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop_cli, "_build_runner", counting_build)
    monkeypatch.setattr(loop_cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(loop_cli, "_tick", lambda runner, observe: False)

    assert (
        main(
            [
                "loop",
                "--topic",
                TOPIC,
                "--vault",
                str(template_vault),
                "--no-observe",
                "--interval",
                "0",
            ]
        )
        == 0
    )
    assert builds["n"] == 1, "an unchanged [loop] table is no reason to rebuild"


def test_watch_mode_rebuilds_when_the_loop_config_changes(
    template_vault: Path, monkeypatch
) -> None:
    """The rebuild's actual purpose: a scorer switched from the dashboard
    reaches the very next tick without a restart."""
    import knotica.cli.loop as loop_cli
    from knotica.core.loop_cadence_config import LoopCadenceConfig

    builds = {"n": 0}
    real_build = loop_cli._build_runner

    def counting_build(args, vault):  # noqa: ANN001, ANN202
        builds["n"] += 1
        return real_build(args, vault)

    resolves = {"n": 0}

    def changing_config():  # noqa: ANN202
        resolves["n"] += 1
        return LoopCadenceConfig(eval_num_threads=1 + resolves["n"])

    ticks = {"n": 0}

    def fake_sleep(_seconds):  # noqa: ANN001, ANN202
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop_cli, "_build_runner", counting_build)
    monkeypatch.setattr(loop_cli, "resolve_loop_cadence_config", changing_config)
    monkeypatch.setattr(loop_cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(loop_cli, "_tick", lambda runner, observe: False)

    assert (
        main(
            [
                "loop",
                "--topic",
                TOPIC,
                "--vault",
                str(template_vault),
                "--no-observe",
                "--interval",
                "0",
            ]
        )
        == 0
    )
    # One build at startup, then one per tick that saw a changed table (the
    # first refresh only records the digest the startup build already used).
    assert builds["n"] >= 2, "a changed [loop] table must reach the next tick"


def test_a_transient_config_error_does_not_kill_the_watcher(
    template_vault: Path, monkeypatch, capsys
) -> None:
    """An unusable `[loop]` value must log and retry, not terminate an
    unattended watcher that was healthy a second ago."""
    import knotica.cli.loop as loop_cli
    from knotica.core.errors import ErrorCode, KnoticaError

    def broken_config():  # noqa: ANN202
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED, "[loop] eval_window is not valid", fix="fix it"
        )

    ticks = {"n": 0}

    def fake_sleep(_seconds):  # noqa: ANN001, ANN202
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop_cli, "resolve_loop_cadence_config", broken_config)
    monkeypatch.setattr(loop_cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(loop_cli, "_tick", lambda runner, observe: False)

    assert (
        main(
            [
                "loop",
                "--topic",
                TOPIC,
                "--vault",
                str(template_vault),
                "--no-observe",
                "--interval",
                "0",
            ]
        )
        == 0
    )
    assert ticks["n"] >= 3, "the watcher kept ticking on its already-built runner"
    assert "config unusable" in capsys.readouterr().err


def test_watch_mode_observes_once_the_quiet_window_elapses(
    template_vault: Path, monkeypatch
) -> None:
    """Three real ticks, a stable HEAD, a fake clock: the observe leg must
    actually fire.

    This is the assertion the rebuild-count test was standing in for. With the
    runner rebuilt per tick, `_pending_head` was `None` every time, so
    `observe_default` returned "observation settling" forever and the leg
    `loop --watch` exists to run never fired again at any interval.
    """
    import knotica.cli.loop as loop_cli

    now = {"t": 0.0}
    real_build = loop_cli._build_runner

    def clocked_build(args, vault):  # noqa: ANN001, ANN202
        runner = real_build(args, vault)
        runner._clock = lambda: now["t"]
        return runner

    ticks = {"n": 0}

    def fake_sleep(_seconds):  # noqa: ANN001, ANN202
        ticks["n"] += 1
        now["t"] += 60.0
        if ticks["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop_cli, "_build_runner", clocked_build)
    monkeypatch.setattr(loop_cli.time, "sleep", fake_sleep)

    assert (
        main(
            [
                "loop",
                "--topic",
                TOPIC,
                "--vault",
                str(template_vault),
                "--no-arena",
                "--fake-scalar",
                "0.61",
                "--interval",
                "0",
                "--observe-quiet",
                "1",
            ]
        )
        == 0
    )

    state = read_loop_state(LocalFSStore(template_vault), TOPIC)
    assert state is not None, "the watcher never observed"
    assert state.last_scalar == 0.61, "a later tick must proceed, not decline forever"
