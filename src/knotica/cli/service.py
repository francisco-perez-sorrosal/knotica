"""``knotica service`` -- install/uninstall/status of the OS-managed loop daemon.

Thin CLI wrapper over :mod:`knotica.service.manager`: parses arguments, resolves
the vault + platform, and renders the result. This adapter owns the failure
grammar the manager layer intentionally leaves alone -- a failed
``launchctl``/``systemctl`` register command raises a raw
:class:`subprocess.CalledProcessError` out of :func:`knotica.service.manager.install`
(characterized in ``tests/test_service_manager.py``: no wrapping, no rollback,
by design), so **this** boundary translates it into the "X failed because Y. To
fix: Z." grammar and rolls back the unit file ``install()`` already wrote --
the same core-stays-simple/adapter-absorbs-recovery split ``knotica init``
uses for its own subprocess wrapping (``_InitError``).

Install/uninstall never run automatically anywhere in this codebase (tests
inject a fake runner or use ``--dry-run``; nothing here calls a real service
manager) -- a live ``knotica service install`` is a command the user runs
themselves, on their own machine, when ready.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_MISUSE,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.service import manager

__all__ = ["configure", "run"]


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``service`` command and its three subcommands."""
    parser = subparsers.add_parser(
        "service",
        parents=[common_parent()],
        help="install/uninstall/status of the OS-managed loop daemon",
        description=(
            "Manage the lifecycle-managed loop service (launchd on macOS, "
            "systemd on Linux -- code-complete but untested): one supervised "
            "process that watches every configured topic, resolved fresh from "
            "config.toml each cycle."
        ),
    )
    service_sub = parser.add_subparsers(dest="service_command", metavar="<subcommand>")
    _configure_install(service_sub)
    _configure_uninstall(service_sub)
    _configure_status(service_sub)
    return parser


def _configure_install(service_sub: argparse._SubParsersAction) -> None:
    install = service_sub.add_parser(
        "install",
        parents=[common_parent()],
        help="write and register the loop service unit",
        description=(
            "Write the launchd/systemd unit for the configured vault and register "
            "it. Idempotent -- re-installing overwrites and re-registers."
        ),
    )
    install.add_argument("--vault", metavar="NAME", help="configured vault name to supervise")
    install.add_argument(
        "--dry-run", action="store_true", help="show the plan; write and register nothing"
    )


def _configure_uninstall(service_sub: argparse._SubParsersAction) -> None:
    uninstall = service_sub.add_parser(
        "uninstall",
        parents=[common_parent()],
        help="deregister and remove the loop service unit",
        description=(
            "Deregister and remove the unit file. A clean no-op if nothing is "
            "installed -- never fails because the service was already stopped."
        ),
    )
    uninstall.add_argument(
        "--dry-run", action="store_true", help="show the plan; deregister and remove nothing"
    )


def _configure_status(service_sub: argparse._SubParsersAction) -> None:
    status = service_sub.add_parser(
        "status",
        parents=[common_parent()],
        help="report install state and per-topic runner liveness",
        description=(
            "Report whether the unit is installed and each configured topic's "
            "runner liveness, via the existing heartbeat convention. Makes no "
            "service-manager call -- always safe to run."
        ),
    )
    status.add_argument("--vault", metavar="NAME", help="configured vault name to report on")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def run(args: argparse.Namespace) -> int:
    """Dispatch to the selected ``service`` subcommand."""
    console = console_from_args(args)
    command = getattr(args, "service_command", None)
    if command == "install":
        return _run_install(console, args)
    if command == "uninstall":
        return _run_uninstall(console, args)
    if command == "status":
        return _run_status(console, args)
    console.error("usage: knotica service {install,uninstall,status}")
    return EXIT_MISUSE


def _run_install(console: Console, args: argparse.Namespace) -> int:
    """Resolve the vault + platform, install (or dry-run), and report the plan."""
    try:
        spec = manager.build_spec(vault=args.vault)
        platform = manager.detect_platform()
    except KnoticaError as error:
        return _emit_knotica_error(console, error)

    try:
        plan = manager.install(spec, dry_run=args.dry_run, platform=platform)
    except (subprocess.CalledProcessError, OSError) as error:
        console.error(_describe_command_failure("install", error))
        manager.uninstall(platform=platform)
        console.error(
            "rolled back the partially written unit file -- no zombie service left behind."
        )
        return EXIT_ERROR

    _render_install_plan(console, plan)
    return EXIT_SUCCESS


