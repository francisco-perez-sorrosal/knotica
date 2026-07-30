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

Two of the six modules below (``core/gapfill.py`` and ``guillotine/report.py``)
were over the ceiling without any ledger row tracking them -- discovered only by
measuring every file rather than trusting the recorded set. That is the argument
for a mechanical check over a hand-maintained list.
"""

from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "knotica"

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


def _line_counts() -> dict[str, int]:
    """Every ``src/knotica`` module by POSIX-relative path, with its line count."""
    return {
        path.relative_to(SRC_ROOT).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(SRC_ROOT.rglob("*.py"))
    }


def test_no_baseline_module_grows_beyond_its_recorded_high_water_mark() -> None:
    counts = _line_counts()
    grown = {
        module: (counts[module], baseline)
        for module, baseline in OVER_CEILING_BASELINE.items()
        if module in counts and counts[module] > baseline
    }
    assert not grown, (
        "A module already over the 800-line ceiling grew further. Extract instead of "
        "appending, or -- if the growth is genuinely unavoidable -- raise its baseline "
        "in the same commit and say why in the message. "
        f"module -> (now, allowed): {grown}"
    )


def test_no_module_outside_the_baseline_exceeds_the_ceiling() -> None:
    counts = _line_counts()
    offenders = {
        module: lines
        for module, lines in counts.items()
        if lines > LINE_CEILING and module not in OVER_CEILING_BASELINE
    }
    assert not offenders, (
        f"A module crossed the {LINE_CEILING}-line ceiling. The exemption list is closed: "
        "split by cohesion rather than adding an entry. "
        f"module -> lines: {offenders}"
    )


def test_baseline_has_no_stale_entries() -> None:
    counts = _line_counts()
    missing = sorted(module for module in OVER_CEILING_BASELINE if module not in counts)
    assert not missing, f"Baseline names modules that no longer exist; drop them: {missing}"
    paid_down = {
        module: counts[module] for module in OVER_CEILING_BASELINE if counts[module] <= LINE_CEILING
    }
    assert not paid_down, (
        "A baseline module is now within the ceiling -- remove its entry so the "
        "exemption cannot silently return. "
        f"module -> lines: {paid_down}"
    )
