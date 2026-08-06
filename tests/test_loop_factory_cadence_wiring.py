"""Knobs that are inert at their raw default must be resolved in the factory.

`eval_min_interval_hours` was parsed, validated, unit-tested, and editable
through the `loop action=cadence` MCP tool -- and then reached nothing. Both
real construction sites (the `knotica loop` watcher and the service daemon)
called `build_loop_runner` without it, so every runner ran at the `0.0` eager
default. A vault with `eval_min_interval_hours = 1` was measured re-attempting
15 times in 20 minutes.

Defaulting at each call site is what failed, so the fix defaults in the one
shared factory instead: forgetting it is no longer expressible. These tests pin
that, and that an explicit argument still wins.

Two siblings shared the same shape and got the same treatment:

- `eval_window` comes from the same `[loop]` resolver and is fully implemented
  in `LoopRunner` (`_cadence_hold` / `_within_window`, midnight wrap included),
  yet no call site ever passed one.
- `arena_score` defaults to `None` while `arena_enabled` defaults to `True`,
  and every arena guard requires *both* -- so the bare signature read "arena on"
  and behaved "arena off". The service daemon and `loop action=run_eval` both
  built runners that way, so a regression there recorded "observation
  regression (arena disabled)" instead of healing.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from knotica.core.arena import heuristic_arena_score

# Imported via `knotica.core.loop`, not `loop_factory` directly: the two modules
# form a deliberate cycle the package resolves by re-exporting the factory from
# `loop`, and every real caller goes through that seam.
from knotica.core.loop import build_loop_runner


def _write_config(
    tmp_path: Path, vault: Path, *, interval: float, window: str | None = None
) -> Path:
    window_line = f'eval_window = "{window}"\n' if window is not None else ""
    config = tmp_path / "config.toml"
    config.write_text(
        "schema_version = 1\n"
        'default_vault = "main"\n\n'
        "[vaults.main]\n"
        f'path = "{vault}"\n\n'
        "[loop]\n"
        f"eval_min_interval_hours = {interval}\n" + window_line,
        encoding="utf-8",
    )
    return config


def test_the_factory_resolves_the_configured_cadence(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=2.5)
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(template_vault, "agentic-systems")

    assert runner._eval_min_interval_hours == 2.5, (
        "a caller that omits the cadence must inherit the configured value -- "
        "omission is what silently disabled the throttle in both real call sites"
    )


def test_an_explicit_cadence_argument_wins_over_config(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=2.5)
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(template_vault, "agentic-systems", eval_min_interval_hours=0.0)

    assert runner._eval_min_interval_hours == 0.0, (
        "an explicit value is a deliberate override (tests, one-shot runs) and "
        "must not be overwritten by config"
    )


def test_absent_config_falls_back_to_the_eager_defaults(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{template_vault}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(template_vault, "agentic-systems")

    assert runner._eval_min_interval_hours == 0.0, (
        "no [loop] table means no throttle configured; the eager default stands"
    )
    assert runner._eval_window is None, (
        "the factory reading the [loop] table must not invent a window when none "
        "is configured -- an all-defaults install schedules byte-identically"
    )


def test_the_factory_resolves_the_configured_eval_window(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=0.0, window="22:00-02:00")
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(template_vault, "agentic-systems")

    assert runner._eval_window == (time(22, 0), time(2, 0)), (
        "eval_window comes from the same [loop] resolver as the interval and the "
        "runner already honours it -- but no call site ever passed one, so the "
        "configured window held nothing back"
    )


def test_an_explicit_eval_window_argument_wins_over_config(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=0.0, window="22:00-02:00")
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(
        template_vault, "agentic-systems", eval_window=(time(9, 0), time(17, 0))
    )

    assert runner._eval_window == (time(9, 0), time(17, 0)), (
        "an explicit window is a deliberate override and must not be overwritten by config"
    )


def test_the_factory_defaults_the_arena_scorer_so_the_arena_can_actually_run(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=0.0)
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(template_vault, "agentic-systems")

    assert runner._arena_enabled is True
    assert runner._arena_score is heuristic_arena_score, (
        "every arena guard requires arena_enabled AND arena_score, so a bare "
        "runner with the default None scorer has the arena silently off -- which "
        "is how the daemon and `loop action=run_eval` recorded regressions as "
        "'arena disabled' instead of healing them"
    )


def test_disabling_the_arena_leaves_the_scorer_unset(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=0.0)
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    runner = build_loop_runner(template_vault, "agentic-systems", arena_enabled=False)

    assert runner._arena_score is None, (
        "`--no-arena` flips arena_enabled, and defaulting the scorer must not "
        "smuggle one back in behind that flag"
    )


def test_an_explicit_arena_scorer_wins_over_the_default(
    template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _write_config(tmp_path, template_vault, interval=0.0)
    monkeypatch.setenv("KNOTICA_CONFIG", str(config))

    def _stub_score(topic: str, root: Path, body: str) -> float:
        return 0.5

    runner = build_loop_runner(template_vault, "agentic-systems", arena_score=_stub_score)

    assert runner._arena_score is _stub_score, (
        "an explicit scorer is a deliberate override (tests pass stubs) and must "
        "not be replaced by the heuristic default"
    )