def _run_uninstall(console: Console, args: argparse.Namespace) -> int:
    """Deregister and remove the unit (or dry-run), and report the plan."""
    try:
        platform = manager.detect_platform()
    except KnoticaError as error:
        return _emit_knotica_error(console, error)

    try:
        result = manager.uninstall(dry_run=args.dry_run, platform=platform)
    except (subprocess.CalledProcessError, OSError) as error:
        console.error(_describe_command_failure("uninstall", error))
        return EXIT_ERROR

    _render_uninstall_plan(console, result)
    return EXIT_SUCCESS


def _run_status(console: Console, args: argparse.Namespace) -> int:
    """Report install state + per-topic liveness (JSON or human)."""
    try:
        result = manager.status(vault=args.vault)
    except KnoticaError as error:
        return _emit_knotica_error(console, error)

    if args.json:
        console.data(json.dumps(_status_payload(result), ensure_ascii=False, indent=2))
    else:
        _render_status_human(console, result)
    return EXIT_SUCCESS


def _emit_knotica_error(console: Console, error: KnoticaError) -> int:
    """Print a core :class:`KnoticaError` in the CLI grammar and pick the exit code."""
    console.error(str(error))
    if error.fix:
        console.error(f"To fix: {error.fix}")
    return EXIT_NOT_CONFIGURED if error.code is ErrorCode.NOT_CONFIGURED else EXIT_ERROR


def _describe_command_failure(action: str, error: BaseException) -> str:
    """Render a subprocess/manager failure in the "X failed because Y. To fix:
    Z." grammar -- the CLI never lets a raw traceback reach the user."""
    if isinstance(error, subprocess.CalledProcessError):
        command = " ".join(str(part) for part in error.cmd)
        detail = (error.stderr or error.stdout or "").strip()
        detail_suffix = f" ({detail})" if detail else ""
        cause = f"`{command}` exited {error.returncode}{detail_suffix}"
    else:
        cause = str(error)
    return (
        f"knotica service {action} failed because {cause}. "
        f"To fix: resolve the error above and re-run `knotica service {action}`."
    )


def _render_install_plan(console: Console, plan: manager.InstallPlan) -> None:
    """Print the install plan -- ``performed`` distinguishes dry-run from real."""
    verb = "would write" if not plan.performed else "wrote"
    console.data(f"knotica service install ({plan.platform})")
    console.data(f"  {verb} unit → {plan.unit_path}")
    if not plan.verified:
        console.error(f"warning: {plan.platform} support is code-complete but untested")
    if plan.performed:
        console.data("  registered")


def _render_uninstall_plan(console: Console, plan: manager.UninstallPlan) -> None:
    """Print the uninstall plan -- symmetric with :func:`_render_install_plan`."""
    console.data(f"knotica service uninstall ({plan.platform})")
    if not plan.unit_existed:
        console.data("  nothing installed — clean no-op")
        return
    verb = "would remove" if not plan.performed else "removed"
    console.data(f"  {verb} unit → {plan.unit_path}")
    if plan.performed:
        console.data("  deregistered")


def _status_payload(result: manager.ServiceStatus) -> dict[str, Any]:
    """The machine-readable ``--json`` shape for :class:`ServiceStatus`."""
    return {
        "platform": result.platform,
        "verified": result.verified,
        "installed": result.installed,
        "unit_path": str(result.unit_path),
        "topics": result.topics,
    }


def _render_status_human(console: Console, result: manager.ServiceStatus) -> None:
    """Print the install state + per-topic liveness table."""
    installed = "installed" if result.installed else "not installed"
    console.data(f"knotica service status ({result.platform}) — {installed}")
    console.data(f"  unit: {result.unit_path}")
    if not result.verified:
        console.error(f"warning: {result.platform} support is code-complete but untested")
    if not result.topics:
        console.data("  no configured topics")
        return
    for topic in result.topics:
        state = "alive" if topic.get("alive") else "not alive"
        console.data(f"  {topic['topic']}: {state}")
