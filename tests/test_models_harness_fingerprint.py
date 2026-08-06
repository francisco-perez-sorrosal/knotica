"""``[models]`` config -> ``harness_version`` fold, proved via a real TOML file.

Fingerprint-hole regression guard: earlier steps wire ``resolve_models_config()
.to_harness_base()`` as the base config for ``knotica eval``. It would be easy
to prove that wiring with a ``HarnessConfig(worker_snapshot=...)`` constructor
call instead -- which passes even if the CLI never actually reads
``config.toml``. Every assertion here instead writes a ``config.toml`` to disk
and resolves through it, so a regression that silently bypasses the file (e.g.
a query-path reuse of a hardcoded snapshot) shows up as a failing test.

The second half of the file covers the *unattended* leg of the same wiring:
``knotica.core.loop.harness_evaluate``, the callable the watcher, the service
daemon, MCP ``run_once`` and the ingest candidate gate all evaluate through. It
once called ``run_eval`` with no ``config=`` at all, so those four paths scored
with the packaged snapshots while ``knotica eval`` scored with the operator's --
two ``harness_version`` values alternating on one topic, each switch tripping
the instrument-changed re-freeze. Same "config that parses but reaches no
runner" hole, one layer down; these tests drive the real function.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from knotica.cli.eval import _OVERRIDE_FIELDS, _resolve_config
from knotica.core.loop import harness_evaluate
from knotica.core.models_config import resolve_models_config
from knotica.evals.config import DEFAULT_CONFIG, harness_version
from knotica.evals.judge import JUDGE_PROMPT_HASH

TOPIC = "agentic-systems"


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    return config_path


def _fingerprint_via_config_path(config_path: Path) -> str:
    """Resolve ``[models]`` from the given TOML file and fingerprint the harness.

    This is the exact wiring ``knotica eval`` uses: read the file,
    fold ``worker``/``judge`` into a ``HarnessConfig``, fingerprint it.
    """
    base = resolve_models_config(config_path).to_harness_base()
    return harness_version(JUDGE_PROMPT_HASH, base)


def _bare_namespace(**overrides: object) -> argparse.Namespace:
    """An ``args`` namespace with every override field unset except the given ones."""
    values = dict.fromkeys(_OVERRIDE_FIELDS, None)
    values.update(overrides)
    return argparse.Namespace(**values)


def test_default_fingerprint_via_toml_matches_packaged_default(tmp_path: Path) -> None:
    """A config file with no ``[models]`` table fingerprints identically to the packaged default."""
    config_path = _write_config(tmp_path, "[gapfill]\nmax_gaps = 3\n")

    resolved = _fingerprint_via_config_path(config_path)

    assert resolved == harness_version(JUDGE_PROMPT_HASH, DEFAULT_CONFIG)


def test_worker_override_via_toml_changes_harness_version(tmp_path: Path) -> None:
    """A ``[models].worker`` value differing from the default rotates ``harness_version``."""
    default_fingerprint = harness_version(JUDGE_PROMPT_HASH, DEFAULT_CONFIG)
    config_path = _write_config(tmp_path, '[models]\nworker = "claude-haiku-5"\n')

    resolved = _fingerprint_via_config_path(config_path)

    assert resolved != default_fingerprint


def test_judge_override_via_toml_changes_harness_version(tmp_path: Path) -> None:
    """A ``[models].judge`` value differing from the default rotates ``harness_version``."""
    default_fingerprint = harness_version(JUDGE_PROMPT_HASH, DEFAULT_CONFIG)
    config_path = _write_config(tmp_path, '[models]\njudge = "claude-opus-5"\n')

    resolved = _fingerprint_via_config_path(config_path)

    assert resolved != default_fingerprint


def test_reverting_config_change_restores_original_fingerprint(tmp_path: Path) -> None:
    """No caching/staleness: reverting a ``[models]`` edit restores the original fingerprint."""
    default_fingerprint = harness_version(JUDGE_PROMPT_HASH, DEFAULT_CONFIG)
    config_path = _write_config(tmp_path, "")

    at_default = _fingerprint_via_config_path(config_path)
    assert at_default == default_fingerprint

    config_path.write_text('[models]\nworker = "claude-haiku-5"\n')
    after_override = _fingerprint_via_config_path(config_path)
    assert after_override != default_fingerprint

    config_path.write_text("")
    after_revert = _fingerprint_via_config_path(config_path)
    assert after_revert == default_fingerprint


def test_cli_worker_snapshot_flag_overrides_conflicting_toml_value(tmp_path: Path) -> None:
    """An explicit ``--worker-snapshot`` wins over a conflicting ``[models].worker`` config value.

    Precedence: ``resolve_models_config().to_harness_base()`` builds
    the base, then ``_resolve_config`` layers the CLI flags on top -- CLI wins.
    """
    config_path = _write_config(tmp_path, '[models]\nworker = "config-worker-x"\n')
    base = resolve_models_config(config_path).to_harness_base()
    assert base.worker_snapshot == "config-worker-x"

    args = _bare_namespace(worker_snapshot="cli-worker-y")
    resolved = _resolve_config(base, args)

    assert resolved.worker_snapshot == "cli-worker-y"
    assert resolved.worker_snapshot != "config-worker-x"

    cli_fingerprint = harness_version(JUDGE_PROMPT_HASH, resolved)
    config_only_fingerprint = harness_version(JUDGE_PROMPT_HASH, base)
    assert cli_fingerprint != config_only_fingerprint


def test_query_override_via_toml_has_no_effect_on_harness_version(tmp_path: Path) -> None:
    """``[models].query`` never folds into ``harness_version`` (provisional).

    ``query`` is excluded from ``ModelsConfig.to_harness_base`` by design --
    it drives conversational-routing surfaces, not the eval harness. Full
    wiring of the query-model knob into the rest of the system lands in a
    later step; this assertion only pins the isolation invariant at the
    ``harness_version`` boundary and is provisional until that step confirms
    no other harness-adjacent path picks up ``query``.
    """
    default_fingerprint = harness_version(JUDGE_PROMPT_HASH, DEFAULT_CONFIG)
    config_path = _write_config(tmp_path, '[models]\nquery = "claude-opus-5"\n')

    resolved = _fingerprint_via_config_path(config_path)

    assert resolved == default_fingerprint


def _capture_unattended_run_eval_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_body: str,
    **overrides: object,
) -> dict[str, Any]:
    """Drive the real ``harness_evaluate`` and return the ``run_eval`` call it made.

    Redirects the config through ``$KNOTICA_CONFIG`` (``config_file_path``
    resolves argument > env > default), so this reads a ``config.toml`` written
    here and never the developer's real ``~/.config/knotica/config.toml``.
    ``run_eval`` is stubbed to a capture: no clone, no model call, no network.

    Returns ``{"config": <HarnessConfig>, "kwargs": {...}}`` -- the base config
    ``harness_evaluate`` resolved, and everything else it forwarded (the
    ``**overrides`` among it).

    The stub carries ``run_eval``'s own ``config: HarnessConfig = DEFAULT_CONFIG``
    default rather than making it required, so a ``harness_evaluate`` that omits
    ``config=`` reproduces the real defect -- the packaged snapshots silently
    binding -- and fails these tests on the assertion that names that symptom,
    not on an arity ``TypeError`` that only says a keyword went missing.
    """
    config_path = _write_config(tmp_path, config_body)
    monkeypatch.setenv("KNOTICA_CONFIG", str(config_path))
    captured: dict[str, Any] = {}

    def _fake_run_eval(
        topic: str, *, config: Any = DEFAULT_CONFIG, **kwargs: Any
    ) -> SimpleNamespace:
        captured["config"] = config
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            record=SimpleNamespace(
                scalar=0.9,
                generation=1,
                harness_version="test-harness",
                corpus_ref="corpus-1",
            ),
            clone_root=tmp_path,
        )

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)

    harness_evaluate(TOPIC, tmp_path, None, **overrides)

    return captured


def test_models_worker_from_toml_reaches_the_unattended_evaluate_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[models].worker`` reaches ``harness_evaluate``, not just ``knotica eval``.

    The regression this guards: ``harness_evaluate`` calling ``run_eval`` with no
    ``config=``, so ``DEFAULT_CONFIG`` binds and every unattended eval -- watcher,
    daemon, ``run_once``, ingest gate -- scores on the packaged snapshots while
    the foreground CLI scores on the operator's.
    """
    captured = _capture_unattended_run_eval_call(
        tmp_path, monkeypatch, '[models]\nworker = "config-worker-x"\njudge = "config-judge-x"\n'
    )

    assert captured["config"].worker_snapshot == "config-worker-x"
    assert captured["config"].judge_snapshot == "config-judge-x"


