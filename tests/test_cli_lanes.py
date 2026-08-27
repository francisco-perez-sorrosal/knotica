"""``knotica`` CLI: the six lane-nested command tree + the hidden top-level shims.

Six lane parser modules and the `DEPRECATED_TOP_LEVEL` shim table are the
production surface this file characterizes; neither exists yet (the paired
implementer step lands concurrently), so every assertion here is derived from
two things that *do* exist today -- `knotica.cli.COMMAND_NAMES` and each
still-shipping command module's own real `argparse` shape -- plus the lane
names in `knotica.core.process_model`, never a hand-copied mapping table.

RED-first, mirroring the project's convention for a paired concurrent step:
production symbols (`DEPRECATED_TOP_LEVEL`, the six lane modules) are resolved
lazily so collection stays green. Where the missing symbol is the whole point
of a test, that test fails explicitly and immediately (`pytest.fail`) rather
than raising a raw `ImportError`, so the failure names exactly what is
missing. Where a test drives the parser through commands that would only
exist post-implementation (a lane top-level name), the natural `SystemExit`
argparse raises for an unrecognized command is left uncaught -- pytest reports
it as a failure, and the message already says which name did not resolve.

Every runtime invocation goes through an unconfigured, HOME-redirected
environment (`unconfigured_env`): every touched command module gates on
`diagnose()` before doing anything else, so this reaches each shim's/lane's
real `run()` and observes its stdout/stderr contract without ever touching a
vault -- the "do not execute vault-touching handlers" constraint holds by
construction, not by mocking.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from knotica.cli.common import (
    EXIT_MIGRATION_AVAILABLE,
    EXIT_MISUSE,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
)
from knotica.cli import COMMAND_NAMES
from knotica.core import process_model

try:
    from knotica.cli import DEPRECATED_TOP_LEVEL as _DEPRECATED_TOP_LEVEL  # type: ignore[attr-defined]
except ImportError:
    _DEPRECATED_TOP_LEVEL = None


def _build_parser() -> argparse.ArgumentParser:
    """Build the exact parser tree ``main`` builds, without dispatching.

    Reuses the CLI's own private registration helper (documented in
    ``knotica/cli/__init__.py`` as the self-registration mechanism: for each
    name in ``COMMAND_NAMES`` it imports the module and calls
    ``module.configure(subparsers)``) rather than re-implementing it, so this
    test tracks the real registration path instead of a second copy of it.
    """
    from knotica.cli import _register_commands

    parser = argparse.ArgumentParser(prog="knotica")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _register_commands(subparsers)
    return parser


def _new_argv(old_tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    """Resolve ``old_tokens`` through ``DEPRECATED_TOP_LEVEL`` by longest-prefix match.

    A shim entry may key on a bare top-level word (``"doctor"``) or on a
    compound old invocation (``"compile promote"``) when a nested subcommand
    is individually re-targeted to a leaf other than its own top-level's
    default (``compile promote`` lands on ``improve promote``, not
    ``improve compile promote``). Preferring the longest match lets a
    compound override win over its own top-level's bare mapping; trailing
    tokens (flags, positionals) that were not part of the matched prefix are
    carried through unchanged.
    """
    if _DEPRECATED_TOP_LEVEL is None:
        return None
    for prefix_len in range(len(old_tokens), 0, -1):
        key = " ".join(old_tokens[:prefix_len])
        if key in _DEPRECATED_TOP_LEVEL:
            return tuple(_DEPRECATED_TOP_LEVEL[key]) + old_tokens[prefix_len:]
    return None


#: Today's real, still-shipping old invocations that Shape C moves under a
#: lane -- one entry per row this suite must prove still resolves and still
#: runs through its shim. Each tuple carries exactly the flags its own
#: module's current ``configure()`` requires to clear argparse validation
#: (verified against each module's source, not guessed), so parsing succeeds
#: and the unconfigured gate is reached before any vault read.
_MOVED_INVOCATIONS: tuple[tuple[str, ...], ...] = (
    ("eval", "--topic", "sample-topic"),
    ("loop", "--topic", "sample-topic"),
    # `compile` enforces `--topic` in `run()` rather than through argparse (the
    # nested `promote` sibling made a parser-level `required=True` impossible),
    # so the bare form exits `EXIT_MISUSE` before ever reaching the config gate
    # -- identically before and after this step. Supplying the topic is what
    # puts it in the same position as every other row here.
    ("compile", "--topic", "sample-topic"),
    (
        "compile",
        "promote",
        "--topic",
        "sample-topic",
        "--branch",
        "compile/sample-topic/abc123",
        "--dry-run",
    ),
    ("datasets", "bootstrap-train", "--topic", "sample-topic"),
    ("datasets", "freeze", "--topic", "sample-topic"),
    ("gapfill", "discover", "--topic", "sample-topic"),
    ("doctor",),
    ("doctor", "repair", "--dry-run"),
    ("okf", "check"),
    ("okf", "export", "--output", "bundle-out"),
    ("okf", "repair", "--dry-run"),
    ("migrate",),
    ("guillotine", "a contested claim", "--topic", "sample-topic"),
)

#: Infra commands Shape C never touches -- their own nested subcommands must
#: resolve exactly as they do today, with no shim and no new lane prefix.
_UNCHANGED_NESTED_INVOCATIONS: tuple[tuple[str, ...], ...] = (
    ("desktop", "install"),
    ("desktop", "status"),
    ("service", "install"),
    ("service", "uninstall"),
    ("service", "status"),
)


def _invocation_id(tokens: tuple[str, ...]) -> str:
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_command_names_narrows_to_the_six_lanes_plus_the_six_unlaned_commands() -> None:
    """`COMMAND_NAMES` becomes exactly the six process lanes plus the six
    commands Shape C keeps unlaned -- everything else is reachable only
    through a lane or a shim, never as a bare top-level entry."""
    unlaned = {"init", "desktop", "mcp", "status", "prompt", "service"}
    expected = unlaned | set(process_model.LANES)

    assert set(COMMAND_NAMES) == expected
    assert len(COMMAND_NAMES) == 12


def test_deprecated_top_level_table_is_declared() -> None:
    """The shim table that carries every renamed command's old name is missing."""
    assert _DEPRECATED_TOP_LEVEL is not None, (
        "knotica.cli.DEPRECATED_TOP_LEVEL is not defined yet -- the paired "
        "implementer step adds the old-name -> new-invocation shim table"
    )


