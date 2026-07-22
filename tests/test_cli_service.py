"""``knotica service`` CLI: install/uninstall/status of the OS-managed loop daemon.

Derived from the plan's behavior contract, not the implementation: exit codes
follow the CLI convention (0 ok / 1 error / 2 misuse / 3 not-configured), and
``status`` on a fresh fixture ``$HOME`` reports not-installed with no
subprocess call at all.

Hard rule (WIP pre-mortem #4 / mirrors ``tests/test_service_manager.py``): no
test in this file may reach a real service manager. An autouse fixture
replaces ``subprocess.run`` with a recorder that raises if any test forgets to
stub it -- a silent fallthrough to the real ``launchctl``/``systemctl`` fails
loudly instead of quietly registering a real system service. Tests that
exercise a non-dry-run ``install``/``uninstall`` explicitly monkeypatch
``subprocess.run`` again (the same fixture instance, so the later patch wins);
every other test relies on ``--dry-run`` or ``status`` (both subprocess-free
by construction) and never needs to.

``isolated_home``/``vault_config`` (see ``conftest.py``) redirect ``HOME`` into
``tmp_path``, so ``knotica.service.manager``'s default ``Path.home()`` resolves
under the fixture, never the developer's real home.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from knotica.cli.common import EXIT_ERROR, EXIT_MISUSE, EXIT_NOT_CONFIGURED, EXIT_SUCCESS


@pytest.fixture(autouse=True)
def _forbid_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any code path under test reaches a real subprocess."""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "real subprocess.run reached from a service CLI test -- use --dry-run, "
            "status, or monkeypatch subprocess.run explicitly"
        )

    monkeypatch.setattr(subprocess, "run", _forbidden)


def _fake_run(*, returncode: int = 0, stderr: str = ""):
    """A ``subprocess.run``-shaped fake honoring ``check=True`` like the real
    thing (raises ``CalledProcessError`` on a non-zero return when checked)."""

    def _run(
        args: tuple[str, ...], *, check: bool = False, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if check and returncode != 0:
            raise subprocess.CalledProcessError(returncode, args, output="", stderr=stderr)
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout="", stderr=stderr
        )

    return _run


def _unit_path(home: Path) -> Path:
    from knotica.service.manager import detect_platform

    return detect_platform().unit_path(home)


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_output_documents_all_three_subcommands(capsys) -> None:
    from knotica.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["service", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "install" in out
    assert "uninstall" in out
    assert "status" in out


# ---------------------------------------------------------------------------
# No subcommand -> misuse
# ---------------------------------------------------------------------------


def test_no_subcommand_exits_misuse(isolated_home: Path) -> None:
    from knotica.cli import main

    assert main(["service"]) == EXIT_MISUSE


# ---------------------------------------------------------------------------
# status -- always subprocess-free, safe with no vault configured
# ---------------------------------------------------------------------------


def test_status_reports_not_installed_on_a_fresh_fixture_home(isolated_home: Path, capsys) -> None:
    from knotica.cli import main

    exit_code = main(["service", "status"])

    assert exit_code == EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "not installed" in out


def test_status_json_reports_installed_false(isolated_home: Path, capsys) -> None:
    from knotica.cli import main

    exit_code = main(["service", "status", "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"] is False


# ---------------------------------------------------------------------------
# install --dry-run / uninstall --dry-run -- subprocess-free, write nothing
# ---------------------------------------------------------------------------


def test_install_dry_run_writes_nothing_and_exits_success(
    vault_config: Path, isolated_home: Path, capsys
) -> None:
    from knotica.cli import main

    exit_code = main(["service", "install", "--vault", "main", "--dry-run"])

    assert exit_code == EXIT_SUCCESS
    assert not _unit_path(isolated_home).exists()
    assert "would write" in capsys.readouterr().out


def test_uninstall_dry_run_on_a_fresh_home_reports_a_clean_no_op(
    isolated_home: Path, capsys
) -> None:
    from knotica.cli import main

    exit_code = main(["service", "uninstall", "--dry-run"])

    assert exit_code == EXIT_SUCCESS
    assert "clean no-op" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# install without a configured vault -> NOT_CONFIGURED
# ---------------------------------------------------------------------------


def test_install_without_a_configured_vault_exits_not_configured(
    unconfigured_env: Path, capsys
) -> None:
    from knotica.cli import main

    exit_code = main(["service", "install"])

    assert exit_code == EXIT_NOT_CONFIGURED
    err = capsys.readouterr().err
    assert "To fix" in err


# ---------------------------------------------------------------------------
# A failed register command: wrapped in the CLI grammar + rolled back
# ---------------------------------------------------------------------------


def test_a_failed_register_command_is_wrapped_and_the_unit_file_is_rolled_back(
    vault_config: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    from knotica.cli import main

    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1, stderr="boom"))

    exit_code = main(["service", "install", "--vault", "main"])

    assert exit_code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "failed because" in err
    assert "To fix" in err
    assert not _unit_path(isolated_home).exists(), (
        "install() writes the unit file before registering (characterized in "
        "test_service_manager.py) -- the CLI boundary must roll it back on a "
        "failed register so no zombie unit survives"
    )


# ---------------------------------------------------------------------------
# A successful install/uninstall round trip
# ---------------------------------------------------------------------------


def test_install_then_uninstall_round_trip_removes_the_unit(
    vault_config: Path,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    from knotica.cli import main

    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=0))

    install_exit = main(["service", "install", "--vault", "main"])
    assert install_exit == EXIT_SUCCESS
    assert _unit_path(isolated_home).exists()

    uninstall_exit = main(["service", "uninstall"])
    assert uninstall_exit == EXIT_SUCCESS
    assert not _unit_path(isolated_home).exists()
