"""Unit tests for the shared additive ``config.toml`` writer."""

import tomllib
from pathlib import Path

import pytest

from knotica.core import config_write
from knotica.core.errors import ErrorCode, KnoticaError


def _load(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_read_config_returns_empty_on_missing_file(tmp_path: Path):
    assert config_write.read_config(tmp_path / "absent.toml") == {}


def test_read_config_returns_empty_on_invalid_toml(tmp_path: Path):
    bad = tmp_path / "config.toml"
    bad.write_text("this is = = not valid toml", encoding="utf-8")
    assert config_write.read_config(bad) == {}


def test_upsert_vault_writes_a_default_entry_and_creates_parent_dir(tmp_path: Path):
    cfg = tmp_path / "nested" / "config.toml"  # parent intentionally absent

    config_write.upsert_vault(cfg, "main", tmp_path / "vault", make_default=True)

    data = _load(cfg)
    assert data["schema_version"] == 1
    assert data["default_vault"] == "main"
    assert data["vaults"]["main"]["path"] == str(tmp_path / "vault")


def test_upsert_vault_is_additive_preserving_other_vaults_and_tables(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'schema_version = 1\ndefault_vault = "main"\n\n'
        '[vaults.main]\npath = "/data/main"\n\n'
        "[loop]\neval_min_interval_hours = 1\n",
        encoding="utf-8",
    )

    config_write.upsert_vault(cfg, "research", "/data/research", make_default=False)

    data = _load(cfg)
    assert data["vaults"]["research"]["path"] == "/data/research"
    assert data["vaults"]["main"]["path"] == "/data/main", "pre-existing vault must survive"
    assert data["default_vault"] == "main", "make_default=False must not move the default"
    assert data["loop"]["eval_min_interval_hours"] == 1, "sibling [loop] table must round-trip"


def test_upsert_vault_make_default_moves_the_default_vault(tmp_path: Path):
    cfg = tmp_path / "config.toml"

    config_write.upsert_vault(cfg, "main", "/data/main", make_default=True)
    config_write.upsert_vault(cfg, "research", "/data/research", make_default=True)

    data = _load(cfg)
    assert data["default_vault"] == "research"
    assert set(data["vaults"]) == {"main", "research"}


def test_dump_config_toml_round_trips_scalars_vaults_and_tables():
    original = {
        "schema_version": 1,
        "default_vault": "main",
        "vaults": {"main": {"path": "/data/main"}, "r": {"path": "/data/r"}},
        "loop": {"eval_min_interval_hours": 1, "eval_window": "22:00-02:00"},
        "gapfill": {"enabled": True},
    }

    rendered = config_write.dump_config_toml(original)

    assert tomllib.loads(rendered) == original


def test_atomic_write_replaces_contents_and_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text("old", encoding="utf-8")

    config_write.atomic_write(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"], "no temp file may linger"


def test_set_default_vault_flips_to_a_configured_vault(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    config_write.upsert_vault(cfg, "main", "/data/main", make_default=True)
    config_write.upsert_vault(cfg, "research", "/data/research", make_default=False)

    config_write.set_default_vault(cfg, "research")

    data = _load(cfg)
    assert data["default_vault"] == "research"
    assert data["vaults"]["main"]["path"] == "/data/main", "sibling vault must survive"


def test_set_default_vault_rejects_an_unconfigured_vault(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    config_write.upsert_vault(cfg, "main", "/data/main", make_default=True)

    with pytest.raises(KnoticaError) as exc:
        config_write.set_default_vault(cfg, "ghost")

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert _load(cfg)["default_vault"] == "main", "a rejected switch must not change the config"
