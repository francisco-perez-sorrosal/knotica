"""Behavioral tests for ``knotica.service`` -- the OS-service-manager
install/uninstall/status module realizing the lifecycle-managed loop service.

The daemon this module registers runs unattended against a real vault, so
these tests enforce one hard rule by construction: **no test may reach a real
service manager**. An autouse fixture replaces ``subprocess.run`` with a
recorder that raises if any test forgets to stub it explicitly -- a silent
fallthrough to the real ``launchctl``/``systemctl`` fails loudly instead of
quietly registering a real system service. Unit/plist files never touch the
developer's real ``$HOME`` either: every install/uninstall/status call below
passes an explicit ``home=`` under ``tmp_path`` (the module's own documented
test seam), never the process's real home directory.

Design under test: **one supervised process iterating all configured
topics** (not one unit per topic) -- ``install()`` writes a single unit
running ``python -m knotica.service``; the watched-topic set is resolved
fresh every supervision cycle via ``resolve_watched_topics()``, never baked
into the unit file. Two characterization tests at the end pin two
shipped-but-noteworthy behaviors (a failed register command is not wrapped
into a typed error, and does not roll back the unit file it already wrote)
so a future change to either is a deliberate, visible diff -- see
LEARNINGS.md for the open design question this surfaces.

RED until ``knotica.service`` lands with this exact surface: every import
below is deferred into the test body so collection succeeds
pre-implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError


# ---------------------------------------------------------------------------
# Hard rule: no test may reach a real service manager (WIP pre-mortem #4)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _forbid_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any code path under test reaches a real subprocess.

    Individual tests that exercise ``install``/``uninstall`` pass their own
    ``_FakeRunner`` explicitly (the module's own injection seam), which never
    touches this patched ``subprocess.run`` at all.
    """

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "real subprocess.run reached from a service-manager test -- pass "
            "a _FakeRunner via install(runner=...)/uninstall(runner=...) instead"
        )

    monkeypatch.setattr(subprocess, "run", _forbidden)


class _FakeRunner:
    """A ``subprocess.run``-shaped recorder honoring ``check=True`` like the
    real thing (raises ``CalledProcessError`` on a non-zero return when
    checked) -- so failure-path tests exercise the same contract ``install``/
    ``uninstall`` actually rely on, not a weaker stand-in."""

    def __init__(self, *, returncode: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.returncode = returncode

    def __call__(
        self, args: tuple[str, ...], *, check: bool = False, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(args))
        if check and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, args)
        return subprocess.CompletedProcess(
            args=args, returncode=self.returncode, stdout="", stderr=""
        )


# ---------------------------------------------------------------------------
# detect_platform() -- launchd (macOS) / systemd (Linux) selection
# ---------------------------------------------------------------------------


def test_detect_platform_selects_launchd_for_darwin() -> None:
    from knotica.service.manager import detect_platform

    assert detect_platform("darwin").name == "launchd"


def test_detect_platform_selects_systemd_for_linux() -> None:
    from knotica.service.manager import detect_platform

    assert detect_platform("linux").name == "systemd"


def test_detect_platform_raises_a_typed_error_for_an_unsupported_platform() -> None:
    from knotica.service.manager import detect_platform

    with pytest.raises(KnoticaError) as excinfo:
        detect_platform("windows")

    assert excinfo.value.message
    assert excinfo.value.fix


# ---------------------------------------------------------------------------
# build_spec() -- resolves the vault to supervise from config.toml
# ---------------------------------------------------------------------------


def test_build_spec_resolves_the_configured_vault(vault_config: Path, template_vault: Path) -> None:
    from knotica.service.manager import build_spec

    spec = build_spec(config_path=vault_config)

    assert spec.vault_name == "main"
    assert spec.vault_path == template_vault


def test_build_spec_raises_not_configured_without_a_ready_vault(unconfigured_env: Path) -> None:
    from knotica.service.manager import build_spec

    with pytest.raises(KnoticaError) as excinfo:
        build_spec(config_path=unconfigured_env / ".config" / "knotica" / "config.toml")

    assert excinfo.value.code == ErrorCode.NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Template content -- launchd plist / systemd unit generation (pure, no I/O)
# ---------------------------------------------------------------------------


def _spec(vault_path: Path) -> object:
    from knotica.service.manager import ServiceSpec

    return ServiceSpec(vault_name="main", vault_path=vault_path)