def test_absent_models_table_leaves_the_unattended_fingerprint_at_the_packaged_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default install (no ``[models]`` table) fingerprints exactly as before.

    Load-bearing: a different fingerprint here would re-freeze every already-frozen
    baseline in the wild on the next tick. Resolving ``[models]`` must be a no-op
    when the table is absent.
    """
    captured = _capture_unattended_run_eval_call(tmp_path, monkeypatch, "[gapfill]\nmax_gaps = 3\n")

    assert harness_version(JUDGE_PROMPT_HASH, captured["config"]) == harness_version(
        JUDGE_PROMPT_HASH, DEFAULT_CONFIG
    )


def test_caller_supplied_snapshot_override_is_not_clobbered_by_the_models_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``worker_snapshot=`` still arrives as an override, alongside the base.

    The MCP ``run_eval`` billed path passes ``partial(harness_evaluate,
    worker_snapshot=..., judge_snapshot=...)``; ``run_eval`` layers ``**overrides``
    onto ``config`` via ``HarnessConfig.with_overrides``, so the caller's value
    wins. This pins the two inputs that composition needs -- the ``[models]`` base
    reaching ``config=`` and the caller's value staying in ``**overrides`` rather
    than being folded into (and shadowed by) the base.
    """
    captured = _capture_unattended_run_eval_call(
        tmp_path,
        monkeypatch,
        '[models]\nworker = "config-worker-x"\n',
        worker_snapshot="caller-worker-y",
    )

    assert captured["config"].worker_snapshot == "config-worker-x"
    assert captured["kwargs"]["worker_snapshot"] == "caller-worker-y"
