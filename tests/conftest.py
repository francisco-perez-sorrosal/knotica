"""Vault-fixture test spine — shared fixtures for every knotica test.

The spine that every mutation test builds on (helpers live in
``tests/support/vault.py``):

- ``vault_seed`` (session) — the repo's ``vault-template/`` instantiated once
  per session into a temp dir with ``git init`` + identity + initial commit.
  Never handed to tests directly: it is the copy-source cache that makes
  per-test instantiation a cheap ``copytree``.
- ``template_vault`` (function) — a fresh, isolated, git-initialized vault per
  test, copied from the seed (``.git`` included). Mutate it freely; baseline
  state is exactly one non-knotica commit and a clean tree.
- ``isolated_home`` (function) — redirects ``HOME``/``XDG_CONFIG_HOME`` into
  ``tmp_path`` and clears ``KNOTICA_CONFIG`` so no test can ever read or write
  the real ``~/.config/knotica`` or any real vault.
- ``unconfigured_env`` (function) — ``isolated_home`` with no ``config.toml``
  anywhere: the unconfigured state, for error-envelope tests.
- ``vault_config`` (function) — writes a ``config.toml`` under the isolated
  home whose ``default_vault`` points at ``template_vault``, and exports
  ``KNOTICA_CONFIG`` with the config-file path so either config-discovery
  mechanism (home-relative or env override) lands on the same file.

Zero network; a fixture instantiation is a local copytree (well under 1 s).
"""

import shutil
from pathlib import Path

import pytest

from support.vault import run_git

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "vault-template"


@pytest.fixture(scope="session")
def vault_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Copy-source cache: template + git init + initial commit, built once per session.

    Tests must never mutate the seed — request ``template_vault`` for a
    mutable instance.
    """
    seed = tmp_path_factory.mktemp("vault-seed") / "vault"
    shutil.copytree(TEMPLATE_DIR, seed)
    run_git(seed, "init")
    run_git(seed, "config", "user.name", "knotica-tests")
    run_git(seed, "config", "user.email", "tests@knotica.invalid")
    run_git(seed, "config", "commit.gpgsign", "false")
    run_git(seed, "add", "-A")
    run_git(seed, "commit", "-m", "vault: instantiate template")
    return seed


@pytest.fixture
def template_vault(vault_seed: Path, tmp_path: Path) -> Path:
    """A fresh throwaway git vault instantiated from the template — mutate freely."""
    vault = tmp_path / "vault"
    shutil.copytree(vault_seed, vault)
    return vault


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fake HOME under tmp_path — the real user config can never be touched."""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("KNOTICA_CONFIG", raising=False)
    return home


@pytest.fixture
def unconfigured_env(isolated_home: Path) -> Path:
    """The unconfigured state: an isolated home with no config.toml anywhere."""
    return isolated_home


@pytest.fixture
def vault_config(
    isolated_home: Path, template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """A config.toml pointing the default vault at ``template_vault``.

    Lives at ``<isolated_home>/.config/knotica/config.toml`` and is also
    exported as ``KNOTICA_CONFIG`` (absolute file path), so the fixture works
    regardless of which discovery mechanism the config layer implements.
    Returns the config-file path.
    """
    config_dir = isolated_home / ".config" / "knotica"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{template_vault}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOTICA_CONFIG", str(config_path))
    return config_path
