"""The surface gate must bite, and it must bite on the divergences that happened.

A gate is only worth the failures it can produce. This one guards three published
surfaces against the code that publishes them, so every check here injects a
divergence into a *copy* of the tree and asserts the gate rejects it — the pass
case alone would go green just as happily if the checks were commented out.

The injections are not hypothetical. `docs/reference.md` really did say "33
tools" while 35 were registered, and `commands/setup.md` really did tell
operators to call a `compile_run` tool that `dec-045`/`dec-050` had removed. Both
survived weeks of review, which is the base rate this file exists to change.

Each case runs the gate as a subprocess against a temporary repo copy, because
that is how `make verify` runs it: exit code and stderr are the whole contract,
and asserting on them keeps the test honest about what a developer will see.
"""

from __future__ import annotations

import shutil
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = Path("scripts") / "check_surface_consistency.py"

#: Only what the gate reads. Copying the whole repo would drag `.venv`, `.git`
#: and `node_modules` into every case for nothing.
_NEEDED = (
    Path("scripts") / "check_surface_consistency.py",
    Path("docs") / "reference.md",
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal copy of the tree the gate reads, safe to corrupt."""
    for relative in _NEEDED:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    shutil.copytree(REPO_ROOT / "commands", tmp_path / "commands")
    return tmp_path


def _run(tree: Path) -> subprocess.CompletedProcess[str]:
    """Run the gate against ``tree`` exactly as ``make verify`` does."""
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, test-local paths
        [sys.executable, str(tree / GATE)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _edit(tree: Path, relative: str, rewrite: Callable[[str], str]) -> None:
    """Rewrite one file in the copied tree."""
    path = tree / relative
    original = path.read_text(encoding="utf-8")
    updated = rewrite(original)
    assert updated != original, f"the injection did not change {relative} — the test is vacuous"
    path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# The pass case
# ---------------------------------------------------------------------------


def test_the_live_tree_passes_the_gate() -> None:
    """The drift the gate was written to expose is fixed, and stays fixed."""
    result = _run(REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert "surface consistency check OK" in result.stdout


def test_the_gate_reports_what_it_checked_rather_than_only_that_it_passed() -> None:
    """A green line naming three counts is auditable; a bare 'OK' is not."""
    result = _run(REPO_ROOT)

    # The integer moves with the surface (the lane rename adds six dispatchers,
    # then removes the flat tools they absorb), so this asserts the *shape* of
    # the report -- a count is named -- not one particular count.
    assert re.search(r"OK — \d+ tools", result.stdout), result.stdout
    assert "CLI subcommands" in result.stdout
    assert "slash commands" in result.stdout


# ---------------------------------------------------------------------------
# Injected divergences — one per surface the gate guards
# ---------------------------------------------------------------------------


def test_a_registered_tool_absent_from_every_table_is_rejected(tree: Path) -> None:
    """The defect that actually shipped: the server grew and the doc did not."""
    _edit(
        tree,
        "docs/reference.md",
        lambda text: text.replace("| `note_capture` |", "| `note_capture_REMOVED_BY_TEST` |", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "'note_capture' is registered but appears in no reference table" in result.stderr


def test_a_table_row_naming_a_dead_tool_is_rejected(tree: Path) -> None:
    """The `compile_run` class of defect: a name the server stopped registering."""
    _edit(
        tree,
        "docs/reference.md",
        lambda text: text.replace(
            "| `list_topics` |", "| `compile_run` | — | — |\n| `list_topics` |", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "lists 'compile_run', which the server does not register" in result.stderr


def test_a_command_names_entry_absent_from_the_cli_table_is_rejected(tree: Path) -> None:
    """`COMMAND_NAMES` is the one declaration of the subcommand set; the doc follows it."""
    _edit(
        tree,
        "docs/reference.md",
        lambda text: text.replace("| `guillotine <claim>` |", "| `ghost <claim>` |", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "CLI subcommand 'guillotine' exists but is absent" in result.stderr


def test_a_shipped_slash_command_absent_from_the_alias_table_is_rejected(tree: Path) -> None:
    """A shipped alias nobody documented is an alias nobody discovers."""
    _edit(
        tree,
        "docs/reference.md",
        lambda text: text.replace("| `/knotica:guillotine` |", "| `/knotica:ghost` |", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "slash command 'guillotine' exists but is absent" in result.stderr
    assert "lists slash command 'ghost', which does not exist" in result.stderr


def test_an_alias_table_row_with_no_command_file_is_rejected(tree: Path) -> None:
    """The mirror direction: a documented alias the plugin does not actually ship."""
    (tree / "commands" / "guillotine.md").unlink()

    result = _run(tree)

    assert result.returncode == 1
    assert "lists slash command 'guillotine', which does not exist" in result.stderr


# ---------------------------------------------------------------------------
# The published integers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "corrupted", "expected"),
    [
        ("21 tools are registered", "20 tools are registered", "says total=20, tables say 21"),
        ("and 14 flat,", "and 13 flat,", "says flat=13, tables say 14"),
        ("(4 read + 3 write + 7", "(5 read + 3 write + 7", "says read=5, tables say 4"),
        ("(4 read + 3 write + 7", "(4 read + 3 write + 6", "says other=6, tables say 7"),
    ],
)
def test_each_summary_integer_is_checked_against_the_tables(
    tree: Path, original: str, corrupted: str, expected: str
) -> None:
    """Every integer in the summary is derived, so every one of them can be wrong."""
    _edit(tree, "docs/reference.md", lambda text: text.replace(original, corrupted, 1))

    result = _run(tree)

    assert result.returncode == 1
    assert expected in result.stderr


def test_a_section_heading_that_miscounts_its_own_table_is_rejected(tree: Path) -> None:
    """The heading class of defect the live tree once carried: a miscounted section."""
    _edit(
        tree,
        "docs/reference.md",
        lambda text: text.replace("### Other flat tools — 7", "### Other flat tools — 5", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "'### Other flat tools — 5' heading disagrees with its own table (7 rows)" in (
        result.stderr
    )


# ---------------------------------------------------------------------------
# Fail-closed on its own input
# ---------------------------------------------------------------------------


def test_a_reworded_summary_fails_rather_than_silently_skipping(tree: Path) -> None:
    """The failure mode that would make every check above worthless.

    If the sentence the gate parses is reworded, the honest outcome is a failure
    naming the regex to update. A gate that shrugs and passes when it can no
    longer find its input reports green for a surface nobody is checking.
    """
    _edit(
        tree,
        "docs/reference.md",
        lambda text: text.replace("tools are registered on the server:", "tools ship:", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "no longer matches the shape this gate parses" in result.stderr


def test_a_missing_reference_document_fails_rather_than_passing_vacuously(tree: Path) -> None:
    (tree / "docs" / "reference.md").unlink()

    result = _run(tree)

    assert result.returncode == 1
    assert "is missing" in result.stderr
