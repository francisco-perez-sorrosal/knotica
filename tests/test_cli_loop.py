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
