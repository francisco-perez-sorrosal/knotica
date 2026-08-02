"""The `[loop]` cadence config must actually reach the runner that honours it.

`eval_min_interval_hours` was parsed, validated, unit-tested, and editable
through the `loop action=cadence` MCP tool -- and then reached nothing. Both
real construction sites (the `knotica loop` watcher and the service daemon)
called `build_loop_runner` without it, so every runner ran at the `0.0` eager
default. A vault with `eval_min_interval_hours = 1` was measured re-attempting
15 times in 20 minutes.

Defaulting at each call site is what failed, so the fix defaults in the one
shared factory instead: forgetting it is no longer expressible. These tests pin
that, and that an explicit argument still wins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Imported via `knotica.core.loop`, not `loop_factory` directly: the two modules
# form a deliberate cycle the package resolves by re-exporting the factory from
# `loop`, and every real caller goes through that seam.
from knotica.core.loop import build_loop_runner


def _write_config(tmp_path: Path, vault: Path, *, interval: float) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        "schema_version = 1\n"
        'default_vault = "main"\n\n'
        "[vaults.main]\n"
        f'path = "{vault}"\n\n'
        "[loop]\n"
        f"eval_min_interval_hours = {interval}\n",
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


def test_absent_config_falls_back_to_the_eager_default(
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
