"""Shared CLI plumbing -- exit codes, output discipline, and color policy.

Every subcommand module reuses this one place so the surface feels like one
system (clig.dev): **stdout carries data, stderr carries every message**
(info, warning, error, progress) -- a script piping ``knotica <cmd>`` must get
clean data on stdout and nothing else. Exit codes are the deterministic branch
signal for hooks and scripts; color is semantic-only and auto-suppressed
whenever the output is not an interactive terminal.

Config is never resolved here and never cached -- adapters resolve it fresh per
invocation (the stateless-server contract); this module only shapes output.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import Any, Protocol, TextIO, cast

from knotica.core import process_model

__all__ = [
    "EXIT_ERROR",
    "EXIT_MIGRATION_AVAILABLE",
    "EXIT_MISUSE",
    "EXIT_NO_GOLDEN_SET",
    "EXIT_NOT_CONFIGURED",
    "EXIT_SUCCESS",
    "UNCONFIGURED_MESSAGE",
    "CommandModule",
    "Console",
    "LaneCommand",
    "Status",
    "common_parent",
    "console_from_args",
    "lane_rail",
    "unconfigured",
]

#: Exit codes (documented interface -- hooks and scripts branch on these).
EXIT_SUCCESS = 0  #: success; a check may have warned but nothing failed.
EXIT_ERROR = 1  #: a check FAILED or the operation failed.
EXIT_MISUSE = 2  #: bad arguments / wrong usage (argparse also emits this).
EXIT_NOT_CONFIGURED = 3  #: no config.toml / vault (mirrors the tool NOT_CONFIGURED).
EXIT_MIGRATION_AVAILABLE = 4  #: `migrate --check` only; up-to-date is EXIT_SUCCESS.
EXIT_NO_GOLDEN_SET = 5  #: `eval` only: the topic has no golden set; run `eval --bootstrap`.

#: The unconfigured message, byte-identical in intent across every surface
#: (interface consistency rule): tools render it in the envelope, the CLI prints
#: it to stderr and exits ``EXIT_NOT_CONFIGURED``.
UNCONFIGURED_MESSAGE = (
    "knotica is not configured — run `/knotica:setup` (Claude Code) or `knotica init` (CLI)."
)

#: ANSI SGR codes for the three semantic states (never decorative).
_ANSI_RESET = "\033[0m"
_ANSI_BY_STATUS = {
    "PASS": "\033[32m",  # green
    "WARN": "\033[33m",  # yellow
    "FAIL": "\033[31m",  # red
}


class Status:
    """The three semantic check states -- always paired with their text glyph."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


def common_parent(*, nested: bool = False) -> argparse.ArgumentParser:
    """Return a parent parser carrying the flags every subcommand shares.

    Added via ``parents=[common_parent()]`` so each subcommand inherits the same
    ``--quiet``/``--verbose``/``--no-color``/``--no-input`` surface. ``--json``
    is *not* here -- it belongs only to the machine-parseable commands
    (``doctor``/``status``/``migrate``), so each of those adds it itself.

    **Pass ``nested=True`` on every nested subparser** (``okf check``, ``doctor
    repair``, ``service status``, ...) and never on a top-level one. The
    asymmetry is not cosmetic and must not be "simplified" away: it is the only
    thing that makes ``knotica okf --quiet check`` and ``knotica okf check
    --quiet`` mean the same thing.

    Attaching the *plain* parent to a nested subparser looks correct and is
    silently wrong. ``argparse._SubParsersAction.__call__`` parses the nested
    subparser into a **fresh** namespace -- so every default it declares is
    applied -- and then copies every key of that namespace onto the parent's::

        subnamespace, arg_strings = parser.parse_known_args(arg_strings, None)
        for key, value in vars(subnamespace).items():
            setattr(namespace, key, value)

    A nested ``--quiet`` defaulting to ``False`` therefore *overwrites* the
    ``True`` the command-level parser already stored, and the flag the user
    typed before the subcommand name is discarded with no error at all.
    ``default=argparse.SUPPRESS`` leaves an unpassed flag out of the
    subnamespace entirely, so nothing is copied and the command-level value
    survives; a flag that *is* passed nested is present and wins.

    Top-level parsers keep the ordinary ``False`` default, which is what
    guarantees the attribute always exists on the namespace -- ``args.verbose``
    is read directly in a few command modules, not only through
    :func:`console_from_args`'s ``getattr`` fallback.
    """
    default = argparse.SUPPRESS if nested else False
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        default=default,
        help="suppress informational output (errors still print to stderr)",
    )
    parent.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=default,
        help="emit debug context to stderr (never on by default)",
    )
    parent.add_argument(
        "--no-color",
        action="store_true",
        default=default,
        help="disable color (also auto-off when not a TTY, NO_COLOR, or TERM=dumb)",
    )
    parent.add_argument(
        "--no-input",
        action="store_true",
        default=default,
        help="never prompt; fail fast if required input is missing",
    )
    return parent


