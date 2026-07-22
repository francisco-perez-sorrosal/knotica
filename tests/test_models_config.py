"""Unit tests for ``resolve_models_config``'s ``[models]`` snapshot resolver."""

from __future__ import annotations

from pathlib import Path

from knotica.core.models_config import (
    QUERY_SNAPSHOT,
    ModelsConfig,
    resolve_models_config,
)
from knotica.evals.config import JUDGE_SNAPSHOT, WORKER_SNAPSHOT


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    return config_path


def test_absent_file_returns_all_packaged_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "does-not-exist.toml"

    resolved = resolve_models_config(config_path)

    assert resolved == ModelsConfig()
    assert resolved.worker == WORKER_SNAPSHOT
    assert resolved.judge == JUDGE_SNAPSHOT
    assert resolved.query == QUERY_SNAPSHOT


def test_absent_models_table_returns_all_packaged_defaults(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[gapfill]\nmax_gaps = 3\n")

    resolved = resolve_models_config(config_path)

    assert resolved == ModelsConfig()


def test_partial_models_table_only_overrides_named_keys(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, '[models]\nworker = "claude-haiku-5"\n')

    resolved = resolve_models_config(config_path)

    assert resolved.worker == "claude-haiku-5"
    assert resolved.judge == JUDGE_SNAPSHOT
    assert resolved.query == QUERY_SNAPSHOT


def test_full_models_table_overrides_all_keys(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        '[models]\nworker = "worker-x"\njudge = "judge-x"\nquery = "query-x"\n',
    )

    resolved = resolve_models_config(config_path)

    assert resolved == ModelsConfig(worker="worker-x", judge="judge-x", query="query-x")


def test_malformed_key_type_falls_back_to_default(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[models]\nworker = 42\n")

    resolved = resolve_models_config(config_path)

    assert resolved.worker == WORKER_SNAPSHOT


def test_to_harness_base_maps_worker_and_judge_only() -> None:
    config = ModelsConfig(worker="worker-x", judge="judge-x", query="query-x")

    harness_base = config.to_harness_base()

    assert harness_base.worker_snapshot == "worker-x"
    assert harness_base.judge_snapshot == "judge-x"
    assert not hasattr(harness_base, "query")
