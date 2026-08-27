"""``knotica --help``: two labelled groups (lanes, setup/primitives), EXAMPLES first.

Group membership is asserted against the live registry (``COMMAND_NAMES`` +
``process_model.LANES``), never a hand-copied list -- a lane renamed at the
declaration must not silently desync this test from the real surface.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from knotica.cli import COMMAND_NAMES, DEPRECATED_TOP_LEVEL, main
from knotica.cli.common import EXIT_NOT_CONFIGURED
from knotica.core import process_model

_LANE_NAMES = set(process_model.LANES)
_SETUP_NAMES = set(COMMAND_NAMES) - _LANE_NAMES


@pytest.fixture
def help_text(capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# Two labelled groups, derived from the live registry.
# ---------------------------------------------------------------------------


def test_lanes_and_setup_partition_command_names_with_no_overlap() -> None:
    """The two groups are a partition of ``COMMAND_NAMES`` -- no name in both,
    none left out."""
    assert _LANE_NAMES | _SETUP_NAMES == set(COMMAND_NAMES)
    assert _LANE_NAMES.isdisjoint(_SETUP_NAMES)


def test_help_has_an_examples_block_before_the_two_groups(help_text: str) -> None:
    lanes_at = help_text.index("LANES")
    setup_at = help_text.index("SETUP")
    examples_at = help_text.index("EXAMPLES")
    assert examples_at < lanes_at < setup_at


@pytest.mark.parametrize("name", sorted(_LANE_NAMES))
def test_every_lane_name_appears_in_the_lanes_group_only(name: str, help_text: str) -> None:
    lanes_block = help_text[help_text.index("LANES") : help_text.index("SETUP")]
    setup_block = help_text[help_text.index("SETUP") :]
    assert re.search(rf"^\s+{re.escape(name)}\b", lanes_block, re.MULTILINE) is not None, (
        f"lane {name!r} must be listed in the LANES group"
    )
    assert re.search(rf"^\s+{re.escape(name)}\b", setup_block, re.MULTILINE) is None, (
        f"lane {name!r} must not also be listed in the SETUP group"
    )


@pytest.mark.parametrize("name", sorted(_SETUP_NAMES))
def test_every_setup_name_appears_in_the_setup_group_only(name: str, help_text: str) -> None:
    lanes_block = help_text[help_text.index("LANES") : help_text.index("SETUP")]
    setup_block = help_text[help_text.index("SETUP") :]
    assert re.search(rf"^\s+{re.escape(name)}\b", setup_block, re.MULTILINE) is not None, (
        f"setup command {name!r} must be listed in the SETUP group"
    )
    assert re.search(rf"^\s+{re.escape(name)}\b", lanes_block, re.MULTILINE) is None, (
        f"setup command {name!r} must not also be listed in the LANES group"
    )


# ---------------------------------------------------------------------------
# No shim leaks into the top-level help.
# ---------------------------------------------------------------------------


def test_no_shimmed_name_appears_anywhere_in_top_level_help(help_text: str) -> None:
    """Every ``DEPRECATED_TOP_LEVEL`` first token is ``help=argparse.SUPPRESS``-ed
    and never registered as a parser -- it must not appear anywhere in the
    rendered help, including the EXAMPLES block (a hand-authored string, the
    one place an old name could leak in by accident)."""
    old_top_level_names = {key.split()[0] for key in DEPRECATED_TOP_LEVEL}
    for name in old_top_level_names:
        assert re.search(rf"\b{re.escape(name)}\b", help_text) is None, (
            f"{name!r} is a shimmed old top-level name and must not appear in --help output"
        )


# ---------------------------------------------------------------------------
# The nudge exit-code contract survives the --help rework untouched.
# ---------------------------------------------------------------------------


def test_status_nudge_exit_code_and_emptiness_contract_is_unaffected_by_help_grouping(
    unconfigured_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`status --nudge` is unlaned and untouched by this step. Its established
    contract (verified against ``cli/status.py::run`` and pinned by
    ``tests/test_cli_lanes.py``) is: exit ``EXIT_NOT_CONFIGURED`` when nothing
    is configured, with emptiness signalled by empty stdout either way -- the
    SessionStart hook only branches on stdout being non-empty
    (``hooks/session_start.sh:100`` does not inspect the exit code at all)."""
    exit_code = main(["status", "--nudge"])
    captured = capsys.readouterr()

    assert exit_code == EXIT_NOT_CONFIGURED
    assert captured.out == ""
