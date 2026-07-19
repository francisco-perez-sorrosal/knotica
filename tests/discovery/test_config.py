"""Behavioral contract tests for ``[gapfill.search]`` config resolution.

Mirrors ``core/config.py``'s side-effect-free, per-call resolution discipline
(no module-level cache, no network at parse). Four behavior groups are under
test: parsing ``[gapfill.search]`` from a real TOML file; the absent-section
default (disabled, not an error); env-key override precedence for API keys;
and side-effect-freedom (parsing never touches the network or constructs an
HTTP client).

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.config`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.
"""

from pathlib import Path

import pytest


def _config_module():
    import knotica.discovery.config as config

    return config


def _errors_module():
    import knotica.core.errors as errors

    return errors


def _write_config(tmp_path: Path, body: str) -> Path:
    config_file = tmp_path / "config.toml"
    config_file.write_text(body, encoding="utf-8")
    return config_file


@pytest.fixture(autouse=True)
def _scrub_search_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a clean environment -- a real key exported on the
    dev machine must never leak into these tests, and a test that sets one
    must not bleed into the next."""
    monkeypatch.delenv("KNOTICA_EXA_API_KEY", raising=False)
    monkeypatch.delenv("KNOTICA_YOUCOM_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Parsing [gapfill.search]
# ---------------------------------------------------------------------------


def test_parses_a_single_provider_from_the_gapfill_search_section(tmp_path: Path):
    config = _config_module()
    config_file = _write_config(
        tmp_path,
        '[gapfill.search]\nprovider = "exa"\n',
    )

    resolved = config.resolve_search_config(config_path=config_file)

    assert resolved.providers == ("exa",)


def test_parses_a_provider_fallback_chain_as_an_ordered_list(tmp_path: Path):
    config = _config_module()
    config_file = _write_config(
        tmp_path,
        '[gapfill.search]\nprovider = ["exa", "youcom"]\n',
    )

    resolved = config.resolve_search_config(config_path=config_file)

    assert resolved.providers == ("exa", "youcom")


# ---------------------------------------------------------------------------
# Absent section -> the packaged default provider chain, never an error
# ---------------------------------------------------------------------------


def test_a_config_file_with_no_gapfill_search_section_falls_back_to_the_default_provider(
    tmp_path: Path,
):
    """An absent section must not raise -- gap-fill discovery is best-effort, so a
    vault with no [gapfill.search] table is a normal, supported state that
    resolves to the packaged default provider chain rather than an error."""
    config = _config_module()
    config_file = _write_config(tmp_path, 'schema_version = 1\ndefault_vault = "main"\n')

    resolved = config.resolve_search_config(config_path=config_file)

    assert resolved.providers == (config.DEFAULT_PROVIDER,)


def test_a_completely_missing_config_file_falls_back_to_the_default_provider_not_an_error(
    tmp_path: Path,
):
    config = _config_module()
    missing_path = tmp_path / "does-not-exist.toml"

    resolved = config.resolve_search_config(config_path=missing_path)

    assert resolved.providers == (config.DEFAULT_PROVIDER,)


def test_a_present_gapfill_search_section_names_the_configured_provider_not_the_default(
    tmp_path: Path,
):
    config = _config_module()
    config_file = _write_config(tmp_path, '[gapfill.search]\nprovider = "exa"\n')

    resolved = config.resolve_search_config(config_path=config_file)

    assert resolved.providers == ("exa",)


# ---------------------------------------------------------------------------
# Env-key override precedence
# ---------------------------------------------------------------------------


def test_the_exa_api_key_is_read_from_its_env_var(monkeypatch: pytest.MonkeyPatch):
    config = _config_module()
    monkeypatch.setenv("KNOTICA_EXA_API_KEY", "sk-exa-test-key")

    api_key = config.resolve_api_key("exa")

    assert api_key == "sk-exa-test-key"


def test_the_youcom_api_key_is_read_from_its_own_distinct_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _config_module()
    monkeypatch.setenv("KNOTICA_YOUCOM_API_KEY", "sk-youcom-test-key")

    api_key = config.resolve_api_key("youcom")

    assert api_key == "sk-youcom-test-key"


def test_api_keys_are_never_read_from_the_toml_file_even_if_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Keys are env-only by contract (AC7) -- a key accidentally committed to
    config.toml must never be picked up, or a leaked file becomes a leaked
    credential."""
    config = _config_module()
    _write_config(
        tmp_path,
        '[gapfill.search]\nprovider = "exa"\napi_key = "sk-exa-should-be-ignored"\n',
    )
    monkeypatch.setenv("KNOTICA_EXA_API_KEY", "sk-exa-env-wins")

    api_key = config.resolve_api_key("exa")

    assert api_key == "sk-exa-env-wins"


