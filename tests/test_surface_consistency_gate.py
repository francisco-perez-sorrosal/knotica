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
    Path(".ai-state") / "DESIGN.md",
    # Check 3 reports these two missing rather than skipping them, so a case
    # asserting the *pass* outcome needs both present or it fails for a reason
    # it never injected.
    Path("hooks") / "session_start.sh",
    Path("skills") / "wiki-maintenance" / "SKILL.md",
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal copy of the tree the gate reads, safe to corrupt."""
    for relative in _NEEDED:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    # The whole of `docs/` rather than `reference.md` alone: check 4 resolves
    # published call-forms across the tree, so copying one file would make every
    # case here report a missing-document finding it never meant to inject.
    shutil.copytree(REPO_ROOT / "docs", tmp_path / "docs")
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
        lambda text: text.replace("| `fill discover` |", "| `ghost discover` |", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "CLI subcommand 'fill' exists but is absent" in result.stderr


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


# ---------------------------------------------------------------------------
# Check 4 -- published call-forms in the rest of docs/ and in DESIGN.md
# ---------------------------------------------------------------------------


def test_a_dead_call_form_in_a_doc_other_than_the_reference_is_rejected(tree: Path) -> None:
    """The `gap-fill.md` defect: a dispatcher the lanes absorbed, published as a call.

    Checks 1-2 read `reference.md` and nothing else, so this shape sat wrong in
    four sibling documents while the gate reported green.
    """
    _edit(
        tree,
        "docs/gap-fill.md",
        lambda text: text.replace("`fill action=gaps_read`", "`gaps_read action=open`", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "is not a registered tool" in result.stderr


def test_an_action_a_live_lane_does_not_declare_is_rejected(tree: Path) -> None:
    _edit(
        tree,
        "docs/gap-fill.md",
        lambda text: text.replace("`fill action=gaps_read`", "`fill action=gaps_reed`", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "has no action" in result.stderr


def test_a_dead_call_form_in_the_design_canon_is_rejected(tree: Path) -> None:
    """`DESIGN.md` § 4 is the canon an agent resolves "what does this expose?" against.

    It declared a 35-tool surface two sections after `DESIGN.md` § 3b correctly said 21, and
    no check read it.
    """
    _edit(
        tree,
        ".ai-state/DESIGN.md",
        lambda text: text.replace(
            "`improve action=loop loop_action=run_eval`", "`loop action=run_eval`", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert ".ai-state/DESIGN.md" in result.stderr


def test_a_lane_verb_offered_as_a_bare_call_is_rejected(tree: Path) -> None:
    """`create_topic` is reachable only as an action; offering it bare is a dead end."""
    _edit(
        tree,
        "docs/tutorial.md",
        lambda text: text.replace(
            "Claude calls `learn action=create_topic`.", "Claude calls `create_topic`.", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "reachable only as" in result.stderr


def test_a_verb_named_as_a_subject_rather_than_a_call_is_not_flagged(tree: Path) -> None:
    """The rule that keeps the gate usable: prose names verbs constantly.

    Without the call-position restriction this exact sentence -- and roughly a
    hundred and fifty like it across `docs/` -- reported as drift. A gate that
    fires on correct prose gets muted, so this case is as load-bearing as the
    four above.
    """
    _edit(
        tree,
        "docs/gap-fill.md",
        lambda text: text + "\n\nThe `gaps_read` verb reads the queue; `datasets` seals it.\n",
    )

    assert _run(tree).returncode == 0


def test_a_history_marked_region_may_publish_a_dead_name(tree: Path) -> None:
    """A migration table must print the dead name -- that is its whole job."""
    _edit(
        tree,
        "docs/gap-fill.md",
        lambda text: (
            text
            + "\n<!-- surface-history-begin: v0.2.0 migration -->\n"
            + "Was `loop action=run_eval`.\n"
            + "<!-- surface-history-end -->\n"
        ),
    )

    assert _run(tree).returncode == 0


# ---------------------------------------------------------------------------
# Fenced blocks and `allowed-tools:` frontmatter -- code position the inline
# backtick rule cannot see
# ---------------------------------------------------------------------------


def test_a_stale_invocation_inside_a_fenced_block_is_rejected(tree: Path) -> None:
    """The blind spot itself: a command's canonical invocation lives in one fence.

    `commands/guillotine.md` really did publish `knotica guillotine` here, months
    after the CLI nested its lanes, with this gate green -- the scanner matched
    inline spans only, and a slash command's one executable line is never one.
    """
    _edit(
        tree,
        "commands/guillotine.md",
        lambda text: text.replace('knotica tend guillotine "$1"', 'knotica guillotine "$1"', 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "`knotica guillotine` is not a CLI subcommand" in result.stderr


def test_a_stale_allowed_tools_entry_is_rejected(tree: Path) -> None:
    """The same command's frontmatter carried the same dead form, equally unscanned."""
    _edit(
        tree,
        "commands/guillotine.md",
        lambda text: text.replace(
            "Bash(knotica tend guillotine:*)", "Bash(knotica guillotine:*)", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "`knotica guillotine` is not a CLI subcommand" in result.stderr


def test_a_dead_call_form_in_a_fenced_block_of_a_doc_is_rejected(tree: Path) -> None:
    """Check 4 reads fences too: a doc's canonical call-form usually lives in one."""
    _edit(
        tree,
        "docs/gap-fill.md",
        lambda text: text + "\n```text\nloop action=run_eval\n```\n",
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "is not a registered tool" in result.stderr


def test_an_output_shaped_fence_is_not_flagged(tree: Path) -> None:
    """The rule that keeps the widening usable: a fence often carries output.

    A bare identifier inside one is as likely to be a JSON key as a tool name, so
    only the *call-form* rules run over a fence -- never the bare-identifier rule
    that resolves `compile_run` against the dispatcher tables.
    """
    _edit(
        tree,
        "commands/status.md",
        lambda text: text + '\n```json\n{"compile_run": 3, "loop_run_once": 1}\n```\n',
    )

    assert _run(tree).returncode == 0


def test_a_history_marked_region_may_publish_a_dead_invocation_in_a_fence(tree: Path) -> None:
    """The marker keeps working now that the region can contain executable lines."""
    _edit(
        tree,
        "commands/guillotine.md",
        lambda text: (
            text
            + "\n<!-- surface-history-begin: v0.2.0 migration -->\n"
            + "```\nknotica guillotine <claim>\n```\n"
            + "<!-- surface-history-end -->\n"
        ),
    )

    assert _run(tree).returncode == 0


def test_the_history_marker_is_scoped_to_its_own_region(tree: Path) -> None:
    """It exempts a region, never the file -- otherwise one marker silences a document."""
    _edit(
        tree,
        "docs/gap-fill.md",
        lambda text: (
            text
            + "\n<!-- surface-history-begin: v0.2.0 migration -->\n"
            + "Was `loop action=run_eval`.\n"
            + "<!-- surface-history-end -->\n\n"
            + "Call `datasets action=freeze` to seal it.\n"
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "is not a registered tool" in result.stderr
