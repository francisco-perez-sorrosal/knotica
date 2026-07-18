"""Behavioral tests for ``knotica init`` -- the fallback-channel setup wizard.

``knotica init`` scaffolds a vault, git-inits it, writes a resolvable
``config.toml``, and (with ``--desktop``) patches ``claude_desktop_config.json``
with the **absolute** ``uvx`` path -- every one of those writes must land inside
a redirected, disposable sandbox so this suite is safe on a real developer
machine.

Isolation is total and layered:

- ``isolated_home`` (conftest) redirects ``HOME``/``XDG_CONFIG_HOME`` into
  ``tmp_path`` and clears ``KNOTICA_CONFIG``, so both config-discovery paths land
  under tmp and no real ``~/.config/knotica`` or Desktop config can be read.
- ``hermetic_bin`` replaces ``PATH`` with a lone directory holding a symlink to
  the real ``git`` plus inert ``uvx``/``uv``/``claude``/``gh`` stubs -- init's
  optional client-registration and pre-warm calls become no-ops, so the run is
  deterministic and can never mutate the developer's real MCP clients.
- A containment canary snapshots the developer's REAL config/Desktop paths and
  asserts init left them byte-for-byte untouched.

RED until the wizard lands: the registered ``init`` stub raises
``NotImplementedError`` (exit 1), so every behavior assertion below fails until
the implementation replaces it. The path-expansion test exercises the existing
config resolver directly and is a GREEN anchor from the start.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from knotica.core import config as config_mod
from support.vault import git_commit_count, git_head_sha, git_status_porcelain

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "vault-template"

# Captured at import time, BEFORE any fixture monkeypatches HOME, so the
# containment canary can prove init never touched the developer's real state.
_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_CANARY_PATHS = (
    _REAL_HOME / ".config" / "knotica",
    _REAL_HOME / ".config" / "knotica" / "config.toml",
    _REAL_HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
)


# ---------------------------------------------------------------------------
# Hermetic execution: redirected HOME + a PATH with only git and inert stubs
# ---------------------------------------------------------------------------


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def hermetic_bin(tmp_path: Path) -> Path:
    """A lone PATH directory: real ``git`` symlinked, everything else inert.

    ``uvx`` prints a version (init pre-warms with ``uvx --version``) and resolves
    to an absolute path (so the Desktop patch has a real absolute command to
    write); ``uv``/``claude``/``gh`` exit 0 so init's optional registration and
    remote steps are no-ops that touch nothing real.
    """
    bin_dir = tmp_path / "hermetic-bin"
    bin_dir.mkdir()
    git = shutil.which("git")
    assert git is not None, "git must be installed to exercise knotica init"
    (bin_dir / "git").symlink_to(git)
    (bin_dir / "uvx").write_text("#!/bin/sh\necho 'uvx 0.0.0'\nexit 0\n", encoding="utf-8")
    (bin_dir / "uv").write_text("#!/bin/sh\necho 'uv 0.0.0'\nexit 0\n", encoding="utf-8")
    for name in ("claude", "gh"):
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for stub in bin_dir.iterdir():
        if not stub.is_symlink():
            _make_executable(stub)
    return bin_dir


def _init_env(hermetic_bin: Path) -> dict[str, str]:
    """The subprocess env: inherits the already-redirected HOME, isolates PATH."""
    env = dict(os.environ)  # HOME/XDG redirected + KNOTICA_CONFIG cleared by isolated_home
    env["PATH"] = str(hermetic_bin)
    env["NO_COLOR"] = "1"
    return env


def _cli(*args: str) -> list[str]:
    console = Path(sys.executable).with_name("knotica")
    if console.exists():
        return [str(console), *args]
    return [
        sys.executable,
        "-c",
        "import sys; from knotica.cli import main; sys.exit(main())",
        *args,
    ]


def _run_init(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _cli("init", *args),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Inventory + path helpers
# ---------------------------------------------------------------------------


def _template_inventory() -> set[str]:
    return {str(p.relative_to(TEMPLATE_DIR)) for p in TEMPLATE_DIR.rglob("*") if p.is_file()}


def _vault_inventory(vault: Path) -> set[str]:
    """Every file under ``vault`` (relative), excluding git's own ``.git`` tree."""
    files: set[str] = set()
    for p in vault.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(vault)
        if rel.parts and rel.parts[0] == ".git":
            continue
        files.add(str(rel))
    return files


