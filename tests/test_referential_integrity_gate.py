"""Dead tool names must not survive in prose a model reads.

`commands/setup.md` told operators to call a `compile_run` tool for roughly three
weeks after `dec-045`/`dec-050` folded it into the `compile` dispatcher. Nothing
caught it, because nothing checked prose against the registry — the reference
tables were gated, and every *other* place a name is published was not.

The C4-shaped case below is the point of this file: inject a consolidated-away
tool name into a command body and the gate must reject it. The rest of the cases
cover the other three surfaces the same defect can hide in, and — just as
important — pin the shapes that must **not** be flagged. An earlier draft of the
gate reported 17 findings on a clean tree by reading "a knotica vault" as an
invocation of a `vault` subcommand; the ordinary-English cases here exist so that
cannot come back.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = Path("scripts") / "check_surface_consistency.py"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of every surface the gate reads, safe to corrupt."""
    for relative in (
        Path("scripts") / "check_surface_consistency.py",
        Path("docs") / "reference.md",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    for directory in ("commands", "hooks", "skills"):
        shutil.copytree(REPO_ROOT / directory, tmp_path / directory)
    # The gate reads description=/fix= prose out of src/ by AST.
    shutil.copytree(
        REPO_ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    return tmp_path


def _run(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell, test-local paths
        [sys.executable, str(tree / GATE)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _edit(tree: Path, relative: str, rewrite: Callable[[str], str]) -> None:
    path = tree / relative
    original = path.read_text(encoding="utf-8")
    updated = rewrite(original)
    assert updated != original, f"the injection did not change {relative} — the test is vacuous"
    path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# The regression this gate exists for
# ---------------------------------------------------------------------------


def test_a_command_body_naming_a_consolidated_away_tool_is_rejected(tree: Path) -> None:
    """C4, reproduced: `compile_run` in a command body, exactly as it shipped."""
    _edit(
        tree,
        "commands/setup.md",
        lambda text: text.replace(
            "`improve action=compile compile_action=run`", "`compile_run`", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "`compile_run` names no registered tool" in result.stderr
    assert "`compile action=run`" in result.stderr, "the finding must name the replacement"


def test_the_same_body_passes_once_the_dead_name_is_removed(tree: Path) -> None:
    """The mirror of the case above — otherwise it proves only that the gate fails."""
    result = _run(tree)

    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# One injected failure per surface
# ---------------------------------------------------------------------------


def test_a_dead_tool_name_in_a_description_string_is_rejected(tree: Path) -> None:
    """The surface a model actually reads when choosing a tool.

    The injection anchors on the first words of `_LOOP_DISPATCH_DESCRIPTION`.
    That opening was reworded when the description corpus was collapsed into the
    lane action tables, so the anchor moved with it -- the rule under test (a
    dead `<dispatcher>_<action>` name inside a `description=` string fails the
    gate) is unchanged, and `_edit` asserts the injection actually landed, so a
    future reword fails loudly here rather than silently passing a vacuous test.
    """
    _edit(
        tree,
        "src/knotica/mcp_server/tools_dispatch_loop.py",
        lambda text: text.replace(
            '"Run and steer the self-improvement gate',
            '"Same as `loop_run_once`. Run and steer the self-improvement gate',
            1,
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "`loop_run_once` names no registered tool" in result.stderr


def test_a_fix_string_naming_a_nonexistent_action_is_rejected(tree: Path) -> None:
    """The live-defect class this gate found, re-aimed at a lane.

    The original injection named `golden action=freeze`; `golden` stopped being
    a registered tool when the lanes absorbed it, so that string now trips the
    *absorbed-dispatcher* arm instead and would no longer exercise this rule.
    A lane selector is the live equivalent: `improve` is registered and
    `freeze` is a `datasets` sub-action, not one of `improve`'s own.
    """
    _edit(
        tree,
        "src/knotica/core/arena_eval.py",
        lambda text: text.replace("`improve action=datasets", "`improve action=freeze", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "'improve' has no such action" in result.stderr
    assert "datasets" in result.stderr, "the finding must name the valid actions"


def test_a_dead_cli_invocation_in_the_session_hook_is_rejected(tree: Path) -> None:
    _edit(
        tree,
        "hooks/session_start.sh",
        lambda text: text.replace("knotica tend doctor", "knotica frobnicate", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "`knotica frobnicate` is not a CLI subcommand" in result.stderr


def test_a_dead_slash_command_in_the_session_hook_is_rejected(tree: Path) -> None:
    _edit(
        tree,
        "hooks/session_start.sh",
        lambda text: text.replace("/knotica:migrate", "/knotica:ghost", 1),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "`/knotica:ghost` ships no command file" in result.stderr


def test_a_tool_named_in_the_skill_but_not_in_the_server_instructions_is_rejected(
    tree: Path,
) -> None:
    """Two independently maintained copies of one routing contract."""
    _edit(
        tree,
        "skills/wiki-maintenance/SKILL.md",
        lambda text: text.replace(
            "load via read_protocol", "load via read_protocol, note_capture", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 1
    assert "'note_capture'" in result.stderr
    assert "_INSTRUCTIONS" in result.stderr


# ---------------------------------------------------------------------------
# What must NOT be flagged — the false positives that broke the first draft
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prose",
    [
        "Create a knotica vault at the given path.",
        "Your knotica wiki stays a git repo.",
        "Configure knotica so that the loop runs nightly.",
        "Use knotica in Chat via Claude Desktop.",
    ],
)
def test_knotica_used_adjectivally_in_prose_is_not_an_invocation(tree: Path, prose: str) -> None:
    """`knotica <word>` is only an invocation in code position, never in a sentence."""
    _edit(tree, "commands/setup.md", lambda text: text + f"\n\n{prose}\n")

    result = _run(tree)

    assert result.returncode == 0, result.stderr


def test_a_parameter_or_config_key_sharing_a_tool_prefix_is_not_flagged(tree: Path) -> None:
    """`eval_window` and `confirm_nonce` are arguments; `golden_review` is a module."""
    _edit(
        tree,
        "commands/setup.md",
        lambda text: text + "\n\nPass `eval_window`, `confirm_nonce`, or read `golden_review`.\n",
    )

    result = _run(tree)

    assert result.returncode == 0, result.stderr


def test_an_ordinary_english_tool_word_in_the_skill_description_is_not_drift(tree: Path) -> None:
    """ "the self-improvement loop" is prose; `loop` the dispatcher is not being named."""
    _edit(
        tree,
        "skills/wiki-maintenance/SKILL.md",
        lambda text: text.replace(
            "the self-improvement loop", "the self-improvement loop, the vault, and notes", 1
        ),
    )

    result = _run(tree)

    assert result.returncode == 0, result.stderr


def test_a_shell_comment_mentioning_knotica_is_not_an_invocation(tree: Path) -> None:
    """The hook's own prose says "knotica is not configured"; `is` is not a subcommand."""
    _edit(
        tree,
        "hooks/session_start.sh",
        lambda text: text + "\n# note: knotica is not configured until init runs\n",
    )

    result = _run(tree)

    assert result.returncode == 0, result.stderr
