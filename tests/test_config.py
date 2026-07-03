"""Behavioral contract tests for ``knotica.core.config``.

The configuration contract: ``config.toml`` (``schema_version`` +
``default_vault`` + named ``[vaults.<name>] path`` entries) is resolved
**fresh on every call** — writing the config after a failed resolution takes
effect with no restart and no reload hook. Three internal states (no usable
config / configured but the target is not an initialized vault / ready)
collapse to a single user-facing "not configured" contract with
state-specific remediation.

Expected interface:

- ``resolve(config_path=..., vault=None)`` — per-call fresh read with
  ``~``/``$ENV`` expansion at resolution time; the explicit config-path
  argument is the test override hook (param name probed: ``config_path`` /
  ``path`` / ``config_file``).
- Ready (the path is a git repo carrying a root ``SCHEMA.md`` with
  ``schema_version``) → returns the resolved vault root (a ``Path`` or an
  object exposing it; probed).
- Any non-ready state → raises ``KnoticaError`` with code ``NOT_CONFIGURED``
  carrying the state-specific remediation. (The "tools never raise" clause is
  the *adapter layer's* contract — the adapter catches this error and renders
  the error envelope.)
- A three-member state enum (UNCONFIGURED / CONFIGURED_NO_VAULT / READY) so
  diagnostics can report which state holds.

All tests are HOME-isolated (autouse fixture), zero-network, and pass the
config path explicitly. A local minimal vault stand-in is used because the
shared test spine lands concurrently — migrate ``_init_vault`` to the shared
vault fixture once the conftest exists.

Production imports are deferred into test bodies so collection succeeds even
while the module under test is still in flight.
"""

import inspect
import subprocess
from pathlib import Path

import pytest

from test_errors import assert_names_both_setup_paths

# ---------------------------------------------------------------------------
# Isolation: never read or write the real ~/.config/knotica/config.toml
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


# ---------------------------------------------------------------------------
# Interface probes + fixture helpers
# ---------------------------------------------------------------------------


def _config_module():
    import knotica.core.config as config

    return config


def _knotica_error():
    from knotica.core.errors import KnoticaError

    return KnoticaError


def _resolve(config_path: Path, vault: str | None = None):
    """Call resolve() with an explicit config path (the test override hook)."""
    config = _config_module()
    resolve = config.resolve
    kwargs: dict[str, object] = {} if vault is None else {"vault": vault}
    params = inspect.signature(resolve).parameters
    for name in ("config_path", "path", "config_file"):
        if name in params:
            return resolve(**{name: config_path}, **kwargs)
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return resolve(config_path=config_path, **kwargs)
    # Fall back to positional (resolve(config_path, vault=...)).
    return resolve(config_path, **kwargs)


def _vault_root_of(resolution) -> Path:
    """Extract the resolved vault root, whatever shape resolve() returns."""
    if isinstance(resolution, Path):
        return resolution
    for attr in ("path", "root", "vault_path", "vault_root"):
        value = getattr(resolution, attr, None)
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
    raise AssertionError(
        "could not extract the vault root from the resolve() result "
        f"({resolution!r}); expected a Path or an object exposing "
        "path/root/vault_path/vault_root"
    )


def _resolve_not_configured(config_path: Path, vault: str | None = None) -> str:
    """Resolve expecting the collapsed unconfigured contract; return the
    user-facing remediation text (message + fix) for specificity assertions."""
    with pytest.raises(_knotica_error()) as excinfo:
        _resolve(config_path, vault=vault)
    err = excinfo.value
    code_name = getattr(err.code, "name", err.code)
    assert code_name == "NOT_CONFIGURED", (
        f"every non-ready state collapses to the single NOT_CONFIGURED contract; got {err.code!r}"
    )
    return f"{err.message} {err.fix}"


