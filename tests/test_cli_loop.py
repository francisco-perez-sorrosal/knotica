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
    assert "holds baseline" in out

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


def test_watch_mode_rebuilds_the_runner_each_tick(template_vault: Path, monkeypatch) -> None:
    """A long-lived watcher re-resolves config per tick (the service's own
    pattern), so a scorer switched mid-watch reaches the next tick."""
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
    assert builds["n"] >= 3, "watch mode must rebuild the runner on every tick"
