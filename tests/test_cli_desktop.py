"""``knotica desktop`` -- maintain the Claude Desktop MCP entry for an existing install.

The command exists because the setup wizard is the wrong tool for the job:
``knotica init --desktop`` also upserts its vault as the **default** in
``config.toml``, so reaching for it to fix a stale Desktop entry silently
switches which knowledge base is active. These tests pin that separation --
``desktop install`` writes the Desktop entry and nothing else.

Fixtures mirror ``tests/test_cli_init.py``: ``isolated_home`` redirects
``HOME``/``XDG_CONFIG_HOME`` under ``tmp_path`` and clears ``KNOTICA_CONFIG``;
``hermetic_bin`` replaces ``PATH`` with inert ``uv``/``uvx``/``claude`` stubs so
the pre-warm call cannot touch the developer's real environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from knotica.core import config as config_mod


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


def _env(hermetic_bin: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(hermetic_bin)
    env["NO_COLOR"] = "1"
    return env


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(_cli(*args), capture_output=True, text=True, env=env, timeout=120)


def _desktop_config_path(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def test_install_writes_the_entry_without_touching_config_toml(
    isolated_home: Path, hermetic_bin: Path
):
    env = _env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        json.dumps({"mcpServers": {"other": {"command": "/usr/bin/true", "args": []}}}, indent=2),
        encoding="utf-8",
    )

    result = _run(env, "desktop", "install")

    assert result.returncode == 0, result.stderr
    servers = json.loads(desktop.read_text(encoding="utf-8"))["mcpServers"]
    assert "knotica" in servers, "the knotica entry must be written"
    assert "other" in servers, "pre-existing servers must survive the patch"
    assert not config_mod.config_file_path().exists(), (
        "desktop install must not write config.toml -- doing so would re-point the "
        "active vault, which is exactly the accident this command exists to avoid"
    )


def test_install_is_idempotent_and_preserves_the_credentials_env_block(
    isolated_home: Path, hermetic_bin: Path
):
    env = _env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "knotica": {
                        "command": "/stale/uv",
                        "args": ["run", "--group", "evals", "knotica", "mcp"],
                        "env": {"ANTHROPIC_API_KEY": "sk-ant-sentinel"},
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    first = _run(env, "desktop", "install")
    after_first = desktop.read_text(encoding="utf-8")
    second = _run(env, "desktop", "install")
    after_second = desktop.read_text(encoding="utf-8")

    assert first.returncode == 0 and second.returncode == 0, f"{first.stderr}\n{second.stderr}"
    assert after_first == after_second, "a second `desktop install` must be a no-op"
    entry = json.loads(after_second)["mcpServers"]["knotica"]
    assert entry["env"] == {"ANTHROPIC_API_KEY": "sk-ant-sentinel"}, (
        "the env block carries the user's Desktop MCP credentials and must survive re-pointing"
    )
    assert "--group" not in entry["args"], "the stale group flag must be replaced"


def test_status_reports_the_entry_without_modifying_it(isolated_home: Path, hermetic_bin: Path):
    env = _env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {
            "mcpServers": {
                "knotica": {
                    "command": "/abs/uv",
                    "args": ["run", "--extra", "evals", "knotica", "mcp"],
                    "env": {"ANTHROPIC_API_KEY": "sk-ant-secret-value"},
                }
            }
        },
        indent=2,
    )
    desktop.write_text(original, encoding="utf-8")

    result = _run(env, "desktop", "status")

    assert result.returncode == 0, result.stderr
    assert desktop.read_text(encoding="utf-8") == original, "status must be read-only"
    assert "--extra evals" in result.stdout, "status must report the launch args"
    assert "ANTHROPIC_API_KEY" in result.stdout, "status must name the configured env keys"
    assert "sk-ant-secret-value" not in result.stdout, (
        "status must report env key NAMES only -- printing the value would leak a "
        "credential into terminal scrollback and CI logs"
    )


def test_status_on_an_unregistered_install_points_at_the_fix(
    isolated_home: Path, hermetic_bin: Path
):
    env = _env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text('{"mcpServers": {}}', encoding="utf-8")

    result = _run(env, "desktop", "status")

    assert result.returncode == 0, result.stderr
    assert "desktop install" in result.stdout, (
        "a missing entry must name the command that creates it, not just report absence"
    )


def test_bare_desktop_command_is_a_usage_error(isolated_home: Path, hermetic_bin: Path):
    result = _run(_env(hermetic_bin), "desktop")

    assert result.returncode != 0, "a bare `knotica desktop` must not silently succeed"
    assert "install" in result.stderr and "status" in result.stderr, (
        f"the usage line must list the available subcommands; got {result.stderr!r}"
    )


def test_a_no_op_run_does_not_overwrite_the_backup_of_the_original(
    isolated_home: Path, hermetic_bin: Path
):
    """The backup is written only when something actually changes.

    Backing up unconditionally would, on the second (no-op) run, copy the
    already-patched file over the ``.bak`` -- destroying the one snapshot of the
    user's original config precisely when they might want to roll back to it.
    """
    env = _env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "knotica": {
                        "command": "/stale/uv",
                        "args": ["run", "--group", "evals", "knotica", "mcp"],
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    assert _run(env, "desktop", "install").returncode == 0
    backup = desktop.with_name(desktop.name + ".bak")
    assert "--group" in backup.read_text(encoding="utf-8"), "the backup must hold the original"

    assert _run(env, "desktop", "install").returncode == 0

    assert "--group" in backup.read_text(encoding="utf-8"), (
        "a no-op re-run must leave the backup alone -- otherwise the second run "
        "silently replaces the original snapshot with the already-patched file"
    )


def test_install_reports_the_file_and_the_key_it_writes(isolated_home: Path, hermetic_bin: Path):
    """A config patch edits a file the user never opened, so the log is the only
    record: it must name the file, the key, and the resulting launch args."""
    env = _env(hermetic_bin)
    desktop = _desktop_config_path(isolated_home)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text('{"mcpServers": {}}', encoding="utf-8")

    result = _run(env, "desktop", "install")

    assert result.returncode == 0, result.stderr
    log = result.stderr
    assert str(desktop) in log, "the log must name the file being patched"
    assert "mcpServers.knotica" in log, "the log must name the key being written"
    assert "--extra evals" in log, "the log must show the launch args actually written"