def test_launchd_plist_declares_the_service_label(tmp_path: Path) -> None:
    from knotica.service.manager import SERVICE_LABEL, detect_platform

    content = detect_platform("darwin").render_unit(
        _spec(tmp_path / "vault"), home=tmp_path / "home"
    )

    assert SERVICE_LABEL in content


def test_launchd_plist_program_arguments_run_the_daemon_module(tmp_path: Path) -> None:
    from knotica.service.manager import daemon_argv, detect_platform

    content = detect_platform("darwin").render_unit(
        _spec(tmp_path / "vault"), home=tmp_path / "home"
    )

    assert all(arg in content for arg in daemon_argv())
    assert "-m" in content
    assert "knotica.service" in content


def test_launchd_plist_paths_are_derived_from_the_given_vault_and_home(tmp_path: Path) -> None:
    from knotica.service.manager import detect_platform

    plat = detect_platform("darwin")
    first = plat.render_unit(_spec(tmp_path / "vault-one"), home=tmp_path / "home-one")
    second = plat.render_unit(_spec(tmp_path / "vault-two"), home=tmp_path / "home-two")

    assert str(tmp_path / "vault-one") in first
    assert str(tmp_path / "vault-two") in second
    assert first != second


def test_systemd_unit_exec_start_runs_the_daemon_module(tmp_path: Path) -> None:
    from knotica.service.manager import daemon_argv, detect_platform

    content = detect_platform("linux").render_unit(
        _spec(tmp_path / "vault"), home=tmp_path / "home"
    )

    assert all(arg in content for arg in daemon_argv())


def test_systemd_unit_working_directory_is_derived_from_the_spec(tmp_path: Path) -> None:
    from knotica.service.manager import detect_platform

    content = detect_platform("linux").render_unit(
        _spec(tmp_path / "my-vault"), home=tmp_path / "home"
    )

    assert str(tmp_path / "my-vault") in content


# ---------------------------------------------------------------------------
# unit_path() -- where each platform's unit file lives under a given home
# ---------------------------------------------------------------------------


def test_launchd_unit_path_lives_under_launch_agents_in_the_given_home(tmp_path: Path) -> None:
    from knotica.service.manager import detect_platform

    path = detect_platform("darwin").unit_path(tmp_path / "home")

    assert path.suffix == ".plist"
    assert "LaunchAgents" in path.parts
    assert str(tmp_path / "home") in str(path)


def test_systemd_unit_path_lives_under_systemd_user_in_the_given_home(tmp_path: Path) -> None:
    from knotica.service.manager import detect_platform

    path = detect_platform("linux").unit_path(tmp_path / "home")

    assert path.suffix == ".service"
    assert "systemd" in path.parts
    assert str(tmp_path / "home") in str(path)


# ---------------------------------------------------------------------------
# resolve_watched_topics() -- config-resolved, fresh every call (not cached)
# ---------------------------------------------------------------------------


def test_resolve_watched_topics_lists_the_seeded_topic(vault_config: Path) -> None:
    from knotica.service.manager import resolve_watched_topics

    topics = resolve_watched_topics(config_path=vault_config)

    assert "agentic-systems" in topics


def test_resolve_watched_topics_reflects_a_topic_added_between_two_calls(
    vault_config: Path, template_vault: Path
) -> None:
    from knotica.service.manager import resolve_watched_topics

    before = resolve_watched_topics(config_path=vault_config)
    assert "second-topic" not in before

    new_topic_dir = template_vault / "second-topic"
    new_topic_dir.mkdir()
    (new_topic_dir / "index.md").write_text("# second-topic\n", encoding="utf-8")

    after = resolve_watched_topics(config_path=vault_config)

    assert "second-topic" in after


def test_resolve_watched_topics_excludes_reserved_top_level_names(vault_config: Path) -> None:
    from knotica.service.manager import resolve_watched_topics

    topics = resolve_watched_topics(config_path=vault_config)

    assert "sources" not in topics


# ---------------------------------------------------------------------------
# supervise() -- the daemon body: one process, every configured topic, fresh
# topic resolution between cycles
# ---------------------------------------------------------------------------


def test_supervise_ticks_every_configured_topic_per_cycle(vault_config: Path) -> None:
    from knotica.service.manager import supervise

    ticks: list[str] = []

    supervise(
        config_path=vault_config,
        run_topic=lambda vault_root, topic: ticks.append(topic),
        sleep=lambda seconds: None,
        max_cycles=2,
    )

    assert ticks == ["agentic-systems", "agentic-systems"], (
        "one supervised process must run one watch tick per configured topic "
        "per cycle, for every cycle"
    )


