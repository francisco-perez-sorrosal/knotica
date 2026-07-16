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

import argparse
import sys
from dataclasses import dataclass
from typing import TextIO

__all__ = [
    "EXIT_ERROR",
    "EXIT_MIGRATION_AVAILABLE",
    "EXIT_MISUSE",
    "EXIT_NO_GOLDEN_SET",
    "EXIT_NOT_CONFIGURED",
    "EXIT_SUCCESS",
    "UNCONFIGURED_MESSAGE",
    "Console",
    "Status",
    "common_parent",
    "console_from_args",
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


def common_parent() -> argparse.ArgumentParser:
    """Return a parent parser carrying the flags every subcommand shares.

    Added via ``parents=[common_parent()]`` so each subcommand inherits the same
    ``--quiet``/``--verbose``/``--no-color``/``--no-input`` surface. ``--json``
    is *not* here -- it belongs only to the machine-parseable commands
    (``doctor``/``status``/``migrate``), so each of those adds it itself.
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="suppress informational output (errors still print to stderr)",
    )
    parent.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="emit debug context to stderr (never on by default)",
    )
    parent.add_argument(
        "--no-color",
        action="store_true",
        help="disable color (also auto-off when not a TTY, NO_COLOR, or TERM=dumb)",
    )
    parent.add_argument(
        "--no-input",
        action="store_true",
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