# ---------------------------------------------------------------------------
# Missing key -> typed NOT_CONFIGURED, before any network/client construction
# ---------------------------------------------------------------------------


def test_a_missing_required_api_key_raises_not_configured_naming_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _config_module()
    errors = _errors_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(errors.KnoticaError) as exc_info:
        config.resolve_api_key("exa")

    assert exc_info.value.code == errors.ErrorCode.NOT_CONFIGURED
    assert "KNOTICA_EXA_API_KEY" in exc_info.value.message


def test_the_not_configured_error_never_echoes_a_credential_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only the missing-key case is under test here (there is no credential to
    echo when the key is absent) -- this pins that the error message names the
    *variable*, never a value, so a future refactor can't accidentally start
    interpolating a resolved secret into the message. Hermetic against the
    developer's real environment AND real .env files (cwd + home isolated)."""
    config = _config_module()
    errors = _errors_module()
    monkeypatch.delenv("KNOTICA_YOUCOM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(errors.KnoticaError) as exc_info:
        config.resolve_api_key("youcom")

    assert "sk-" not in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# Side-effect-free at parse: no network, no client construction
# ---------------------------------------------------------------------------


def test_resolving_search_config_makes_no_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Parsing config must never touch the network -- simulate an environment
    where any socket construction would fail, and prove parsing survives it."""
    config = _config_module()
    config_file = _write_config(tmp_path, '[gapfill.search]\nprovider = "exa"\n')

    import socket

    def _forbidden_socket(*args, **kwargs):
        raise AssertionError("resolve_search_config must never open a socket")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)

    resolved = config.resolve_search_config(config_path=config_file)

    assert resolved.providers == ("exa",)


def test_resolving_search_config_is_side_effect_free_across_repeated_calls(tmp_path: Path):
    """Mirrors core/config.py's no-module-level-cache discipline: a config
    edited between two calls must be picked up immediately -- there is no
    stale, process-lifetime cache to invalidate."""
    config = _config_module()
    config_file = _write_config(tmp_path, '[gapfill.search]\nprovider = "exa"\n')

    first = config.resolve_search_config(config_path=config_file)
    config_file.write_text('[gapfill.search]\nprovider = ["exa", "youcom"]\n', encoding="utf-8")
    second = config.resolve_search_config(config_path=config_file)

    assert first.providers == ("exa",)
    assert second.providers == ("exa", "youcom")


def test_an_invalid_toml_file_falls_back_to_the_default_provider_rather_than_raising(
    tmp_path: Path,
):
    """Malformed TOML must degrade the same way an absent section does --
    gap-fill discovery is best-effort and must never crash a caller that only
    wanted to resolve the provider chain."""
    config = _config_module()
    config_file = _write_config(tmp_path, "this is not valid toml [[[")

    resolved = config.resolve_search_config(config_path=config_file)

    assert resolved.providers == (config.DEFAULT_PROVIDER,)


def test_api_key_falls_back_to_a_dotenv_file_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With the env var unset, the key is read from ./.env (KEY=VALUE lines,
    comments and quotes tolerated); the value itself never appears in errors."""
    config = _config_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# discovery credentials\nexport KNOTICA_YOUCOM_API_KEY='dotenv-youcom-key'\n",
        encoding="utf-8",
    )

    assert config.resolve_api_key("youcom", environ={}) == "dotenv-youcom-key"


def test_the_process_environment_wins_over_a_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _config_module()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KNOTICA_YOUCOM_API_KEY=dotenv-value\n", encoding="utf-8")

    resolved = config.resolve_api_key("youcom", environ={"KNOTICA_YOUCOM_API_KEY": "env-value"})

    assert resolved == "env-value"


def test_a_missing_key_still_raises_typed_when_no_dotenv_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The .env fallback must not weaken fail-before-network: no env var and no
    file anywhere in the fallback chain is still a typed NOT_CONFIGURED whose
    fix text now names the .env locations."""
    config = _config_module()
    errors = _errors_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(errors.KnoticaError) as excinfo:
        config.resolve_api_key("youcom", environ={})

    assert excinfo.value.code is errors.ErrorCode.NOT_CONFIGURED
    assert ".env" in (excinfo.value.fix or "")
