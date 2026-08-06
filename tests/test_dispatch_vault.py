"""Behavioral tests for the ``vault`` dispatcher (config-level KB switching).

Tested at the payload-builder layer (the functions the dispatcher routes to
verbatim). Config side effects run against the redirected ``vault_config`` /
``unconfigured_env`` fixtures; credential env vars are controlled per test so
the headless report is deterministic regardless of the host's real environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core import config as config_mod
from knotica.core import config_write
from knotica.core.config import ConfigDiagnosis, ConfigState, ResolvedVault
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.llm import API_KEY_ENV_VAR, OAUTH_TOKEN_ENV_VAR
from knotica.mcp_server import tools_dispatch_vault as vd


def _clear_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OAUTH_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)


# ---------------------------------------------------------------------------
# list / status (integration through config.toml)
# ---------------------------------------------------------------------------


def test_list_reports_configured_vaults_and_default(vault_config: Path) -> None:
    payload = vd._list_payload()

    assert payload["default_vault"] == "main"
    assert [v["name"] for v in payload["vaults"]] == ["main"]
    assert payload["vaults"][0]["ready"] is True


def test_status_reports_ready_active_vault_and_clean_misconfig_in_lean_mode(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_creds(monkeypatch)

    payload = vd._status_payload()

    assert payload["config_state"] == "READY"
    assert payload["active_vault"]["name"] == "main"
    assert payload["active_vault"]["ready"] is True
    assert payload["headless"]["credential_mode"] == "none"
    assert payload["misconfig"] == [], "a ready lean host has nothing to flag"


def test_status_flags_an_unconfigured_host(
    unconfigured_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_creds(monkeypatch)

    payload = vd._status_payload()

    assert payload["config_state"] == ConfigState.UNCONFIGURED.value
    assert payload["active_vault"]["ready"] is False
    assert payload["misconfig"], "an unconfigured host must surface a misconfig message"


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------


def test_use_switches_the_active_vault(vault_config: Path, template_vault: Path) -> None:
    config_write.upsert_vault(
        config_mod.config_file_path(), "research", template_vault, make_default=False
    )

    payload = vd._use_payload("research")

    assert payload["active_vault"] == "research"
    assert payload["ready"] is True
    assert config_mod.list_vaults()["default_vault"] == "research"


def test_use_rejects_an_unknown_vault(vault_config: Path) -> None:
    with pytest.raises(KnoticaError) as exc:
        vd._use_payload("ghost")

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert config_mod.list_vaults()["default_vault"] == "main", "default must not move"


def test_use_requires_a_name(vault_config: Path) -> None:
    with pytest.raises(KnoticaError) as exc:
        vd._use_payload("   ")

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_registers_a_vault_without_switching(vault_config: Path, template_vault: Path) -> None:
    payload = vd._add_payload("research", str(template_vault), make_default=False)

    assert payload["name"] == "research"
    assert payload["made_default"] is False
    assert payload["ready"] is True
    catalog = config_mod.list_vaults()
    assert catalog["default_vault"] == "main"
    assert {v["name"] for v in catalog["vaults"]} == {"main", "research"}


def test_add_with_make_default_switches_active(vault_config: Path, template_vault: Path) -> None:
    vd._add_payload("research", str(template_vault), make_default=True)

    assert config_mod.list_vaults()["default_vault"] == "research"


def test_add_requires_a_name(vault_config: Path, template_vault: Path) -> None:
    with pytest.raises(KnoticaError):
        vd._add_payload("", str(template_vault), make_default=False)


def test_add_requires_a_path(vault_config: Path) -> None:
    with pytest.raises(KnoticaError):
        vd._add_payload("research", "   ", make_default=False)


def test_add_rejects_a_name_that_is_not_a_toml_bare_key(
    vault_config: Path, template_vault: Path
) -> None:
    """A space or a dot in the name corrupts config.toml at the write seam."""
    with pytest.raises(KnoticaError) as exc:
        vd._add_payload("my vault", str(template_vault), make_default=False)

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert "[A-Za-z0-9_-]+" in exc.value.message, "the error must name the allowed characters"
    catalog = config_mod.list_vaults()
    assert {v["name"] for v in catalog["vaults"]} == {"main"}, "the config must be untouched"


def test_add_rejects_a_dotted_name_that_would_register_a_phantom_vault(
    vault_config: Path, template_vault: Path
) -> None:
    with pytest.raises(KnoticaError) as exc:
        vd._add_payload("my.vault", str(template_vault), make_default=False)

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert {v["name"] for v in config_mod.list_vaults()["vaults"]} == {"main"}


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_scaffolds_and_registers_a_new_vault(vault_config: Path, tmp_path: Path) -> None:
    target = tmp_path / "new-vault"

    payload = vd._create_payload("scratch", str(target), "", make_default=False)

    assert payload["name"] == "scratch"
    assert payload["created"] is True
    assert payload["made_default"] is False
    assert payload["ready"] is True
    assert (target / "SCHEMA.md").is_file()
    assert (target / ".git").is_dir()
    catalog = config_mod.list_vaults()
    assert catalog["default_vault"] == "main"
    assert {v["name"] for v in catalog["vaults"]} == {"main", "scratch"}


def test_create_with_make_default_switches_active(vault_config: Path, tmp_path: Path) -> None:
    target = tmp_path / "new-vault"

    vd._create_payload("scratch", str(target), "", make_default=True)

    assert config_mod.list_vaults()["default_vault"] == "scratch"


def test_create_requires_a_name(vault_config: Path, tmp_path: Path) -> None:
    with pytest.raises(KnoticaError):
        vd._create_payload("", str(tmp_path / "new-vault"), "", make_default=False)


def test_create_requires_a_path(vault_config: Path) -> None:
    with pytest.raises(KnoticaError):
        vd._create_payload("scratch", "   ", "", make_default=False)


def test_create_rejects_an_invalid_name_before_scaffolding_anything(
    vault_config: Path, tmp_path: Path
) -> None:
    """The name check must precede the scaffold, or a rejected create still writes.

    ``create`` is the one action with a filesystem side effect ahead of the config
    write, so validating late would leave an orphan vault on disk that no config
    entry points at.
    """
    target = tmp_path / "new-vault"

    with pytest.raises(KnoticaError) as exc:
        vd._create_payload("my vault", str(target), "", make_default=False)

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    assert not target.exists(), "nothing may be scaffolded for a name that cannot be registered"


def test_create_rejects_clobbering_a_non_empty_non_vault_directory(
    vault_config: Path, tmp_path: Path
) -> None:
    target = tmp_path / "not-a-vault"
    target.mkdir()
    (target / "unrelated.txt").write_text("pre-existing content", encoding="utf-8")

    with pytest.raises(KnoticaError) as exc:
        vd._create_payload("scratch", str(target), "", make_default=False)

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
    catalog_names = {v["name"] for v in config_mod.list_vaults()["vaults"]}
    assert "scratch" not in catalog_names, "a failed scaffold must not register the vault"


def test_create_produces_a_bare_vault_without_the_demo_topic(
    vault_config: Path, tmp_path: Path
) -> None:
    target = tmp_path / "decision-making"

    vd._create_payload("decision-making", str(target), "choices", make_default=False)

    assert (target / "choices" / "SCHEMA.md").is_file(), "the requested topic is seeded"
    assert not (target / "agentic-systems").exists(), (
        "a dashboard-created KB must be bare — no packaged demo topic"
    )
    assert not (target / "sources" / "agentic-systems").exists()


# ---------------------------------------------------------------------------
# headless status + misconfig (pure units, env/deps controlled)
# ---------------------------------------------------------------------------


def test_headless_credential_mode_prefers_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OAUTH_TOKEN_ENV_VAR, "tok")
    monkeypatch.setenv(API_KEY_ENV_VAR, "key")

    assert vd._headless_status()["credential_mode"] == "oauth"


def test_headless_credential_mode_falls_back_to_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OAUTH_TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv(API_KEY_ENV_VAR, "key")

    assert vd._headless_status()["credential_mode"] == "api_key"


def test_headless_credential_mode_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_creds(monkeypatch)

    status = vd._headless_status()
    assert status["credential_mode"] == "none"
    assert status["ready"] is False


def _ready_diag() -> ConfigDiagnosis:
    return ConfigDiagnosis(
        state=ConfigState.READY,
        detail="ready",
        remediation="",
        vault=ResolvedVault(name="main", path=Path("/v")),
    )


def test_misconfig_empty_when_config_ready_and_headless_consistent() -> None:
    catalog = {"default_vault": "main", "vaults": [{"name": "main", "path": "/v", "ready": True}]}
    headless = {"credential_mode": "none", "deps_installed": False}

    assert vd._collect_misconfig(_ready_diag(), catalog, headless) == []


def test_misconfig_flags_credential_present_but_deps_missing() -> None:
    catalog = {"default_vault": "main", "vaults": [{"name": "main", "path": "/v", "ready": True}]}
    headless = {"credential_mode": "api_key", "deps_installed": False}

    issues = vd._collect_misconfig(_ready_diag(), catalog, headless)

    assert any("anthropic/dspy" in message for message in issues)


def test_misconfig_flags_a_not_ready_configured_vault() -> None:
    catalog = {
        "default_vault": "main",
        "vaults": [{"name": "broken", "path": "/nope", "ready": False, "detail": "no SCHEMA.md"}],
    }
    headless = {"credential_mode": "none", "deps_installed": False}

    issues = vd._collect_misconfig(_ready_diag(), catalog, headless)

    assert any("broken" in message for message in issues)


def test_validate_action_rejects_an_unknown_action() -> None:
    with pytest.raises(KnoticaError) as exc:
        vd._validate_action("frobnicate")

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT
