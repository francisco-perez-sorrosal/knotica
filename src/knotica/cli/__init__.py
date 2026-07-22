"""CLI adapter -- the ``knotica`` console entry point and dispatch registry.

Thin by design: parses arguments, applies output conventions (stdout = data,
stderr = messages -- see :mod:`knotica.cli.common`), and delegates. Reads go
through ``knotica.core`` read functions (including the read-only ``VaultVcs``
state accessors that ``doctor``/``status`` use); mutations go ONLY through
``knotica.core.operations.*`` -- this package never performs vault mutations and
never imports ``core.lock``, and the sole writer of the vault is
``core.transaction`` (enforced by the import-boundary fitness test).

**Self-registration dispatch.** ``main`` builds the argparse parser, then for
each command name imports its module and calls ``module.configure(subparsers)``
(which adds that subcommand's parser + flags) and dispatches to
``module.run(args) -> int``. Each command lives in exactly one module, so a
later step fills one command without editing this file -- no shared-writer
race. Every command module exports the same two callables:

* ``configure(subparsers) -> ArgumentParser`` -- add the subcommand's parser.
* ``run(args) -> int`` -- execute and return the process exit code.
"""

import argparse
import sys
from importlib import import_module
from importlib.metadata import version
from types import ModuleType

from knotica.cli.common import EXIT_ERROR, EXIT_MISUSE

#: Registered command names, in help-listing order. Each maps to a
#: ``knotica.cli.<name>`` module exporting ``configure`` and ``run``.
COMMAND_NAMES: tuple[str, ...] = (
    "init",
    "mcp",
    "doctor",
    "status",
    "migrate",
    "prompt",
    "guillotine",
    "okf",
    "eval",
    "datasets",
    "compile",
    "loop",
    "gapfill",
    "service",
)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected command's ``run``.

    Returns the command's exit code. With no subcommand, prints help to stderr
    and returns ``EXIT_MISUSE``; a stub command raising ``NotImplementedError``
    is reported cleanly on stderr as ``EXIT_ERROR``.
    """
    parser = argparse.ArgumentParser(
        prog="knotica",
        description="AI-maintained, compounding knowledge wiki -- deterministic CLI surface.",
    )
    parser.add_argument("--version", action="version", version=f"knotica {version('knotica')}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    modules = _register_commands(subparsers)
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_MISUSE

    try:
        return modules[args.command].run(args)
    except NotImplementedError as not_ready:
        print(f"knotica: {not_ready}", file=sys.stderr)
        return EXIT_ERROR


def _register_commands(subparsers: argparse._SubParsersAction) -> dict[str, ModuleType]:
    """Import each command module and let it register its own subparser."""
    modules: dict[str, ModuleType] = {}
    for name in COMMAND_NAMES:
        module = import_module(f"knotica.cli.{name}")
        module.configure(subparsers)
        modules[name] = module
    return modules