def _should_use_color(no_color_flag: bool, stream: TextIO, environ: dict[str, str]) -> bool:
    """Resolve the color policy for ``stream`` (semantic color, off by default off-TTY)."""
    if no_color_flag:
        return False
    if "NO_COLOR" in environ:
        return False
    if environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


@dataclass(frozen=True, slots=True)
class Console:
    """Output discipline: data to stdout, every message to stderr.

    Construct via :func:`console_from_args` so the color policy and the
    quiet/verbose flags are resolved once from the parsed arguments.
    """

    quiet: bool
    verbose: bool
    use_color: bool
    out: TextIO
    err: TextIO

    def data(self, text: str) -> None:
        """Write payload data to stdout -- the only thing that ever goes there."""
        print(text, file=self.out)

    def info(self, text: str) -> None:
        """Write an informational message to stderr (suppressed under ``--quiet``)."""
        if not self.quiet:
            print(text, file=self.err)

    def warn(self, text: str) -> None:
        """Write a warning to stderr (always shown, even under ``--quiet``)."""
        print(text, file=self.err)

    def error(self, text: str) -> None:
        """Write an error to stderr (always shown)."""
        print(text, file=self.err)

    def debug(self, text: str) -> None:
        """Write debug context to stderr (only under ``--verbose``)."""
        if self.verbose:
            print(text, file=self.err)

    def status_glyph(self, status: str) -> str:
        """Return the ``PASS``/``WARN``/``FAIL`` glyph, colored only when enabled.

        The text glyph is always present, so meaning never rides on color alone
        (accessibility: never color-only).
        """
        if not self.use_color or status not in _ANSI_BY_STATUS:
            return status
        return f"{_ANSI_BY_STATUS[status]}{status}{_ANSI_RESET}"


def console_from_args(
    args: argparse.Namespace,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    environ: dict[str, str] | None = None,
) -> Console:
    """Build a :class:`Console` from parsed args, resolving the color policy.

    Color keys off the message stream (stderr), since that is where colored
    glyphs are written; data on stdout is always plain.
    """
    import os

    resolved_out = out if out is not None else sys.stdout
    resolved_err = err if err is not None else sys.stderr
    env = environ if environ is not None else dict(os.environ)
    return Console(
        quiet=bool(getattr(args, "quiet", False)),
        verbose=bool(getattr(args, "verbose", False)),
        use_color=_should_use_color(bool(getattr(args, "no_color", False)), resolved_err, env),
        out=resolved_out,
        err=resolved_err,
    )


def unconfigured(console: Console) -> int:
    """Emit the shared unconfigured message to stderr and return exit code 3."""
    console.error(UNCONFIGURED_MESSAGE)
    return EXIT_NOT_CONFIGURED


