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


def test_dump_config_toml_round_trips_a_nested_sub_table():
    """``[gapfill.search]`` is a shipped nested table -- it must survive a dump.

    A dict nested inside a top-level table is the shape ``discovery/config.py``
    reads (``SEARCH_CONFIG_SECTION = "gapfill.search"``). Rendering it as an
    inline JSON object produced a file that no longer parsed.
    """
    original = {
        "schema_version": 1,
        "vaults": {"main": {"path": "/data/main"}},
        "gapfill": {
            "enabled": True,
            "search": {"provider": "youcom", "mailto": "me@example.com"},
        },
    }

    rendered = config_write.dump_config_toml(original)

    assert tomllib.loads(rendered) == original


def test_dump_config_toml_emits_sub_table_headers_after_their_parents_scalars():
    """Scalars must precede sub-table headers, or TOML reparents them.

    ``enabled`` written *after* ``[gapfill.search]`` would silently land in the
    sub-table, so the round-trip above would still pass while the meaning moved.
    """
    rendered = config_write.dump_config_toml(
        {"gapfill": {"search": {"provider": "youcom"}, "enabled": True}}
    )

    assert rendered.index("enabled = true") < rendered.index("[gapfill.search]")
    assert tomllib.loads(rendered) == {
        "gapfill": {"enabled": True, "search": {"provider": "youcom"}}
    }


def test_dump_config_toml_round_trips_a_list_valued_sub_table_key():
    """The documented ``provider = ["youcom", "exa"]`` fallback chain is a list."""
    original = {"gapfill": {"search": {"provider": ["youcom", "exa"]}}}

    assert tomllib.loads(config_write.dump_config_toml(original)) == original


def test_upsert_vault_preserves_a_nested_gapfill_search_table(tmp_path: Path):
    """The end-to-end regression guard for silent whole-config destruction.

    Two ordinary ``vault action=add`` calls used to corrupt ``config.toml`` on the
    first write and then -- because ``read_config`` swallows the parse error and
    returns ``{}`` -- rebuild it from nothing on the second, deleting every vault,
    ``default_vault`` and sibling table with no error.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'schema_version = 1\ndefault_vault = "main"\n\n'
        '[vaults.main]\npath = "/data/main"\n\n'
        '[vaults.r]\npath = "/data/r"\n\n'
        "[loop]\neval_min_interval_hours = 1\n\n"
        '[models]\ndefault = "sonnet"\n\n'
        "[gapfill]\nenabled = true\n\n"
        '[gapfill.search]\nprovider = "youcom"\nmailto = "me@example.com"\n',
        encoding="utf-8",
    )

    config_write.upsert_vault(cfg, "second", "/data/second", make_default=False)
    config_write.upsert_vault(cfg, "third", "/data/third", make_default=False)

    data = _load(cfg)
    assert set(data["vaults"]) == {"main", "r", "second", "third"}
    assert data["default_vault"] == "main"
    assert data["loop"]["eval_min_interval_hours"] == 1
    assert data["models"]["default"] == "sonnet"
    assert data["gapfill"]["enabled"] is True
    assert data["gapfill"]["search"] == {"provider": "youcom", "mailto": "me@example.com"}


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
