"""``[models].query`` wiring into ``answer_question`` -- and its fingerprint isolation.

Completes the isolation invariant provisionally pinned in
``test_models_harness_fingerprint.py``: varying ``[models].query`` changes
``answer_question``'s resolved worker model while ``harness_version`` (computed
independently via ``resolve_models_config().to_harness_base()``) is provably
unchanged. Both files must agree that ``query`` never rotates the fingerprint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.config import CONFIG_PATH_ENV_VAR
from knotica.core.models_config import QUERY_SNAPSHOT, resolve_models_config
from knotica.core.query_engine import answer_question
from knotica.evals.config import DEFAULT_CONFIG
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    return config_path


class _CapturingRunner:
    """A stub runner returning a fixed answer without calling a real model."""

    def run(self, store: object, topic: str, question: str) -> object:
        from knotica.evals.llm import TokenUsage
        from knotica.evals.runner import Prediction

        return Prediction(
            answer="stub",
            citations=[],
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )


def _capture_select_runner(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch ``select_runner`` to record its ``worker_snapshot`` and return a stub."""
    captured: dict[str, object] = {}

    def _fake_select_runner(store, topic, *, llm_client=None, worker_snapshot="", cache=None):
        captured["worker_snapshot"] = worker_snapshot
        return _CapturingRunner()

    monkeypatch.setattr("knotica.core.query_engine.select_runner", _fake_select_runner)
    return captured


def test_models_query_config_changes_answer_question_resolved_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, template_vault: Path
) -> None:
    """A ``[models].query`` override reaches ``answer_question``'s worker resolution."""
    config_path = _write_config(tmp_path, '[models]\nquery = "custom-query-snapshot"\n')
    monkeypatch.setenv(CONFIG_PATH_ENV_VAR, str(config_path))
    captured = _capture_select_runner(monkeypatch)
    store = LocalFSStore(template_vault)

    answer_question(store, TOPIC, "What is agent workflow memory?")

    assert captured["worker_snapshot"] == "custom-query-snapshot"


def test_absent_models_query_config_defaults_to_query_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, template_vault: Path
) -> None:
    """No ``[models]`` table resolves the default ``QUERY_SNAPSHOT``."""
    config_path = _write_config(tmp_path, "[gapfill]\nmax_gaps = 3\n")
    monkeypatch.setenv(CONFIG_PATH_ENV_VAR, str(config_path))
    captured = _capture_select_runner(monkeypatch)
    store = LocalFSStore(template_vault)

    answer_question(store, TOPIC, "What is agent workflow memory?")

    assert captured["worker_snapshot"] == QUERY_SNAPSHOT


def test_explicit_worker_snapshot_argument_overrides_models_query_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, template_vault: Path
) -> None:
    """An explicit ``worker_snapshot`` caller argument still wins over ``[models].query``."""
    config_path = _write_config(tmp_path, '[models]\nquery = "config-query-snapshot"\n')
    monkeypatch.setenv(CONFIG_PATH_ENV_VAR, str(config_path))
    captured = _capture_select_runner(monkeypatch)
    store = LocalFSStore(template_vault)

    answer_question(
        store, TOPIC, "What is agent workflow memory?", worker_snapshot="explicit-snapshot"
    )

    assert captured["worker_snapshot"] == "explicit-snapshot"


def test_varying_models_query_never_changes_harness_version(tmp_path: Path) -> None:
    """Varying ``[models].query`` alone leaves ``harness_version`` untouched.

    Computed independently of ``answer_question`` via
    ``resolve_models_config().to_harness_base()``, matching the eval harness's own
    fingerprinting wiring -- agrees with the isolation invariant pinned in
    ``test_models_harness_fingerprint.py``.
    """
    from knotica.evals.config import harness_version
    from knotica.evals.judge import JUDGE_PROMPT_HASH

    default_fingerprint = harness_version(JUDGE_PROMPT_HASH, DEFAULT_CONFIG)

    config_path = _write_config(tmp_path, '[models]\nquery = "another-query-snapshot"\n')
    base = resolve_models_config(config_path).to_harness_base()
    resolved_fingerprint = harness_version(JUDGE_PROMPT_HASH, base)

    assert resolved_fingerprint == default_fingerprint
