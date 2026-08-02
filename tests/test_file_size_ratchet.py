"""File-size ratchet: the over-ceiling set may shrink, never grow.

The project's coding conventions target 200-400 line modules with a hard
ceiling of 800. Six modules already exceed it, for historical reasons that are
recorded in the tech-debt ledger. Splitting them is a large, risky refactor --
``evals/harness.py`` is the eval instrument, and ``core/loop.py`` is the most
carefully adversarially-reviewed code in the tree -- so this test does not
demand that work. It bounds it.

Three rules, which together turn unbounded growth into a debt that can only be
paid down:

1. A module in :data:`OVER_CEILING_BASELINE` may shrink but never grow. The
   recorded number is a high-water mark, not a target.
2. A module absent from the baseline may never exceed :data:`LINE_CEILING`. New
   code complies from birth; the exemption list is closed.
3. A baseline entry that has fallen to the ceiling must be *removed* from the
   baseline. Without this the list silently accumulates stale exemptions and the
   ratchet stops ratcheting.

Two of the six source modules below (``core/gapfill.py`` and
``guillotine/report.py``) were over the ceiling without any ledger row tracking
them -- discovered only by measuring every file rather than trusting the
recorded set. That is the argument for a mechanical check over a hand-maintained
list, and it is the argument that extended this ratchet to ``tests/``.

**``tests/`` is scanned because it was the same blind spot, one directory over.**
The ratchet originally measured ``src/knotica`` only, so the 800-line ceiling
silently did not apply to the test tree -- and eleven test modules had crossed
it unnoticed, the largest at more than three times the ceiling. A ceiling that
only applies where someone remembered to look is not a ceiling. The same three
rules now govern both roots; test modules are held to the identical standard as
the code they exercise, because a 2500-line test file is exactly as hard to
navigate as a 2500-line module.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "knotica"
TESTS_ROOT = REPO_ROOT / "tests"

#: The project's hard per-module line ceiling.
LINE_CEILING = 800

#: Modules already over the ceiling, at their measured high-water mark. Each may
#: shrink freely; none may grow. Removing an entry is the goal -- see rule 3.
#: Paths are POSIX, relative to ``src/knotica``.
OVER_CEILING_BASELINE: dict[str, int] = {
    "evals/harness.py": 1214,
    "core/loop.py": 1146,
    "evals/golden.py": 975,
    "core/records.py": 947,
    "core/gapfill.py": 938,
    "guillotine/report.py": 847,
}


#: Test modules already over the ceiling, at their measured high-water mark.
#: Recorded when the ratchet was extended to ``tests/``; same rules as above.
TESTS_OVER_CEILING_BASELINE: dict[str, int] = {
    "test_mcp_notes.py": 2506,
    "test_evals_harness.py": 1565,
    "core/notes/test_anchor.py": 1534,
    "core/notes/test_resolve.py": 1489,
    "test_evals_golden.py": 1372,
    "core/notes/test_reanchor_note.py": 1112,
    "test_search.py": 1086,
    "core/notes/test_capture_note.py": 950,
    "core/notes/test_store.py": 938,
    "test_transaction.py": 847,
    "test_cli_eval.py": 809,
}

#: The scanned trees, each with its own baseline. Parametrising the rules over
#: this rather than duplicating them is what keeps the two roots from drifting
#: apart -- a rule added for one is a rule added for both.
SCANNED_TREES = (
    pytest.param(SRC_ROOT, OVER_CEILING_BASELINE, id="src"),
    pytest.param(TESTS_ROOT, TESTS_OVER_CEILING_BASELINE, id="tests"),
)


def _line_counts(root: Path) -> dict[str, int]:
    """Every module under ``root`` by POSIX-relative path, with its line count."""
    return {
        path.relative_to(root).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(root.rglob("*.py"))
    }


@pytest.mark.parametrize(("root", "baseline"), SCANNED_TREES)
def test_no_baseline_module_grows_beyond_its_recorded_high_water_mark(
    root: Path, baseline: dict[str, int]
) -> None:
    counts = _line_counts(root)
    grown = {
        module: (counts[module], recorded)
        for module, recorded in baseline.items()
        if module in counts and counts[module] > recorded
    }
    assert not grown, (
        "A module already over the 800-line ceiling grew further. Extract instead of "
        "appending, or -- if the growth is genuinely unavoidable -- raise its baseline "
        "in the same commit and say why in the message. "
        f"module -> (now, allowed): {grown}"
    )


@pytest.mark.parametrize(("root", "baseline"), SCANNED_TREES)
def test_no_module_outside_the_baseline_exceeds_the_ceiling(
    root: Path, baseline: dict[str, int]
) -> None:
    counts = _line_counts(root)
    offenders = {
        module: lines
        for module, lines in counts.items()
        if lines > LINE_CEILING and module not in baseline
    }
    assert not offenders, (
        f"A module crossed the {LINE_CEILING}-line ceiling. The exemption list is closed: "
        "split by cohesion rather than adding an entry. "
        f"module -> lines: {offenders}"
    )


@pytest.mark.parametrize(("root", "baseline"), SCANNED_TREES)
def test_baseline_has_no_stale_entries(root: Path, baseline: dict[str, int]) -> None:
    counts = _line_counts(root)
    missing = sorted(module for module in baseline if module not in counts)
    assert not missing, f"Baseline names modules that no longer exist; drop them: {missing}"
    paid_down = {module: counts[module] for module in baseline if counts[module] <= LINE_CEILING}
    assert not paid_down, (
        "A baseline module is now within the ceiling -- remove its entry so the "
        "exemption cannot silently return. "
        f"module -> lines: {paid_down}"
    )