# ---------------------------------------------------------------------------
# Resolution: every moved invocation still parses at its new lane depth.
# Parse-level only -- `run()` is never called here, so no vault is touched.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_tokens", _MOVED_INVOCATIONS, ids=_invocation_id)
def test_every_moved_invocation_still_resolves_at_its_new_lane_depth(
    old_tokens: tuple[str, ...],
) -> None:
    if _DEPRECATED_TOP_LEVEL is None:
        pytest.fail("DEPRECATED_TOP_LEVEL not yet defined -- cannot resolve the new location")
    new_argv = _new_argv(old_tokens)
    assert new_argv is not None, f"no shim entry resolves {_invocation_id(old_tokens)!r}"

    parser = _build_parser()
    # A leaf-specific flag (e.g. --branch on `promote`) only parses clean
    # against the parser that actually owns it -- successful parsing here is
    # itself the handler-identity proof, without ever calling run().
    namespace = parser.parse_args(list(new_argv))

    assert namespace.command == new_argv[0]


@pytest.mark.parametrize("tokens", _UNCHANGED_NESTED_INVOCATIONS, ids=_invocation_id)
def test_unlaned_nested_subcommands_are_unaffected_by_the_lane_registry_rewrite(
    tokens: tuple[str, ...],
) -> None:
    """`desktop`/`service` are infra; Shape C moves nothing here, so their own
    nested subcommands must resolve exactly as they do before this step."""
    parser = _build_parser()
    namespace = parser.parse_args(list(tokens))
    assert namespace.command == tokens[0]