def _desktop_config_path(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def _under(child: Path, parent: Path) -> bool:
    return child.resolve().is_relative_to(parent.resolve())


def _canary_snapshot() -> dict[str, tuple[bool, int | None, int | None]]:
    """Existence + mtime + size of the developer's REAL config/Desktop paths."""
    snapshot: dict[str, tuple[bool, int | None, int | None]] = {}
    for p in _REAL_CANARY_PATHS:
        try:
            st = p.stat()
            snapshot[str(p)] = (True, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            snapshot[str(p)] = (False, None, None)
    return snapshot


# ---------------------------------------------------------------------------
# Scaffold completeness, git init, resolvable config
# ---------------------------------------------------------------------------


def test_init_scaffolds_the_full_template_and_writes_a_resolvable_config(
    isolated_home: Path, hermetic_bin: Path, tmp_path: Path
):
    """``init --yes --vault <tmp>`` reproduces the packaged template exactly,
    git-inits the vault with an initial commit, and writes a config that
    resolves that vault to READY."""
    env = _init_env(hermetic_bin)
    vault = tmp_path / "vault"

    result = _run_init(env, "--yes", "--vault", str(vault))

    assert result.returncode == 0, result.stderr
    assert _vault_inventory(vault) == _template_inventory(), (
        "the scaffolded vault must contain exactly the packaged vault-template inventory"
    )
    assert (vault / ".git").is_dir(), "init must initialize a git repository in the vault"
    assert git_commit_count(vault) >= 1, "init must land an initial commit"
    assert git_status_porcelain(vault) == "", "the freshly initialized vault must be a clean tree"

    diagnosis = config_mod.diagnose()
    assert diagnosis.state is config_mod.ConfigState.READY, diagnosis.detail
    assert diagnosis.vault is not None
    assert diagnosis.vault.path.resolve() == vault.resolve()


def test_default_vault_path_resolves_through_home_and_env_expansion(
    isolated_home: Path, hermetic_bin: Path
):
    """A configured default-vault path resolves whether it is written with a
    ``~`` prefix or a ``$HOME`` reference -- expansion happens at resolution
    time (the config resolver's contract), never bound at import."""
    env = _init_env(hermetic_bin)
    vault = isolated_home / "data" / "knotica"

    result = _run_init(env, "--yes", "--vault", str(vault))
    assert result.returncode == 0, result.stderr

    config_file = config_mod.config_file_path()

    config_file.write_text(
        'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "~/data/knotica"\n',
        encoding="utf-8",
    )
    tilde = config_mod.diagnose()
    assert tilde.state is config_mod.ConfigState.READY, tilde.detail
    assert tilde.vault is not None and tilde.vault.path.resolve() == vault.resolve()

    config_file.write_text(
        'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "$HOME/data/knotica"\n',
        encoding="utf-8",
    )
    env_ref = config_mod.diagnose()
    assert env_ref.state is config_mod.ConfigState.READY, env_ref.detail
    assert env_ref.vault is not None and env_ref.vault.path.resolve() == vault.resolve()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_init_twice_leaves_the_vault_and_config_intact(
    isolated_home: Path, hermetic_bin: Path, tmp_path: Path
):
    """A second ``init`` over the same target is safe: no clobbered files, no
    dirtied tree, and the config still resolves to the same vault."""
    env = _init_env(hermetic_bin)
    vault = tmp_path / "vault"

    first = _run_init(env, "--yes", "--vault", str(vault))
    assert first.returncode == 0, first.stderr
    inventory_after_first = _vault_inventory(vault)
    head_after_first = git_head_sha(vault)

    second = _run_init(env, "--yes", "--vault", str(vault))

    assert second.returncode == 0, second.stderr
    assert _vault_inventory(vault) == inventory_after_first, (
        "re-init must not clobber vault contents"
    )
    assert git_status_porcelain(vault) == "", "re-init must leave a clean tree"
    assert git_head_sha(vault) == head_after_first, "re-init must not add gratuitous commits"

    diagnosis = config_mod.diagnose()
    assert diagnosis.state is config_mod.ConfigState.READY, diagnosis.detail
    assert diagnosis.vault is not None and diagnosis.vault.path.resolve() == vault.resolve()


# ---------------------------------------------------------------------------
# --no-input fails fast on missing required input
# ---------------------------------------------------------------------------


def test_no_input_without_a_vault_path_fails_fast_with_misuse_exit(
    isolated_home: Path, hermetic_bin: Path
):
    """With ``--no-input`` and no way to supply the required vault path, init
    must refuse to prompt and exit with the documented misuse code."""
    env = _init_env(hermetic_bin)

    result = _run_init(env, "--no-input")

    assert result.returncode == 2, (
        f"missing required input under --no-input must exit 2 (got {result.returncode}); "
        f"stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Desktop config patch: additive merge, absolute uvx, backup-first
# ---------------------------------------------------------------------------


def test_desktop_patch_merges_preserving_servers_and_writes_absolute_launch_with_backup(
    isolated_home: Path, hermetic_bin: Path, tmp_path: Path
):
    """``init --desktop`` merges into an existing Desktop config: pre-existing
    keys and servers survive, the added knotica server carries an ABSOLUTE
    ``uv`` (local repo) or ``uvx`` command (Desktop's minimal PATH), and the
    prior config is backed up byte-for-byte before the patch."""
    env = _init_env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "mcpServers": {"existing-server": {"command": "/usr/bin/true", "args": []}},
        "globalShortcut": "Cmd+Space",
    }
    original_text = json.dumps(original, indent=2)
    desktop.write_text(original_text, encoding="utf-8")
    vault = tmp_path / "vault"

    result = _run_init(env, "--yes", "--vault", str(vault), "--desktop")

    assert result.returncode == 0, result.stderr
    patched = json.loads(desktop.read_text(encoding="utf-8"))
    assert patched.get("globalShortcut") == "Cmd+Space", (
        "unrelated top-level keys must be preserved"
    )
    assert patched["mcpServers"]["existing-server"] == {"command": "/usr/bin/true", "args": []}, (
        "a pre-existing MCP server entry must survive the merge unchanged"
    )

    added = {
        name: entry for name, entry in patched["mcpServers"].items() if name != "existing-server"
    }
    knotica_entry = added.get("knotica")
    assert knotica_entry is not None, (
        f"init --desktop must add a knotica server entry; added entries: {added!r}"
    )
    command = knotica_entry.get("command", "")
    assert os.path.isabs(command) and ("uv" in command or "uvx" in command), (
        "init --desktop must add a knotica server whose command is an absolute uv/uvx path; "
        f"entry: {knotica_entry!r}"
    )
    args = knotica_entry.get("args", [])
    if args[:2] == ["run", "--directory"]:
        assert args[3:6] == ["--group", "evals", "knotica"] and args[-1] == "mcp", (
            f"Desktop uv run args must be run --directory <repo> --group evals knotica mcp; "
            f"got args={args!r}"
        )
        assert Path(args[2]).resolve() == REPO_ROOT.resolve(), (
            f"Desktop uv run --directory must point at the local checkout; got {args[2]!r}"
        )
    else:
        assert args[0] == "--refresh" and args[1] == "--from" and args[-2:] == ["knotica", "mcp"], (
            f"Desktop uvx args must be --refresh --from <repo> … knotica mcp; got args={args!r}"
        )
        assert args.count("--with") == 2 and "anthropic" in args and "dspy" in args, (
            "Desktop uvx args must include --with anthropic --with dspy for headless query; "
            f"got args={args!r}"
        )

    backups = [b for b in desktop.parent.glob("*.bak") if b.is_file()]
    assert backups, "the prior Desktop config must be backed up before patching"
    assert any(b.read_text(encoding="utf-8") == original_text for b in backups), (
        "a backup must preserve the original Desktop config byte-for-byte"
    )


# ---------------------------------------------------------------------------
# Containment: nothing escapes the temp sandbox
# ---------------------------------------------------------------------------


def test_init_writes_nothing_outside_the_temp_sandbox(
    isolated_home: Path, hermetic_bin: Path, tmp_path: Path
):
    """A full init run (vault + config + Desktop patch) writes only under
    ``tmp_path`` and never touches the developer's real config or Desktop
    config -- the property that makes this suite safe on a real machine."""
    env = _init_env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text('{"mcpServers": {}}', encoding="utf-8")
    vault = tmp_path / "vault"

    before = _canary_snapshot()
    result = _run_init(env, "--yes", "--vault", str(vault), "--desktop")

    assert result.returncode == 0, result.stderr
    assert vault.is_dir() and _under(vault, tmp_path), "the vault must live under the temp sandbox"
    assert _under(config_mod.config_file_path(), tmp_path), "config must be written under tmp"
    assert _canary_snapshot() == before, (
        "init touched a real config/Desktop path outside the sandbox -- containment breach"
    )