class CommandModule(Protocol):
    """The ``configure``/``run`` shape every ``knotica.cli.<name>`` module exports.

    ``import_module`` returns a plain ``ModuleType`` (attribute access is
    untyped), so this Protocol is the one place that names the self-
    registration contract precisely enough for the dispatch in
    :func:`knotica.cli.main` -- and in :class:`LaneCommand` -- to type-check
    without widening to ``Any``.
    """

    def configure(
        self, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> argparse.ArgumentParser: ...

    def run(self, args: argparse.Namespace) -> int: ...


#: The four namespace destinations :func:`common_parent` owns. Named once so
#: :func:`_suppress_common_defaults` and ``common_parent`` cannot drift apart.
_COMMON_DESTS = frozenset({"quiet", "verbose", "no_color", "no_input"})

#: How a lane's rail reads in help text. A ``sequence`` lane advances through
#: its stages in order; the ``checklist`` lane's stages are independent peers,
#: so an arrow between them would assert an ordering the model denies.
_RAIL_JOIN = {"sequence": " → ", "checklist": ", "}


def lane_rail(lane: str) -> str:
    """Render ``lane``'s rail from the process model, or ``""`` when it has none.

    The rail is read from ``core.process_model`` on every call -- the CLI is a
    projection of that one declaration, never a second copy of it, so a stage
    renamed there is renamed in ``knotica <lane> --help`` with no edit here.
    """
    stages = process_model.LANE_STAGES[lane]
    if not stages:
        return ""
    return _RAIL_JOIN[process_model.LANE_KIND[lane]].join(stage.title for stage in stages)


#: The MCP verb each ``Stage.action`` names, mapped to the CLI invocation that
#: performs the same real-world operation -- adapter-owned prose, not a
#: structural derivation (several verbs, e.g. ``suggestions_review``/``query``,
#: have no CLI equivalent at all and are deliberately absent here). A verb
#: reached from more than one lane's rail (``loop`` gates both Fill and
#: Improve; ``vault_health`` backs both Tend's ``doctor`` and ``okf`` stages)
#: renders the one real command that performs it, regardless of which lane's
#: rail is asking.
_VERB_CLI_COMMAND: Mapping[str, str] = MappingProxyType(
    {
        "gapfill_discover": "fill discover",
        "loop": "improve loop",
        "compile": "improve compile",
        "datasets": "improve bootstrap-train",
        "branches": "improve promote",
        "vault_health": "tend doctor",
    }
)

#: Shown for a stage the client-as-brain advances through conversation, not a
#: CLI invocation (``Stage.handoff`` or no declared ``action`` at all).
_HANDOFF_TEXT = "handled via conversation"

#: Shown for a stage whose verb has no CLI equivalent (dashboard/MCP-only).
_NO_CLI_COMMAND_TEXT = "no CLI equivalent -- dashboard/MCP only"


def _advancing_text(stage: process_model.Stage, topic: str) -> str:
    """The rail's "how to advance this stage" text for ``stage``."""
    if stage.handoff or stage.action is None:
        return _HANDOFF_TEXT
    command = _VERB_CLI_COMMAND.get(stage.action)
    if command is None:
        return _NO_CLI_COMMAND_TEXT
    return f"knotica {command} --topic {topic}"


def _render_lane_rail(
    console: Console, lane: str, topic: str, stages: Sequence[Mapping[str, Any]]
) -> None:
    """Print ``lane``'s rail: each declared stage's title, live state, and
    advancing command -- one line per stage, in rail order."""
    declared = {stage.id: stage for stage in process_model.LANE_STAGES[lane]}
    console.data(f"{lane} rail -- topic: {topic}")
    for live in stages:
        stage = declared[live["id"]]
        console.data(f"  {stage.title:<14} {live['state']:<10} {_advancing_text(stage, topic)}")


def _suppress_common_defaults(parser: argparse.ArgumentParser) -> None:
    """Re-parent a command parser one level deeper without losing pre-name flags.

    ``common_parent(nested=True)`` exists because ``_SubParsersAction.__call__``
    parses a nested subparser into a *fresh* namespace -- applying every default
    it declares -- and then copies every key onto the parent's, so a nested
    ``--quiet`` defaulting to ``False`` silently discards the ``True`` the user
    typed *before* the subcommand name. A lane moves nine command parsers from
    depth 1 to depth 2, which puts every one of them in exactly that position.

    Rather than edit nine modules to pass ``nested=True`` (they are re-parented
    unchanged, by design), the lane applies the same suppression mechanically
    after registration. ``parser._actions`` is argparse's only handle on a
    registered action's default; there is no public accessor.
    """
    for action in parser._actions:  # noqa: SLF001 -- argparse exposes no public equivalent
        if action.dest in _COMMON_DESTS:
            action.default = argparse.SUPPRESS


class LaneCommand:
    """One process lane's ``configure``/``run`` pair over its member commands.

    A lane is a parser that owns no behavior of its own: it names a lane of the
    process model, renders that lane's rail in its help, and re-parents the
    command modules that act in it one level deeper. Lanes with no members
    (``home``, ``learn``, ``answer``) register the parser only and let their
    module supply its own ``run``.

    Membership is resolved by *observing* which parsers each member module
    registers, so a module that contributes two leaves (``compile`` also
    registers ``promote``) needs no declaration here and no edit there.

    A lane with members also accepts a hidden ``--topic`` at its own level:
    ``knotica <lane> --topic <t>`` with no subcommand renders that lane's
    live rail (each stage's title, current state, and the command that
    advances it) instead of the usage/misuse fallback. The flag is
    ``help=SUPPRESS``-ed so ``knotica <lane> --help`` stays byte-identical;
    each member subcommand still declares its own ``--topic`` independently.
    """

    def __init__(self, *, lane: str, summary: str, members: Sequence[str] = ()) -> None:
        if lane not in process_model.LANES:
            raise ValueError(f"{lane!r} is not a declared process lane")
        self.lane = lane
        self.summary = summary
        self.members = tuple(members)
        self._parser: argparse.ArgumentParser | None = None
        self._modules: dict[str, CommandModule] = {}

    @property
    def dest(self) -> str:
        """The namespace attribute carrying the chosen member command."""
        return f"{self.lane}_command"

    def configure(
        self, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> argparse.ArgumentParser:
        """Register the lane parser and every member command beneath it."""
        parser = subparsers.add_parser(
            self.lane,
            parents=[common_parent()],
            help=self.summary,
            description=self._description(),
        )
        self._parser = parser
        self._modules = {}
        if not self.members:
            return parser

        # Hidden (help=SUPPRESS): a bare `knotica <lane> --topic <t>` renders
        # the live rail via `run()` below, but `--help`/`-h` output must stay
        # byte-identical to today's, so this flag is documented in this
        # module's docstring instead.
        parser.add_argument("--topic", metavar="NAME", help=argparse.SUPPRESS)
        lane_sub = parser.add_subparsers(dest=self.dest, metavar="<command>")
        for module_name in self.members:
            module = cast(CommandModule, import_module(f"knotica.cli.{module_name}"))
            registered_before = set(lane_sub.choices)
            module.configure(lane_sub)
            for name in set(lane_sub.choices) - registered_before:
                _suppress_common_defaults(lane_sub.choices[name])
                self._modules[name] = module
        return parser

    def run(self, args: argparse.Namespace) -> int:
        """Dispatch to the chosen member, render the rail, or print the lane's help.

        A bare invocation (no member chosen) renders the live rail only when
        ``--topic`` was given -- the rail is inherently per-topic, so with no
        topic there is nothing to render and the prior misuse path is kept.
        """
        chosen = getattr(args, self.dest, None)
        if chosen is not None and chosen in self._modules:
            return self._modules[chosen].run(args)
        topic = getattr(args, "topic", None)
        if chosen is None and self.members and topic:
            return self._render_rail(args, topic)
        return self.print_usage()

    def _render_rail(self, args: argparse.Namespace, topic: str) -> int:
        """Render this lane's rail: each stage's title, live state, and advancing command.

        Imports are local: ``core.status`` pulls in ``evals.golden`` (and, through
        it, an LLM client module), which every other lane's bare invocation --
        and every module that merely imports this one, e.g. ``doctor`` -- must
        not inherit just to render a rail nobody asked for.
        """
        from knotica.core.config import diagnose
        from knotica.core.page import TopicNotFoundError
        from knotica.core.status import gather_wiki_status
        from knotica.store import LocalFSStore

        console = console_from_args(args)
        diagnosis = diagnose()
        if diagnosis.vault is None:
            return unconfigured(console)
        vault = diagnosis.vault
        store = LocalFSStore(vault.path)
        try:
            payload = gather_wiki_status(store, vault.path, topic=topic, view="summary")
        except TopicNotFoundError as error:
            console.error(str(error))
            return EXIT_ERROR
        stages = payload["topics"][0]["lanes"][self.lane]
        _render_lane_rail(console, self.lane, topic, stages)
        return EXIT_SUCCESS

    def print_usage(self) -> int:
        """Print the lane's own help to stderr and report misuse."""
        if self._parser is not None:
            self._parser.print_help(sys.stderr)
        return EXIT_MISUSE

    def _description(self) -> str:
        """The lane's help description, with its rail when the lane has one."""
        rail = lane_rail(self.lane)
        return f"{self.summary}. Rail: {rail}." if rail else f"{self.summary}."
