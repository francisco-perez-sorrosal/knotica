"""OS-service-manager install / uninstall / status for the loop watcher daemon.

The self-improvement loop (observe -> gate -> heal) becomes an automatically
supervised background service so the user never starts an auxiliary process by
hand. This module owns the *lifecycle* only; loop semantics are unchanged --
the daemon is the same headless watcher, running on a clone, flock-guarded, one
commit per mutating op.

Two platform generators sit behind one interface:

* **launchd** (macOS) -- the live-verified target. A LaunchAgent plist under
  ``~/Library/LaunchAgents`` with ``RunAtLoad`` + ``KeepAlive`` for
  supervision/restart.
* **systemd** (Linux) -- code-complete but **untested**: a ``--user`` unit under
  ``~/.config/systemd/user``. Flagged untested here and via ``status().verified``
  so surfaces can warn.

Design contract -- **one supervised process iterating all configured topics**:
the daemon resolves its watched-topic set from ``config.toml`` *fresh on every
supervision cycle* (:func:`resolve_watched_topics`), never a set baked into the
unit file at install time. A topic added to the vault after install is picked up
on the next cycle without reinstalling the service.

Liveness follows the existing heartbeat convention: :func:`status` reports each
topic's runner liveness via :func:`knotica.core.loop_heartbeat.read_runner_liveness`,
the same ``.knotica/locks/`` signal ``wiki_status`` already exposes -- no new
liveness mechanism.

Zero-burden bar: install is declarative (write unit + register); uninstall is
strictly symmetrical (deregister + remove unit), tolerant of a
not-currently-installed state, so it never leaves a zombie behind.
"""

from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from knotica.core.config import diagnose
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.lint import RESERVED_TOP_LEVEL_NAMES
from knotica.core.loop_heartbeat import read_runner_liveness
from knotica.store import LocalFSStore, VaultStore

__all__ = [
    "SERVICE_LABEL",
    "SYSTEMD_UNIT_NAME",
    "InstallPlan",
    "ServiceSpec",
    "ServiceStatus",
    "UninstallPlan",
    "build_spec",
    "daemon_argv",
    "detect_platform",
    "install",
    "resolve_watched_topics",
    "status",
    "supervise",
    "uninstall",
]

#: launchd LaunchAgent label / systemd unit stem. One service supervises the
#: whole configured vault, so a single fixed identity is correct.
SERVICE_LABEL = "com.knotica.loop"
SYSTEMD_UNIT_NAME = "knotica-loop.service"

#: Default seconds between supervision cycles (one pass over all configured
#: topics). Observation evals are cache-cheap on unchanged content.
DEFAULT_SUPERVISION_INTERVAL_SECONDS = 30.0

