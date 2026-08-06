"""``knotica desktop`` -- wire an *existing* install into Claude Desktop.

Distinct from ``knotica init --desktop`` by intent, not by degree. ``init`` is
the first-run wizard: it scaffolds a vault, writes ``config.toml``, registers
the MCP server, and ``--desktop`` adds Desktop patching as one more stage of
that setup. This command re-points an install that already exists, so it must
*not* do the rest -- notably, ``init`` upserts its vault as the **default** in
``config.toml``, which would silently switch which knowledge base is active for
someone who ran it only to fix a stale Desktop entry.

So the split is: ``init --desktop`` for standing an install up, ``desktop
install`` for maintaining one. The shape mirrors ``knotica service
install|status``, the other command that manages an OS-level integration rather
than vault content.

The Desktop-config mechanics themselves are *not* reimplemented here. Launch
argv construction and the additive/backed-up patch live in
:mod:`knotica.cli.init` and are called from this adapter, so there is exactly
one definition of what a knotica Desktop entry looks like.
"""

from __future__ import annotations

import argparse

from knotica.cli.common import (
    EXIT_MISUSE,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
)
from knotica.cli.init import (
    MCP_SERVER_NAME,
    desktop_config_path,
    mcp_from_source,
    patch_desktop,
    warm_launch,
)

__all__ = ["configure", "run"]


def configure(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the ``desktop`` command and its subcommands."""
    parser = subparsers.add_parser(
        "desktop",
        parents=[common_parent()],
        help="point Claude Desktop at this knotica install",
        description=(
            "Manage the Claude Desktop MCP entry for an install that already "
            "exists. Unlike `knotica init --desktop`, this never scaffolds a "
            "vault and never rewrites config.toml, so it cannot change which "
            "knowledge base is active."
        ),
    )
    desktop_sub = parser.add_subparsers(dest="desktop_command", metavar="<subcommand>")
    _configure_install(desktop_sub)
    _configure_status(desktop_sub)
    return parser


def _configure_install(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    subparsers.add_parser(
        "install",
        parents=[common_parent(nested=True)],
        help="write (or refresh) the Claude Desktop MCP entry",
        description=(
            "Patch the Claude Desktop config so its `knotica` MCP server points "
            "at this install. Additive and idempotent: the file is backed up "
            "first, every other server is preserved, and the entry's `env` block "
            "(where Desktop MCP credentials live) is carried over untouched."
        ),
    )


def _configure_status(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    subparsers.add_parser(
        "status",
        parents=[common_parent(nested=True)],
        help="report what the Claude Desktop entry currently points at",
        description="Read-only: report the current Claude Desktop MCP entry for knotica.",
    )


def run(args: argparse.Namespace) -> int:
    """Dispatch to the selected ``desktop`` subcommand."""
    console = console_from_args(args)
    command = getattr(args, "desktop_command", None)
    if command == "install":
        return _run_install(console)
    if command == "status":
        return _run_status(console)
    console.error("usage: knotica desktop {install,status}")
    return EXIT_MISUSE


def _run_install(console: Console) -> int:
    """Patch the Desktop config, warm the launch, and name the next step."""
    from_source = mcp_from_source()
    patch_desktop(console, from_source)
    warm_launch(console, from_source)
    console.data(f"Claude Desktop MCP entry '{MCP_SERVER_NAME}' points at {from_source}")
    console.data("next step: fully quit Claude Desktop (Cmd-Q) and reopen it")
    return EXIT_SUCCESS


def _run_status(console: Console) -> int:
    """Report the current Desktop entry without touching it."""
    import json

    path = desktop_config_path()
    if not path.is_file():
        console.data(f"no Claude Desktop config at {path}")
        return EXIT_SUCCESS
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        console.warn(f"Desktop config at {path} is not valid JSON")
        return EXIT_SUCCESS
    entry = config.get("mcpServers", {}).get(MCP_SERVER_NAME)
    if not isinstance(entry, dict):
        console.data(f"no '{MCP_SERVER_NAME}' server registered in {path}")
        console.data("next step: knotica desktop install")
        return EXIT_SUCCESS
    console.data(f"command: {entry.get('command', '(unset)')}")
    console.data(f"args: {' '.join(str(a) for a in entry.get('args', []))}")
    # Report only the credential env NAMES; the values are secrets.
    console.data(f"env: {', '.join(sorted(entry.get('env', {}))) or '(none)'}")
    return EXIT_SUCCESS
