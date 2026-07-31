"""Unit tests for ``resolve_notes_config``'s ``[notes]`` threshold resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.notes_config import (
    DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    DEFAULT_GUESS_THRESHOLD,
    NotesConfig,
    resolve_notes_config,
)


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    return config_path


def test_absent_config_file_yields_default_thresholds(tmp_path: Path) -> None:
    config_path = tmp_path / "does-not-exist.toml"

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig()
    assert resolved.guess_threshold == 0.75
    assert resolved.complete_orphan_threshold == 0.35


def test_absent_notes_table_yields_default_thresholds(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[gapfill]\nmax_gaps = 3\n")

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig()


def test_explicit_valid_overrides_are_honoured(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, "[notes]\nguess_threshold = 0.9\ncomplete_orphan_threshold = 0.2\n"
    )

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig(guess_threshold=0.9, complete_orphan_threshold=0.2)


def test_overriding_one_key_leaves_the_other_at_default(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[notes]\nguess_threshold = 0.9\n")

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig(guess_threshold=0.9, complete_orphan_threshold=0.35)


def test_boundary_threshold_values_are_accepted(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, "[notes]\nguess_threshold = 1.0\ncomplete_orphan_threshold = 0.0\n"
    )

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig(guess_threshold=1.0, complete_orphan_threshold=0.0)


@pytest.mark.parametrize("key", ["guess_threshold", "complete_orphan_threshold"])
def test_non_float_threshold_raises_typed_configuration_error(tmp_path: Path, key: str) -> None:
    config_path = _write_config(tmp_path, f'[notes]\n{key} = "high"\n')

    with pytest.raises(KnoticaError) as excinfo:
        resolve_notes_config(config_path)

    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED
    assert key in excinfo.value.fix


@pytest.mark.parametrize("value", [-0.1, 1.5])
@pytest.mark.parametrize("key", ["guess_threshold", "complete_orphan_threshold"])
def test_out_of_range_threshold_raises_typed_configuration_error(
    tmp_path: Path, key: str, value: float
) -> None:
    config_path = _write_config(tmp_path, f"[notes]\n{key} = {value}\n")

    with pytest.raises(KnoticaError) as excinfo:
        resolve_notes_config(config_path)

    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED
    assert key in excinfo.value.fix


def test_malformed_toml_degrades_to_defaults(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, "[notes]\nguess_threshold = \n")

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig()


def test_inverted_threshold_pair_raises_typed_configuration_error(tmp_path: Path) -> None:
    """`complete_orphan_threshold` above `guess_threshold` inverts the band
    the resolution ladder is built around: the stricter rung-8 guard becomes
    unreachable once the looser rung-6 guard has already fired, so the
    `orphaned`-with-a-guess outcome silently stops occurring. This must be
    rejected as a configuration error, not silently accepted.
    """
    config_path = _write_config(
        tmp_path, "[notes]\nguess_threshold = 0.3\ncomplete_orphan_threshold = 0.5\n"
    )

    with pytest.raises(KnoticaError) as excinfo:
        resolve_notes_config(config_path)

    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED
    assert "guess_threshold" in excinfo.value.fix
    assert "complete_orphan_threshold" in excinfo.value.fix


def test_equal_threshold_pair_raises_typed_configuration_error(tmp_path: Path) -> None:
    """Equality is rejected too -- it empties the graded-recovery band just
    as completely as an inverted pair does, leaving rung 8 unreachable.
    """
    config_path = _write_config(
        tmp_path, "[notes]\nguess_threshold = 0.5\ncomplete_orphan_threshold = 0.5\n"
    )

    with pytest.raises(KnoticaError) as excinfo:
        resolve_notes_config(config_path)

    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED
    assert "guess_threshold" in excinfo.value.fix
    assert "complete_orphan_threshold" in excinfo.value.fix


def test_valid_ordered_threshold_pair_still_resolves(tmp_path: Path) -> None:
    """Regression: the new pair-coherence check must not reject a config
    whose `complete_orphan_threshold` is strictly below its `guess_threshold`
    -- a good config must keep resolving exactly as it did before.
    """
    config_path = _write_config(
        tmp_path, "[notes]\nguess_threshold = 0.6\ncomplete_orphan_threshold = 0.3\n"
    )

    resolved = resolve_notes_config(config_path)

    assert resolved == NotesConfig(guess_threshold=0.6, complete_orphan_threshold=0.3)


def test_module_exports_default_threshold_constants() -> None:
    assert DEFAULT_GUESS_THRESHOLD == 0.75
    assert DEFAULT_COMPLETE_ORPHAN_THRESHOLD == 0.35
    assert NotesConfig().guess_threshold == DEFAULT_GUESS_THRESHOLD
    assert NotesConfig().complete_orphan_threshold == DEFAULT_COMPLETE_ORPHAN_THRESHOLD