#: Subprocess runner seam -- tests inject a fake so no real ``launchctl`` /
#: ``systemctl`` is ever invoked in CI.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def daemon_argv() -> tuple[str, ...]:
    """The argv the installed unit runs: the self-contained daemon entry.

    ``python -m knotica.service`` invokes :func:`supervise`, which resolves the
    watched-topic set from config on every cycle. The interpreter is bound at
    install time (stable), but the topic set is never baked in.
    """
    return (sys.executable, "-m", "knotica.service")


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """What to install: the vault the daemon supervises and the argv it runs.

    ``vault_path`` is the unit's working directory and label context only -- the
    daemon still resolves topics from config fresh at runtime, so the path is not
    a frozen topic set.
    """

    vault_name: str
    vault_path: Path
    exec_argv: tuple[str, ...] = field(default_factory=daemon_argv)


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Declarative description of an install -- returned verbatim on ``dry_run``.

    ``performed`` is False for a dry run (nothing was written or registered) and
    True once the unit file was written and the register command ran.
    """

    platform: str
    verified: bool
    unit_path: Path
    unit_content: str
    register_command: tuple[str, ...]
    performed: bool


@dataclass(frozen=True, slots=True)
class UninstallPlan:
    """Declarative description of an uninstall -- the symmetric counterpart."""

    platform: str
    unit_path: Path
    deregister_command: tuple[str, ...]
    unit_existed: bool
    performed: bool


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """Install + liveness readout. ``verified`` flags the untested systemd path."""

    platform: str
    verified: bool
    installed: bool
    unit_path: Path
    topics: list[dict[str, Any]]


class _Platform(ABC):
    """One OS service manager: where the unit lives and how to (de)register it."""

    name: str
    verified: bool

    @abstractmethod
    def unit_path(self, home: Path) -> Path:
        """Absolute path of the unit file under ``home``."""

    @abstractmethod
    def render_unit(self, spec: ServiceSpec, *, home: Path) -> str:
        """Render the unit file content for ``spec``."""

    @abstractmethod
    def register_command(self, unit_path: Path) -> tuple[str, ...]:
        """Command that loads/enables the unit after it is written."""

    @abstractmethod
    def deregister_command(self, unit_path: Path) -> tuple[str, ...]:
        """Command that unloads/disables the unit before it is removed."""


class _Launchd(_Platform):
    """macOS LaunchAgent (live-verified). ``RunAtLoad`` + ``KeepAlive``."""

    name = "launchd"
    verified = True

    def unit_path(self, home: Path) -> Path:
        return home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"

    def render_unit(self, spec: ServiceSpec, *, home: Path) -> str:
        log_dir = home / "Library" / "Logs" / "knotica"
        program_arguments = "\n".join(
            f"      <string>{_xml_escape(arg)}</string>" for arg in spec.exec_argv
        )
        return _load_template("launchd.plist.template").substitute(
            label=SERVICE_LABEL,
            program_arguments=program_arguments,
            working_directory=str(spec.vault_path),
            stdout_path=str(log_dir / "loop.out.log"),
            stderr_path=str(log_dir / "loop.err.log"),
        )

    def register_command(self, unit_path: Path) -> tuple[str, ...]:
        return ("launchctl", "bootstrap", f"gui/{os.getuid()}", str(unit_path))

    def deregister_command(self, unit_path: Path) -> tuple[str, ...]:
        return ("launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}")


class _Systemd(_Platform):
    """Linux systemd --user unit. Code-complete but UNTESTED on live systemd."""

    name = "systemd"
    verified = False

    def unit_path(self, home: Path) -> Path:
        return home / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME

    def render_unit(self, spec: ServiceSpec, *, home: Path) -> str:
        exec_start = " ".join(spec.exec_argv)
        return _load_template("systemd.service.template").substitute(
            exec_start=exec_start,
            working_directory=str(spec.vault_path),
        )

    def register_command(self, unit_path: Path) -> tuple[str, ...]:
        return ("systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME)

    def deregister_command(self, unit_path: Path) -> tuple[str, ...]:
        return ("systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME)


def detect_platform(platform_name: str | None = None) -> _Platform:
    """Return the platform generator for this OS (or an explicit override).

    Raises :class:`KnoticaError` for an unsupported platform -- the loop service
    ships launchd + systemd only.
    """
    name = platform_name or sys.platform
    if name in ("darwin", "launchd"):
        return _Launchd()
    if name in ("linux", "systemd") or name.startswith("linux"):
        return _Systemd()
    raise KnoticaError(
        code=ErrorCode.INVALID_ARGUMENT,
        message=f"No loop service manager for platform {name!r}.",
        fix="The loop service supports macOS (launchd) and Linux (systemd) only.",
    )


def _home(home: Path | None) -> Path:
    """Resolve the target home directory (explicit override wins -- a test seam)."""
    return home if home is not None else Path.home()


def build_spec(
    vault: str | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
    exec_argv: tuple[str, ...] | None = None,
) -> ServiceSpec:
    """Resolve the vault to supervise and assemble the install spec.

    Raises :class:`KnoticaError` (``NOT_CONFIGURED``) when no ready vault
    resolves -- there is nothing to supervise, so install must not proceed.
    """
    diagnosis = diagnose(vault=vault, config_path=config_path)
    if diagnosis.vault is None:
        raise KnoticaError(
            code=ErrorCode.NOT_CONFIGURED,
            message=diagnosis.detail,
            fix=diagnosis.remediation,
        )
    return ServiceSpec(
        vault_name=diagnosis.vault.name,
        vault_path=diagnosis.vault.path,
        exec_argv=exec_argv if exec_argv is not None else daemon_argv(),
    )


def install(
    spec: ServiceSpec,
    *,
    dry_run: bool = False,
    home: Path | None = None,
    platform: _Platform | None = None,
    runner: Runner | None = None,
) -> InstallPlan:
    """Write the unit file and register it. Idempotent: re-install overwrites.

    On ``dry_run`` nothing is written or run -- the returned plan shows exactly
    what *would* happen (unit path, rendered content, register command).
    """
    target = _home(home)
    plat = platform or detect_platform()
    unit_path = plat.unit_path(target)
    content = plat.render_unit(spec, home=target)
    register = plat.register_command(unit_path)
    if dry_run:
        return _install_plan(plat, unit_path, content, register, performed=False)

    # Resolved at call time (never a def-time default) so test monkeypatches
    # of subprocess.run always take effect -- a frozen default could silently
    # reach the real service manager.
    run = runner if runner is not None else subprocess.run
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(content, encoding="utf-8")
    run(register, check=True, capture_output=True, text=True)
    return _install_plan(plat, unit_path, content, register, performed=True)


def uninstall(
    *,
    dry_run: bool = False,
    home: Path | None = None,
    platform: _Platform | None = None,
    runner: Runner | None = None,
) -> UninstallPlan:
    """Deregister and remove the unit -- the strict symmetric inverse of install.

    Idempotent and zombie-free: a not-currently-installed service is a clean
    no-op (deregister failures are tolerated -- the unit may already be
    unloaded), and the unit file is removed if present.
    """
    target = _home(home)
    plat = platform or detect_platform()
    unit_path = plat.unit_path(target)
    deregister = plat.deregister_command(unit_path)
    existed = unit_path.exists()
    if dry_run:
        return _uninstall_plan(plat, unit_path, deregister, existed, performed=False)

    # Deregister first (reverse of install's write-then-register); tolerate a
    # not-loaded unit -- uninstall must never fail because the service was
    # already stopped. Runner resolved at call time, never as a def-time
    # default (a frozen default would bypass test monkeypatches).
    run = runner if runner is not None else subprocess.run
    run(deregister, check=False, capture_output=True, text=True)
    unit_path.unlink(missing_ok=True)
    return _uninstall_plan(plat, unit_path, deregister, existed, performed=True)


def status(
    *,
    vault: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    home: Path | None = None,
    platform: _Platform | None = None,
) -> ServiceStatus:
    """Report install state + per-topic runner liveness (heartbeat convention).

    No service-manager call is made here: ``installed`` is unit-file presence,
    and liveness comes from the ``.knotica/locks/`` heartbeat -- so status is
    always safe to call, no mocking needed.
    """
    target = _home(home)
    plat = platform or detect_platform()
    unit_path = plat.unit_path(target)
    diagnosis = diagnose(vault=vault, config_path=config_path)
    topics: list[dict[str, Any]] = []
    if diagnosis.vault is not None:
        vault_root = diagnosis.vault.path
        for topic in resolve_watched_topics(vault=vault, config_path=config_path):
            liveness = read_runner_liveness(vault_root, topic)
            topics.append({"topic": topic, **liveness})
    return ServiceStatus(
        platform=plat.name,
        verified=plat.verified,
        installed=unit_path.exists(),
        unit_path=unit_path,
        topics=topics,
    )


def resolve_watched_topics(
    vault: str | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """The topic set to watch *this cycle*, resolved fresh from config + vault.

    Reads ``config.toml`` fresh (no cache) and enumerates topic directories from
    the resolved vault. Returns ``()`` when no vault resolves -- the daemon then
    idles until config becomes valid, rather than crashing.
    """
    diagnosis = diagnose(vault=vault, config_path=config_path)
    if diagnosis.vault is None:
        return ()
    store = LocalFSStore(diagnosis.vault.path)
    return tuple(name for name in sorted(store.list_dir("")) if _is_topic(store, name))


def supervise(
    *,
    vault: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    interval_seconds: float = DEFAULT_SUPERVISION_INTERVAL_SECONDS,
    run_topic: Callable[[Path, str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    """The supervised process: iterate every configured topic, forever.

    Each cycle resolves the watched-topic set fresh (so a config change between
    cycles is honored) and runs one watch tick per topic. ``run_topic``,
    ``sleep``, and ``max_cycles`` are injection seams: tests drive a bounded
    number of cycles with a fake tick and never touch a real vault.
    """
    tick = run_topic if run_topic is not None else _default_run_topic
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        diagnosis = diagnose(vault=vault, config_path=config_path)
        if diagnosis.vault is not None:
            vault_root = diagnosis.vault.path
            for topic in resolve_watched_topics(vault=vault, config_path=config_path):
                tick(vault_root, topic)
        cycle += 1
        if max_cycles is None or cycle < max_cycles:
            sleep(max(0.2, interval_seconds))


def _default_run_topic(vault_root: Path, topic: str) -> None:
    """One live watch tick for a topic: beat, observe the default branch, poll.

    Imports the loop machinery lazily -- the install/status control path never
    pays for it, and the heavy dependency loads only in the running daemon.
    """
    from knotica.core.gapfill_config import resolve_gapfill_config
    from knotica.core.loop import LoopDecision, build_loop_runner, harness_evaluate
    from knotica.core.loop_heartbeat import write_heartbeat

    write_heartbeat(vault_root, topic, interval_seconds=DEFAULT_SUPERVISION_INTERVAL_SECONDS)
    runner = build_loop_runner(
        vault_root,
        topic,
        evaluate=harness_evaluate,
        gapfill_config=resolve_gapfill_config(),
    )
    observed = runner.observe_default()
    if observed.acted:
        print(observed.message, file=sys.stderr)
    candidate = runner.poll_once()
    if candidate.acted and candidate.decision is LoopDecision.fail:
        print(candidate.message, file=sys.stderr)


def _install_plan(
    plat: _Platform,
    unit_path: Path,
    content: str,
    register: tuple[str, ...],
    *,
    performed: bool,
) -> InstallPlan:
    return InstallPlan(
        platform=plat.name,
        verified=plat.verified,
        unit_path=unit_path,
        unit_content=content,
        register_command=register,
        performed=performed,
    )


def _uninstall_plan(
    plat: _Platform,
    unit_path: Path,
    deregister: tuple[str, ...],
    existed: bool,
    *,
    performed: bool,
) -> UninstallPlan:
    return UninstallPlan(
        platform=plat.name,
        unit_path=unit_path,
        deregister_command=deregister,
        unit_existed=existed,
        performed=performed,
    )


def _is_topic(store: VaultStore, name: str) -> bool:
    """Whether a top-level entry is a topic: a visible, non-reserved directory."""
    if name.startswith(".") or name in RESERVED_TOP_LEVEL_NAMES:
        return False
    try:
        store.list_dir(name)
    except NotADirectoryError:
        return False
    return True


def _load_template(name: str) -> Template:
    resource = importlib.resources.files("knotica.service.templates") / name
    return Template(resource.read_text(encoding="utf-8"))


def _xml_escape(value: str) -> str:
    """Escape the five XML predefined entities for plist ``<string>`` content."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