def _init_vault(path: Path) -> Path:
    """Minimal ready vault: a git repo with a root SCHEMA.md carrying
    schema_version — the contract's definition of an initialized vault."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True, capture_output=True)
    (path / "SCHEMA.md").write_text("---\nschema_version: 1\n---\n\n# SCHEMA\n", encoding="utf-8")
    return path


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body, encoding="utf-8")
    return config_path


def _single_vault_config(tmp_path: Path, vault_path: str, name: str = "main") -> Path:
    return _write_config(
        tmp_path,
        f'schema_version = 1\ndefault_vault = "{name}"\n\n[vaults.{name}]\npath = "{vault_path}"\n',
    )


# ---------------------------------------------------------------------------
# No usable config → one collapsed contract
# ---------------------------------------------------------------------------


def test_missing_config_file_reports_not_configured_with_setup_remediation(
    tmp_path: Path,
):
    remediation = _resolve_not_configured(tmp_path / "config" / "config.toml")
    assert_names_both_setup_paths(remediation)


def test_malformed_toml_reports_not_configured_not_a_parser_crash(tmp_path: Path):
    config_path = _write_config(tmp_path, "default_vault = [unclosed\n")
    # pytest.raises(KnoticaError) inside: a raw TOMLDecodeError escaping
    # resolve() fails this test with its own traceback — that is the point.
    remediation = _resolve_not_configured(config_path)
    assert remediation.strip()


def test_missing_default_vault_key_reports_not_configured(tmp_path: Path):
    vault = _init_vault(tmp_path / "vault")
    config_path = _write_config(
        tmp_path,
        f'schema_version = 1\n\n[vaults.main]\npath = "{vault}"\n',
    )
    _resolve_not_configured(config_path)


def test_unknown_vault_name_reports_not_configured_naming_the_vault(tmp_path: Path):
    vault = _init_vault(tmp_path / "vault")
    config_path = _single_vault_config(tmp_path, str(vault))
    remediation = _resolve_not_configured(config_path, vault="papers")
    assert "papers" in remediation, (
        "the remediation must name the unknown vault so the caller can act"
    )


# ---------------------------------------------------------------------------
# Configured, but the target is not an initialized vault
# ---------------------------------------------------------------------------


def test_config_pointing_at_a_missing_path_reports_not_configured(tmp_path: Path):
    config_path = _single_vault_config(tmp_path, str(tmp_path / "nowhere"))
    _resolve_not_configured(config_path)


@pytest.mark.parametrize(
    "missing_ingredient",
    ["empty-dir", "git-repo-without-schema", "schema-without-git-repo"],
)
def test_a_directory_that_is_not_an_initialized_vault_reports_not_configured(
    tmp_path: Path, missing_ingredient: str
):
    """Ready requires BOTH a git repo and a root SCHEMA.md — each ingredient
    alone is still not a vault, collapsed to the one external contract."""
    target = _make_non_vault_dir(tmp_path / "not-a-vault", missing_ingredient)
    config_path = _single_vault_config(tmp_path, str(target))
    _resolve_not_configured(config_path)


def _make_non_vault_dir(target: Path, missing_ingredient: str) -> Path:
    """A directory carrying at most ONE of the two ready-vault ingredients."""
    target.mkdir()
    if missing_ingredient == "git-repo-without-schema":
        subprocess.run(["git", "init", "--quiet", str(target)], check=True, capture_output=True)
    elif missing_ingredient == "schema-without-git-repo":
        (target / "SCHEMA.md").write_text("---\nschema_version: 1\n---\n", encoding="utf-8")
    return target


def test_no_config_and_bad_path_carry_distinct_remediations(tmp_path: Path):
    """One external contract, three internal states: the remediation text must
    still be state-specific, or 'specific remediation' means nothing."""
    no_config = _resolve_not_configured(tmp_path / "absent" / "config.toml")
    config_path = _single_vault_config(tmp_path, str(tmp_path / "nowhere"))
    bad_path = _resolve_not_configured(config_path)
    assert no_config != bad_path


# ---------------------------------------------------------------------------
# Ready: resolution + default-vault semantics
# ---------------------------------------------------------------------------


def test_default_vault_pointing_at_an_initialized_vault_resolves_its_root(
    tmp_path: Path,
):
    vault = _init_vault(tmp_path / "vault")
    config_path = _single_vault_config(tmp_path, str(vault))
    resolved = _vault_root_of(_resolve(config_path))
    assert resolved == vault


def test_explicit_vault_argument_overrides_the_default_vault(tmp_path: Path):
    main = _init_vault(tmp_path / "main-vault")
    papers = _init_vault(tmp_path / "papers-vault")
    config_path = _write_config(
        tmp_path,
        'schema_version = 1\ndefault_vault = "main"\n\n'
        f'[vaults.main]\npath = "{main}"\n\n'
        f'[vaults.papers]\npath = "{papers}"\n',
    )
    assert _vault_root_of(_resolve(config_path, vault="papers")) == papers
    assert _vault_root_of(_resolve(config_path)) == main


def test_tilde_in_vault_path_expands_to_the_current_home(tmp_path: Path, _isolated_home: Path):
    vault = _init_vault(_isolated_home / "vault")
    config_path = _single_vault_config(tmp_path, "~/vault")
    assert _vault_root_of(_resolve(config_path)) == vault


def test_environment_variables_in_vault_path_expand_at_resolution_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = _init_vault(tmp_path / "data" / "vault")
    monkeypatch.setenv("KNOTICA_TEST_DATA_DIR", str(tmp_path / "data"))
    config_path = _single_vault_config(tmp_path, "$KNOTICA_TEST_DATA_DIR/vault")
    assert _vault_root_of(_resolve(config_path)) == vault


# ---------------------------------------------------------------------------
# Per-call freshness: setup takes effect without a restart
# ---------------------------------------------------------------------------


def test_config_written_after_a_failed_resolve_takes_effect_without_reload(
    tmp_path: Path,
):
    """The graceful-boot story end to end at the resolver level: first call
    unconfigured, setup writes the file, next call is ready — no restart, no
    reload hook."""
    config_path = tmp_path / "config" / "config.toml"
    _resolve_not_configured(config_path)

    vault = _init_vault(tmp_path / "vault")
    _single_vault_config(tmp_path, str(vault))
    assert _vault_root_of(_resolve(config_path)) == vault


def test_config_rewrite_between_calls_redirects_to_the_new_vault(tmp_path: Path):
    """No module-level cache: the second call must see the rewritten file."""
    first = _init_vault(tmp_path / "first-vault")
    second = _init_vault(tmp_path / "second-vault")

    config_path = _single_vault_config(tmp_path, str(first))
    assert _vault_root_of(_resolve(config_path)) == first

    _single_vault_config(tmp_path, str(second))
    assert _vault_root_of(_resolve(config_path)) == second


# ---------------------------------------------------------------------------
# The three-state machine is distinguishable internally
# ---------------------------------------------------------------------------


def _module_has_enum_with_members(module, member_names: set[str]) -> bool:
    import enum

    return any(
        isinstance(obj, type)
        and issubclass(obj, enum.Enum)
        and member_names <= set(obj.__members__)
        for obj in vars(module).values()
    )


def test_the_module_distinguishes_the_three_internal_states():
    """Diagnostics must report WHICH of the three states holds — the module
    exposes them as an enum even though tools see one collapsed contract."""
    config = _config_module()
    expected = {"UNCONFIGURED", "CONFIGURED_NO_VAULT", "READY"}
    assert _module_has_enum_with_members(config, expected), (
        "knotica.core.config must expose a three-state enum with members "
        f"{sorted(expected)} so diagnostics can distinguish them"
    )
