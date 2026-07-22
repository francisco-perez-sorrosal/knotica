"""Behavioral tests for ``hooks/session_start.sh`` -- the SessionStart
topic-awareness seed + "what needs my attention" nudge.

Runs the actual POSIX shell script via ``subprocess``, but stubs ``uvx`` on
``$PATH`` with a fast, deterministic fake so tests never spawn the real uv
toolchain (no network dependency, no wall-clock reliance on a real knotica
install/cold-start). The fake logs every invocation's argv to a call-log file
so tests can assert exactly which ``knotica`` subcommands ran and how many
times -- the mechanism for verifying the topic-seed and attention-nudge share
ONE combined ``status --nudge`` call rather than spawning a subprocess per
signal.

The config-nudge / uvx-presence / migrate / doctor blocks predate this change
and are only exercised here incidentally (to reach the warm path); their own
behavior is not re-verified beyond "still fires, still exits 0".
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "session_start.sh"

#: A fast, deterministic fake ``uvx`` -- responds to each subcommand the hook
#: invokes without ever touching the real uv toolchain. Every invocation's
#: argv is appended to ``$CALL_LOG`` (one line per call) for assertion.
_FAKE_UVX = """#!/bin/sh
echo "$*" >> "$CALL_LOG"
case "$*" in
*"--version"*) echo "knotica 0.0.0-fake"; exit 0 ;;
*"migrate --check"*) exit "${FAKE_MIGRATE_EXIT:-0}" ;;
*"status --nudge"*) printf '%s' "${FAKE_NUDGE_OUTPUT:-}"; exit 0 ;;
*"doctor"*) printf '%s' "${FAKE_DOCTOR_OUTPUT:-OK}"; exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def fake_uvx_dir(tmp_path: Path) -> Path:
    """A directory holding the fake ``uvx``, ready to prepend onto ``$PATH``."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "uvx"
    script.write_text(_FAKE_UVX, encoding="utf-8")
    mode = script.stat().st_mode
    script.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@pytest.fixture
def call_log(tmp_path: Path) -> Path:
    """Where the fake ``uvx`` records every invocation (created lazily)."""
    return tmp_path / "calls.log"


def _path_without_uvx() -> str:
    """The current ``$PATH`` with every directory holding a real ``uvx`` removed.

    Portable stand-in for "uv is not installed" -- filters by content rather
    than assuming a fixed system PATH layout.
    """
    kept = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not (Path(entry) / "uvx").exists()
    ]
    return os.pathsep.join(kept)


def _write_config(home: Path, vault_path: str = "/tmp/fake-vault") -> None:
    """A minimal ``config.toml`` -- the fake ``uvx`` never reads it, so the
    vault path need not exist; only the config-nudge's ``grep`` needs to match."""
    config_dir = home / ".config" / "knotica"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{vault_path}"\n',
        encoding="utf-8",
    )


def _run_hook(
    *,
    home: Path,
    call_log: Path,
    uvx_dir: Path | None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    path = f"{uvx_dir}{os.pathsep}{os.environ.get('PATH', '')}" if uvx_dir else _path_without_uvx()
    env = {
        "HOME": str(home),
        "CLAUDE_PLUGIN_ROOT": str(REPO_ROOT),
        "PATH": path,
        "CALL_LOG": str(call_log),
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["sh", str(HOOK_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Warm path: topic list + attention items
# ---------------------------------------------------------------------------


def test_configured_vault_emits_the_topic_list(tmp_path: Path, fake_uvx_dir: Path, call_log: Path):
    home = tmp_path / "home"
    _write_config(home)

    result = _run_hook(
        home=home,
        call_log=call_log,
        uvx_dir=fake_uvx_dir,
        extra_env={"FAKE_NUDGE_OUTPUT": "This vault covers topics: agentic-systems, other\n"},
    )

    assert result.returncode == 0, result.stderr
    assert "This vault covers topics: agentic-systems, other" in result.stdout


def test_non_zero_attention_items_are_surfaced(tmp_path: Path, fake_uvx_dir: Path, call_log: Path):
    home = tmp_path / "home"
    _write_config(home)
    nudge = (
        "This vault covers topics: agentic-systems\n"
        "Needs attention: 2 pending suggestion(s), 1 refused-awaiting-rework\n"
    )

    result = _run_hook(
        home=home, call_log=call_log, uvx_dir=fake_uvx_dir, extra_env={"FAKE_NUDGE_OUTPUT": nudge}
    )

    assert result.returncode == 0, result.stderr
    assert "Needs attention: 2 pending suggestion(s), 1 refused-awaiting-rework" in result.stdout


def test_no_attention_line_when_nothing_needs_attention(
    tmp_path: Path, fake_uvx_dir: Path, call_log: Path
):
    home = tmp_path / "home"
    _write_config(home)

    result = _run_hook(
        home=home,
        call_log=call_log,
        uvx_dir=fake_uvx_dir,
        extra_env={"FAKE_NUDGE_OUTPUT": "This vault covers topics: agentic-systems\n"},
    )

    assert result.returncode == 0, result.stderr
    assert "Needs attention" not in result.stdout


# ---------------------------------------------------------------------------
# Cold / unconfigured path: unchanged, no nudge call at all
# ---------------------------------------------------------------------------


def test_unconfigured_vault_never_calls_the_nudge(
    tmp_path: Path, fake_uvx_dir: Path, call_log: Path
):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)  # no knotica/config.toml written

    result = _run_hook(home=home, call_log=call_log, uvx_dir=fake_uvx_dir)

    assert "Knotica is not configured yet" in result.stdout
    assert not call_log.exists() or "status" not in call_log.read_text()


def test_uvx_missing_skips_the_nudge_entirely(tmp_path: Path, call_log: Path):
    home = tmp_path / "home"
    _write_config(home)

    result = _run_hook(home=home, call_log=call_log, uvx_dir=None)

    assert "needs uv" in result.stdout.lower()
    assert not call_log.exists()


# ---------------------------------------------------------------------------
# Timing + subprocess-count discipline: one combined call, warm path stays fast
# ---------------------------------------------------------------------------


def test_warm_path_makes_exactly_one_status_nudge_call_and_stays_fast(
    tmp_path: Path, fake_uvx_dir: Path, call_log: Path
):
    home = tmp_path / "home"
    _write_config(home)

    start = time.monotonic()
    result = _run_hook(
        home=home,
        call_log=call_log,
        uvx_dir=fake_uvx_dir,
        extra_env={"FAKE_NUDGE_OUTPUT": "This vault covers topics: agentic-systems\n"},
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text().splitlines() if call_log.exists() else []
    nudge_calls = [line for line in calls if "status --nudge" in line]
    assert len(nudge_calls) == 1, (
        f"topic-seed and attention-nudge must share one combined status --nudge "
        f"call, got {nudge_calls!r}"
    )
    assert elapsed < 3.0, f"warm path with a fast stub took {elapsed:.2f}s -- unexpectedly slow"
