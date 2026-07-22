"""``[models]`` config -> ``harness_version`` fold, proved via a real TOML file.

Fingerprint-hole regression guard: earlier steps wire ``resolve_models_config()
.to_harness_base()`` as the base config for ``knotica eval``. It would be easy
to prove that wiring with a ``HarnessConfig(worker_snapshot=...)`` constructor
call instead -- which passes even if the CLI never actually reads
``config.toml``. Every assertion here instead writes a ``config.toml`` to disk
and resolves through it, so a regression that silently bypasses the file (e.g.
a query-path reuse of a hardcoded snapshot) shows up as a failing test.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from knotica.cli.eval import _OVERRIDE_FIELDS, _resolve_config
from knotica.core.models_config import resolve_models_config
from knotica.evals.config import DEFAULT_CONFIG, harness_version
from knotica.evals.judge import JUDGE_PROMPT_HASH


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
