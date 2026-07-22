"""Unit tests for ``resolve_loop_cadence_config``'s ``[loop]`` cadence resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.errors import KnoticaError
from knotica.core.loop_cadence_config import LoopCadenceConfig, resolve_loop_cadence_config


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    return config_path


def test_absent_file_returns_all_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "does-not-exist.toml"

    resolved = resolve_loop_cadence_config(config_path)

    assert resolved == LoopCadenceConfig()


def test_absent_loop_table_returns_all_defaults(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[gapfill]\nmax_gaps = 3\n")

    resolved = resolve_loop_cadence_config(config_path)

    assert resolved == LoopCadenceConfig()


def test_valid_interval_window_and_threads_resolve(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        '[loop]\neval_min_interval_hours = 24\neval_window = "01:00-05:00"\neval_num_threads = 2\n',
    )

    resolved = resolve_loop_cadence_config(config_path)

    assert resolved == LoopCadenceConfig(
        eval_min_interval_hours=24.0, eval_window="01:00-05:00", eval_num_threads=2
    )


def test_midnight_wrapped_window_parses_without_error(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, '[loop]\neval_window = "22:00-02:00"\n')

    resolved = resolve_loop_cadence_config(config_path)

    assert resolved.parsed_window() is not None


def test_malformed_window_raises_typed_error(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, '[loop]\neval_window = "not-a-window"\n')

    with pytest.raises(KnoticaError):
        resolve_loop_cadence_config(config_path)


def test_out_of_range_thread_count_raises_typed_error(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[loop]\neval_num_threads = 99\n")

    with pytest.raises(KnoticaError):
        resolve_loop_cadence_config(config_path)


def test_zero_thread_count_raises_typed_error(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[loop]\neval_num_threads = 0\n")

    with pytest.raises(KnoticaError):
        resolve_loop_cadence_config(config_path)
