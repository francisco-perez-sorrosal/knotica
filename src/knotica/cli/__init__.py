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

**Two levels, six lanes.** The top-level set is the six process lanes plus the
six commands that belong to no lane. A lane module owns no behavior: it
re-parents its member command modules one level deeper via the same
``configure(subparsers)`` contract, which is depth-agnostic. Lane names come
from ``core.process_model``, so the CLI is a projection of the one declaration
the MCP dispatchers and the dashboard rails also project from.
"""

from __future__ import annotations

import argparse
import sys
from importlib import import_module
from importlib.metadata import version
from typing import cast

from knotica.cli.common import EXIT_ERROR, EXIT_MISUSE, CommandModule
from knotica.core import process_model

#: Registered top-level command names, in help-listing order. Each maps to a
#: ``knotica.cli.<name>`` module exporting ``configure`` and ``run``. The six
#: lane names are read from the process model rather than restated, so a lane
#: renamed there is renamed here.
COMMAND_NAMES: tuple[str, ...] = (
    "init",
    "desktop",
    "mcp",
    "status",
    "prompt",
    *process_model.LANES,
    "service",
)

#: Old top-level invocations, mapped to where they now live. Purely a
#: deprecation shim: ``main`` rewrites a matching argv prefix, warns on stderr,
#: and then parses the *new* invocation, so an old name behaves exactly like
#: the command it forwards to. Keys may be compound (``"compile promote"``)
#: because three moves flatten a group level away, which no same-level
#: ``aliases=`` and no shim *parser* can express; the longest matching prefix
#: wins. Nothing here is registered as a parser, so the help surface is clean
#: from day one and the whole table is deletable in one commit.
DEPRECATED_TOP_LEVEL: dict[str, tuple[str, ...]] = {
    "compile promote": ("improve", "promote"),
    "datasets bootstrap-train": ("improve", "bootstrap-train"),
    "datasets freeze": ("improve", "freeze"),
    "gapfill discover": ("fill", "discover"),
    "eval": ("improve", "eval"),
    "loop": ("improve", "loop"),
    "compile": ("improve", "compile"),
    "datasets": ("improve",),
    "gapfill": ("fill",),
    "doctor": ("tend", "doctor"),
    "okf": ("tend", "okf"),
    "migrate": ("tend", "migrate"),
    "guillotine": ("tend", "guillotine"),
}

_LONGEST_DEPRECATED_KEY = max(len(key.split()) for key in DEPRECATED_TOP_LEVEL)


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
    args = parser.parse_args(_resolve_deprecated(sys.argv[1:] if argv is None else list(argv)))

    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_MISUSE

    try:
        return modules[args.command].run(args)
    except NotImplementedError as not_ready:
        print(f"knotica: {not_ready}", file=sys.stderr)
        return EXIT_ERROR


def _resolve_deprecated(argv: list[str]) -> list[str]:
    """Rewrite a deprecated invocation to its new location, warning on stderr.

    The warning goes to stderr and *only* stderr: a ``--json`` consumer piping
    the command, and the SessionStart hook that captures a command's output
    with ``2>&1``, must both keep seeing exactly what the target command
    prints. Returns ``argv`` unchanged when nothing matches.
    """
    if not argv or argv[0].startswith("-"):
        return argv
    for length in range(min(len(argv), _LONGEST_DEPRECATED_KEY), 0, -1):
        old = " ".join(argv[:length])
        target = DEPRECATED_TOP_LEVEL.get(old)
        if target is None:
            continue
        new = " ".join(target)
        print(
            f"knotica: '{old}' has moved. Run: knotica {new}. "
            "The old name still works and will be removed in a future release.",
            file=sys.stderr,
        )
        if length == 1:
            _hint_compound_form(old, argv[length:])
        return [*target, *argv[length:]]
    return argv


def _hint_compound_form(matched: str, rest: list[str]) -> None:
    """Point at the compound rewrite when a flag hid it from prefix matching.

    ``knotica compile --quiet promote ...`` matches only the single-token key
    ``compile`` (the flag breaks the two-word prefix), rewrites to a form with
    no ``promote`` beneath it, and argparse then rejects the leftover token.
    The matcher cannot skip flags safely -- a token after ``--topic`` is a
    value, not a subcommand -- so instead of guessing, name the command the
    user probably meant. stderr only, like every other message here.
    """
    for key, target in DEPRECATED_TOP_LEVEL.items():
        first, _, second = key.partition(" ")
        if first == matched and second and second in rest:
            print(
                f"knotica: if you meant '{key}', its new home is: knotica {' '.join(target)}",
                file=sys.stderr,
            )


def _register_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, CommandModule]:
    """Import each command module and let it register its own subparser.

    ``import_module`` returns an untyped ``ModuleType``; the ``cast`` here is
    the single, justified site asserting every ``knotica.cli.<name>`` module
    satisfies the ``configure``/``run`` contract (the docstring-documented
    self-registration convention) -- callers downstream then get a properly
    typed ``run(args) -> int`` instead of ``Any``.
    """
    modules: dict[str, CommandModule] = {}
    for name in COMMAND_NAMES:
        module = cast(CommandModule, import_module(f"knotica.cli.{name}"))
        module.configure(subparsers)
        modules[name] = module
    return modules