def test_supervise_reresolves_topics_between_cycles(
    vault_config: Path, template_vault: Path
) -> None:
    from knotica.service.manager import supervise

    ticks: list[str] = []

    def add_topic_after_first_cycle(seconds: float) -> None:
        if not (template_vault / "second-topic").exists():
            new_topic_dir = template_vault / "second-topic"
            new_topic_dir.mkdir()
            (new_topic_dir / "index.md").write_text("# second-topic\n", encoding="utf-8")

    supervise(
        config_path=vault_config,
        run_topic=lambda vault_root, topic: ticks.append(topic),
        sleep=add_topic_after_first_cycle,
        max_cycles=2,
    )

    assert ticks.count("second-topic") == 1, (
        "a topic added between two supervision cycles must be picked up on the "
        "next cycle -- the watched set resolves fresh per cycle, never cached"
    )


def test_resolve_watched_topics_is_empty_when_nothing_is_configured(unconfigured_env: Path) -> None:
    from knotica.service.manager import resolve_watched_topics

    topics = resolve_watched_topics(
        config_path=unconfigured_env / ".config" / "knotica" / "config.toml"
    )

    assert topics == ()


# ---------------------------------------------------------------------------
# install() -- writes the unit file + registers it (mocked runner)
# ---------------------------------------------------------------------------


def test_install_writes_the_unit_file_under_the_given_home(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install

    home = tmp_path / "home"
    plan = install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=home,
        platform=detect_platform("darwin"),
        runner=_FakeRunner(),
    )

    assert plan.performed is True
    assert plan.unit_path.exists()
    assert plan.unit_path.read_text(encoding="utf-8") == plan.unit_content


def test_install_invokes_the_register_command(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install

    runner = _FakeRunner()
    install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=tmp_path / "home",
        platform=detect_platform("darwin"),
        runner=runner,
    )

    assert runner.calls
    assert "launchctl" in runner.calls[0]
    assert "bootstrap" in runner.calls[0]


