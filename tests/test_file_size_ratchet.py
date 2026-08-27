"""File-size ratchet: the over-ceiling set may shrink, never grow.

The project's coding conventions target 200-400 line modules with a hard
ceiling of 800. Five modules already exceed it, for historical reasons that are
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

Two source modules (``core/gapfill.py`` and ``guillotine/report.py``) were over
the ceiling without any ledger row tracking them -- discovered only by measuring
every file rather than trusting the recorded set. ``guillotine/report.py`` has
since fallen under the ceiling (td-036 deleted a dead pre-transaction writer from
it) and its entry is gone: a paid-down exemption is removed, never kept. That is the argument for a mechanical check over a hand-maintained
list, and it is the argument that extended this ratchet to ``tests/``.

**``tests/`` is scanned because it was the same blind spot, one directory over.**
The ratchet originally measured ``src/knotica`` only, so the 800-line ceiling
silently did not apply to the test tree -- and eleven test modules had crossed
it unnoticed, the largest at more than three times the ceiling. A ceiling that
only applies where someone remembered to look is not a ceiling. The same three
rules now govern both roots; test modules are held to the identical standard as
the code they exercise, because a 2500-line test file is exactly as hard to
navigate as a 2500-line module.

**``dashboard/src`` is scanned for the same reason (td-026): TypeScript shipped
alongside the Python tree was invisible to a gate that only ever walked
``*.py``.** The scan covers ``*.ts``/``*.tsx`` and excludes two things the
ceiling does not bind: files under any ``__tests__/`` directory (test doubles
colocated with their source, unlike Python's separate ``tests/`` tree) and
``processModel.ts``, mechanically generated from
``src/knotica/core/process_model.py`` and never hand-edited. ``.css`` stays
explicitly out of scope -- whether a stylesheet is bound by a per-module code
ceiling is a judgment call this step does not make; ``app.css``'s 3 098 lines
are a deliberate, recorded scope line, not an oversight.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "knotica"
TESTS_ROOT = REPO_ROOT / "tests"
DASHBOARD_SRC_ROOT = REPO_ROOT / "dashboard" / "src"

#: The project's hard per-module line ceiling.
LINE_CEILING = 800

#: Modules already over the ceiling, at their measured high-water mark. Each may
#: shrink freely; none may grow. Removing an entry is the goal -- see rule 3.
#: Paths are POSIX, relative to ``src/knotica``.
OVER_CEILING_BASELINE: dict[str, int] = {
    # Raised deliberately, with the extractable part extracted first. The
    # gate-input fingerprint and arena-scorer provenance work added in-place
    # field wiring to all three: a `instructions_override` parameter threaded to
    # the runner (harness), five `baseline_golden_manifest_sha` writes plus a
    # scorer-descriptor field (loop), and one row in each of the two decision
    # tables for the `withdraw` transition (gapfill). None of it is a procedure
    # that could live elsewhere. The one part that was -- the ~50-line
    # `hold_preview` -- moved to `core/loop_holds.py`, which is why `core/loop.py`
    # lands at 1168 rather than 1211.
    "evals/harness.py": 1234,
    "core/loop.py": 1168,
    "evals/golden.py": 975,
    # Raised 947 -> 955 for `GapRecord.decided_reason`: an additive optional
    # field (mirrors `SuggestionRecord.decided_reason`) so the human gap
    # transition's reason survives a re-read instead of existing only in the
    # one-shot tool result. One field, one docstring, one line each in
    # `to_json_line`/`from_json_line` -- not extractable from a frozen record's
    # own (de)serialization pair.
    "core/records.py": 955,
    # Raised 1115 -> 1138 for `review_gap`'s dismiss-requires-a-reason rule and
    # its `decided_reason` persistence: `_plan_gap_decision` grew a reason check
    # and a docstring, and `apply_gap_decision` now threads the cleaned reason
    # onto the record it replaces. Same module as the prior raise below, same
    # reason it cannot move: the human gap transition's whole legality table
    # lives here beside `apply_decision`'s. td-042 still names the real fix --
    # split gapfill.py into file / drain / decide-gate.
    #
    # Prior raise, 944 -> 1115, for the gap-lifecycle writers: two of the three
    # `GAP_STATUSES` values had no writer anywhere in `src/knotica/`, so the gap
    # queue was append-only in practice and its declared terminal state did not
    # exist. Closing that needs the machine transition (a merged gate verdict
    # resolves the originating gap, inside the gate stamp's own transaction) and
    # the human one (`apply_gap_decision`, dismiss/reopen). Neither is extractable
    # from here: the machine half must be declared to the *same* `VaultTransaction`
    # as the suggestion stamp or the two writes stop being one commit, and the
    # human half is the parameter-for-parameter sibling of `apply_decision`, which
    # lives in this module.
    "core/gapfill.py": 1138,
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

#: TypeScript/TSX modules under ``dashboard/src`` already over the ceiling, at
#: their measured high-water mark, taken **after** the M3 lane dissolution
#: landed -- not before, so this baseline reflects the tree the ratchet
#: actually starts guarding rather than a doomed intermediate shape.
DASHBOARD_OVER_CEILING_BASELINE: dict[str, int] = {
    # The shared type-definition module for every lane and the panes that
    # remain. Splitting it by domain (notes / sources / arena / loop) is the
    # fix td-026 itself named as preferable -- not attempted here, since this
    # step closes the ratchet's *visibility* gap, not the underlying design.
    "types.ts": 1251,
    # Wraps every MCP tool call the dashboard makes; crossed the ceiling
    # during M3's lane wiring. Extractable by tool-domain the same way
    # `types.ts` is, and not attempted here for the same reason.
    "toolClient.ts": 875,
}


def _is_test_double_or_generated(path: Path) -> bool:
    """True for files the dashboard ceiling does not bind.

    ``__tests__/`` directories hold test doubles colocated with their source
    (the dashboard has no separate ``tests/`` tree to scan instead).
    ``processModel.ts`` is mechanically generated from
    ``src/knotica/core/process_model.py`` and never hand-edited.
    """
    return "__tests__" in path.parts or path.name == "processModel.ts"


#: The scanned trees, each with its own baseline, glob patterns, and exclusion
#: rule. Parametrising over this rather than duplicating the rules per root is
#: what keeps them from drifting apart -- a rule added for one is a rule added
#: for all.
SCANNED_TREES = (
    pytest.param(SRC_ROOT, OVER_CEILING_BASELINE, ("*.py",), None, id="src"),
    pytest.param(TESTS_ROOT, TESTS_OVER_CEILING_BASELINE, ("*.py",), None, id="tests"),
    pytest.param(
        DASHBOARD_SRC_ROOT,
        DASHBOARD_OVER_CEILING_BASELINE,
        ("*.ts", "*.tsx"),
        _is_test_double_or_generated,
        id="dashboard",
    ),
)


def _line_counts(
    root: Path,
    patterns: tuple[str, ...],
    exclude: Callable[[Path], bool] | None,
) -> dict[str, int]:
    """Every module under ``root`` matching ``patterns``, by POSIX-relative path.

    ``exclude`` (when given) drops paths the ceiling does not bind -- test
    doubles, generated files -- before line counts are computed.
    """
    paths = (path for pattern in patterns for path in root.rglob(pattern))
    if exclude is not None:
        paths = (path for path in paths if not exclude(path))
    return {
        path.relative_to(root).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(paths)
    }


@pytest.mark.parametrize(("root", "baseline", "patterns", "exclude"), SCANNED_TREES)
def test_no_baseline_module_grows_beyond_its_recorded_high_water_mark(
    root: Path,
    baseline: dict[str, int],
    patterns: tuple[str, ...],
    exclude: Callable[[Path], bool] | None,
) -> None:
    counts = _line_counts(root, patterns, exclude)
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


@pytest.mark.parametrize(("root", "baseline", "patterns", "exclude"), SCANNED_TREES)
def test_no_module_outside_the_baseline_exceeds_the_ceiling(
    root: Path,
    baseline: dict[str, int],
    patterns: tuple[str, ...],
    exclude: Callable[[Path], bool] | None,
) -> None:
    counts = _line_counts(root, patterns, exclude)
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


@pytest.mark.parametrize(("root", "baseline", "patterns", "exclude"), SCANNED_TREES)
def test_baseline_has_no_stale_entries(
    root: Path,
    baseline: dict[str, int],
    patterns: tuple[str, ...],
    exclude: Callable[[Path], bool] | None,
) -> None:
    counts = _line_counts(root, patterns, exclude)
    missing = sorted(module for module in baseline if module not in counts)
    assert not missing, f"Baseline names modules that no longer exist; drop them: {missing}"
    paid_down = {module: counts[module] for module in baseline if counts[module] <= LINE_CEILING}
    assert not paid_down, (
        "A baseline module is now within the ceiling -- remove its entry so the "
        "exemption cannot silently return. "
        f"module -> lines: {paid_down}"
    )