# ---------------------------------------------------------------------------
# Shim behavior: warns on stderr, adds nothing to stdout, same exit code as
# the target it forwards to.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_tokens", _MOVED_INVOCATIONS, ids=_invocation_id)
def test_every_shimmed_old_invocation_warns_on_stderr_and_adds_nothing_to_stdout(
    old_tokens: tuple[str, ...],
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if _DEPRECATED_TOP_LEVEL is None:
        pytest.fail("DEPRECATED_TOP_LEVEL not yet defined -- cannot exercise its shim")
    from knotica.cli import main

    exit_code = main(list(old_tokens))
    captured = capsys.readouterr()

    assert captured.out == "", (
        "a shim must add nothing to stdout -- a --json consumer piping this "
        "command, or the session-start hook's 2>&1 capture, must see only "
        "the target command's own output"
    )
    assert captured.err != "", "a shimmed old name must warn on stderr"
    assert exit_code == EXIT_NOT_CONFIGURED, (
        "the shim must forward to the same terminal outcome the new "
        "invocation reaches -- here, the shared unconfigured gate"
    )


def test_no_shimmed_old_name_appears_in_top_level_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`help=argparse.SUPPRESS` keeps every shim invisible on day one."""
    if _DEPRECATED_TOP_LEVEL is None:
        pytest.fail("DEPRECATED_TOP_LEVEL not yet defined -- cannot check its help visibility")
    from knotica.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0

    out = capsys.readouterr().out
    old_top_level_names = {key.split()[0] for key in _DEPRECATED_TOP_LEVEL}
    for name in old_top_level_names:
        assert re.search(rf"\b{re.escape(name)}\b", out) is None, (
            f"{name!r} is a shimmed old name and must not appear in --help output"
        )


def test_a_flag_between_compound_shim_words_gets_a_corrective_hint_on_stderr(
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A flag between the two words of a compound old form hides it from the
    prefix matcher, so the single-token rewrite fires and argparse rejects the
    leftover token. That failure is acceptable only if the user is told the
    command they probably meant -- on stderr, with stdout untouched."""
    from knotica.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["compile", "--quiet", "promote", "--topic", "t"])
    assert excinfo.value.code == EXIT_MISUSE

    captured = capsys.readouterr()
    assert captured.out == "", "the corrective hint must never leak onto stdout"
    assert "if you meant 'compile promote'" in captured.err
    assert "knotica improve promote" in captured.err


def test_a_flag_value_that_matches_a_compound_word_is_not_rewritten_as_one(
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--topic promote` carries `promote` as a VALUE; the matcher must keep
    treating the invocation as the single-token `compile` rewrite rather than
    guessing a compound form out of a flag's argument."""
    from knotica.cli import main

    exit_code = main(["compile", "--topic", "promote"])
    captured = capsys.readouterr()
    assert exit_code == EXIT_NOT_CONFIGURED
    assert captured.out == ""
    assert "'compile' has moved" in captured.err


# ---------------------------------------------------------------------------
# The two additions: `learn` / `answer` guide, never misuse.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["learn", "answer"])
def test_conversational_lane_entry_exits_success_with_guidance_on_stdout(
    name: str,
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Asking where a lane's commands live is guidance, not misuse."""
    from knotica.cli import main

    exit_code = main([name])

    assert exit_code == EXIT_SUCCESS
    assert exit_code != EXIT_MISUSE
    out = capsys.readouterr().out
    assert out.strip() != "", f"`knotica {name}` must print guidance, not stay silent"


# ---------------------------------------------------------------------------
# `home`: exits 0 unconditionally, signals emptiness via empty stdout.
# ---------------------------------------------------------------------------


def test_home_exits_success_unconditionally_even_when_unconfigured(
    unconfigured_env: Path,
) -> None:
    """Every other command exits `EXIT_NOT_CONFIGURED` when unconfigured;
    `home` does not -- an inbox with nothing configured yet is still a valid,
    successful (empty) inbox, not an error state."""
    from knotica.cli import main

    exit_code = main(["home"])

    assert exit_code == EXIT_SUCCESS


def test_home_signals_emptiness_via_empty_stdout_not_a_nonzero_exit(
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from knotica.cli import main

    exit_code = main(["home"])

    assert exit_code == EXIT_SUCCESS
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# `status --nudge`: untouched by Shape C, so its hook-facing contract must
# survive the registry rewrite byte for byte.
# ---------------------------------------------------------------------------


def test_status_nudge_stays_empty_on_stdout_when_unconfigured(
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`status` is unlaned and untouched by this step; the SessionStart hook
    captures this command's stdout directly and only echoes it when
    non-empty, so an unconfigured environment must still print nothing here."""
    from knotica.cli import main

    exit_code = main(["status", "--nudge"])

    assert exit_code == EXIT_NOT_CONFIGURED
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Exit codes are a stable contract, independent of the registry rewrite.
# ---------------------------------------------------------------------------


def test_exit_code_constants_are_unchanged_by_the_registry_rewrite() -> None:
    assert EXIT_SUCCESS == 0
    assert EXIT_MISUSE == 2
    assert EXIT_MIGRATION_AVAILABLE == 4


def test_no_subcommand_still_exits_misuse_after_the_registry_rewrite() -> None:
    from knotica.cli import main

    assert main([]) == EXIT_MISUSE