def test_reinstalling_is_idempotent_and_converges_to_identical_content(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install

    spec = ServiceSpec(vault_name="main", vault_path=tmp_path / "vault")
    home = tmp_path / "home"
    runner = _FakeRunner()

    first = install(spec, home=home, platform=detect_platform("darwin"), runner=runner)
    second = install(spec, home=home, platform=detect_platform("darwin"), runner=runner)

    assert first.unit_content == second.unit_content
    assert first.unit_path == second.unit_path
    assert len(runner.calls) == 2  # re-install re-registers -- not an error either time


def test_install_dry_run_performs_no_file_write_and_no_subprocess_call(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install

    runner = _FakeRunner()
    plan = install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=tmp_path / "home",
        platform=detect_platform("darwin"),
        runner=runner,
        dry_run=True,
    )

    assert plan.performed is False
    assert not plan.unit_path.exists()
    assert runner.calls == []


# ---------------------------------------------------------------------------
# uninstall() -- symmetric with install(), clean no-op when nothing installed
# ---------------------------------------------------------------------------


def test_uninstall_removes_the_unit_file_that_install_created(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install, uninstall

    home = tmp_path / "home"
    plat = detect_platform("darwin")
    installed = install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=home,
        platform=plat,
        runner=_FakeRunner(),
    )
    assert installed.unit_path.exists()

    result = uninstall(home=home, platform=plat, runner=_FakeRunner())

    assert result.unit_existed is True
    assert not installed.unit_path.exists()


def test_uninstall_invokes_the_deregister_command(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install, uninstall

    home = tmp_path / "home"
    plat = detect_platform("darwin")
    install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=home,
        platform=plat,
        runner=_FakeRunner(),
    )
    runner = _FakeRunner()

    uninstall(home=home, platform=plat, runner=runner)

    assert runner.calls
    assert "launchctl" in runner.calls[0]
    assert "bootout" in runner.calls[0]


def test_uninstall_when_nothing_was_installed_does_not_raise(tmp_path: Path) -> None:
    from knotica.service.manager import detect_platform, uninstall

    result = uninstall(
        home=tmp_path / "home", platform=detect_platform("darwin"), runner=_FakeRunner()
    )

    assert result.unit_existed is False


def test_uninstall_dry_run_removes_no_unit_file(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install, uninstall

    home = tmp_path / "home"
    plat = detect_platform("darwin")
    installed = install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=home,
        platform=plat,
        runner=_FakeRunner(),
    )
    runner = _FakeRunner()

    uninstall(home=home, platform=plat, runner=runner, dry_run=True)

    assert installed.unit_path.exists()
    assert runner.calls == []


# ---------------------------------------------------------------------------
# status() -- installed/running/stopped, via the existing heartbeat convention
# ---------------------------------------------------------------------------


def test_status_reports_not_installed_when_no_unit_file_exists(tmp_path: Path) -> None:
    from knotica.service.manager import detect_platform, status

    result = status(home=tmp_path / "home", platform=detect_platform("darwin"))

    assert result.installed is False


def test_status_reports_installed_once_a_unit_file_exists(tmp_path: Path) -> None:
    from knotica.service.manager import ServiceSpec, detect_platform, install, status

    home = tmp_path / "home"
    plat = detect_platform("darwin")
    install(
        ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
        home=home,
        platform=plat,
        runner=_FakeRunner(),
    )

    result = status(home=home, platform=plat)

    assert result.installed is True


@pytest.mark.parametrize(
    ("platform_name", "expected_verified"), [("darwin", True), ("linux", False)]
)
def test_status_flags_the_untested_systemd_path(
    tmp_path: Path, platform_name: str, expected_verified: bool
) -> None:
    from knotica.service.manager import detect_platform, status

    result = status(home=tmp_path / "home", platform=detect_platform(platform_name))

    assert result.verified is expected_verified


def test_status_reports_a_configured_topic_as_alive_via_the_heartbeat_convention(
    vault_config: Path, template_vault: Path
) -> None:
    from knotica.core.loop_heartbeat import write_heartbeat
    from knotica.service.manager import detect_platform, status

    write_heartbeat(template_vault, "agentic-systems", interval_seconds=2.0)

    result = status(
        home=template_vault / "home", config_path=vault_config, platform=detect_platform("darwin")
    )

    topic_entry = next(entry for entry in result.topics if entry["topic"] == "agentic-systems")
    assert topic_entry["alive"] is True


def test_status_reports_a_configured_topic_as_not_alive_with_no_heartbeat(
    vault_config: Path, template_vault: Path
) -> None:
    from knotica.service.manager import detect_platform, status

    result = status(
        home=template_vault / "home", config_path=vault_config, platform=detect_platform("darwin")
    )

    topic_entry = next(entry for entry in result.topics if entry["topic"] == "agentic-systems")
    assert topic_entry["alive"] is False


# ---------------------------------------------------------------------------
# Characterization -- two shipped-but-noteworthy behaviors (see LEARNINGS.md)
# ---------------------------------------------------------------------------


def test_a_failed_register_command_propagates_rather_than_being_swallowed(tmp_path: Path) -> None:
    """install() does not wrap a register failure into a typed error -- the raw
    subprocess failure propagates. Characterized here so a future decision to
    wrap it (e.g. at the CLI boundary in the paired ``knotica service`` command)
    is a deliberate, visible change, not a silent behavior shift."""
    from knotica.service.manager import ServiceSpec, detect_platform, install

    with pytest.raises(subprocess.CalledProcessError):
        install(
            ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
            home=tmp_path / "home",
            platform=detect_platform("darwin"),
            runner=_FakeRunner(returncode=1),
        )


def test_uninstall_cleans_up_a_unit_file_left_by_a_failed_install(tmp_path: Path) -> None:
    """install() does not roll back the unit file it already wrote when the
    register command then fails -- but uninstall() (tolerant of a failed
    deregister) always removes it, so the system has no permanent zombie
    even though install() itself has no auto-rollback."""
    from knotica.service.manager import ServiceSpec, detect_platform, install, uninstall

    home = tmp_path / "home"
    plat = detect_platform("darwin")
    with pytest.raises(subprocess.CalledProcessError):
        install(
            ServiceSpec(vault_name="main", vault_path=tmp_path / "vault"),
            home=home,
            platform=plat,
            runner=_FakeRunner(returncode=1),
        )
    stray_unit = plat.unit_path(home)
    assert stray_unit.exists()  # the characterized gap: write happens before register

    uninstall(home=home, platform=plat, runner=_FakeRunner())

    assert not stray_unit.exists()
