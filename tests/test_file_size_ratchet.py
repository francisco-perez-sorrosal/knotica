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
every file rather than trusting the recorded set. Both entries are now gone.
``guillotine/report.py`` fell under the ceiling when td-036 deleted a dead
pre-transaction writer from it; ``core/gapfill.py`` was split into the
``core/gapfill/`` package (td-042), six modules of 92-421 lines behind one
re-exporting ``__init__``, so the largest exemption this list ever carried
needed no successor entry. ``core/records.py`` followed it (td-009): now the
``core/records/`` package, one module per record family. A paid-down exemption
is removed, never kept. That is the argument for a mechanical check over a
hand-maintained list, and it is the argument that extended this ratchet to
``tests/``.

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
    # `hold_preview` -- moved to `core/loop_holds.py`. It was later raised
    # 1234 -> 1241 (harness) and 1168 -> 1189 (loop) for the lint-attribution
    # instrument fix and the rebaseline freeze guard: the harness's scalar
    # input filter must sit beside the scalar composition it feeds, and the
    # guard must sit inside `rebaseline` -- the one freeze-time entry point
    # that could create an unreachable bar.
    #
    # `core/loop.py` was here at 1189 and is gone (td-008): the fourth
    # extraction pass moved the observe leg to `core/loop_observe.py` and the
    # regression -> gap redirect to `core/loop_gap_redirect.py`, landing the
    # module at ~680. Deleted with no successor, the rule-3 outcome and the
    # third exemption this list has paid off by splitting rather than by
    # shrinking in place. `evals/harness.py` (1241) and `evals/golden.py`
    # (975) followed in the same pass (td-002): each is now a package of
    # focused modules, the largest at 303 lines, deleted with no successor
    # once Gate 0 proved the instrument fingerprint layout-independent.
    # `core/records.py` was here at 994, raised four times as record fields
    # landed. It is now the `core/records/` package (td-009) -- one module per
    # record family, 117-268 lines each -- so the entry is deleted with no
    # successor, the rule-3 outcome. That is the second exemption this list has
    # paid off by splitting rather than by shrinking in place.
}


#: Test modules already over the ceiling, at their measured high-water mark.
#: Recorded when the ratchet was extended to ``tests/``; same rules as above.
TESTS_OVER_CEILING_BASELINE: dict[str, int] = {
    "test_mcp_notes.py": 2506,
    # 1565 -> 1567 / 1372 -> 1373: the td-002 package split's mechanical
    # import-path edits (harness/golden became packages); no test semantics
    # moved. td-030 still owns both files' paydown.
    "test_evals_harness.py": 1567,
    "core/notes/test_anchor.py": 1534,
    "core/notes/test_resolve.py": 1489,
    "test_evals_golden.py": 1373,
    "core/notes/test_reanchor_note.py": 1112,
    "test_search.py": 1086,
    "core/notes/test_capture_note.py": 950,
    "core/notes/test_store.py": 938,
    "test_transaction.py": 847,
    "test_cli_eval.py": 809,
}

#: TypeScript/TSX modules under ``dashboard/src`` already over the ceiling, at
#: their measured high-water mark, re-taken **after** the last standalone
#: panes dissolved into lanes -- so this baseline reflects the tree the
#: ratchet actually guards rather than a doomed intermediate shape.
#:
#: Both numbers were raised once, from the M3-era 1251/875, when the lane
#: wave's own wiring pushed past them: the `learn`/`answer`/`fill` lanes added
#: payload types and two flat tool-client methods, and the growth was already
#: on the branch before it was reconciled here. Raising a baseline is the
#: ratchet's documented escape hatch, not a silent one -- and both entries
#: still want the domain split below, which is the actual fix.
#:
#: Raised a second time, from 1340/1150, by the Home lane: its cross-topic
#: attention payload added a type family to `types.ts`, and `wikiStatus` grew
#: a `view` parameter in `toolClient.ts`. Both raises were the last ones these
#: entries needed -- `td-057` splits both modules by domain, after which they
#: fall under the ceiling and `test_baseline_has_no_stale_entries` requires
#: their removal outright.
#:
#: Both entries are now gone, and the dashboard tree carries **no** exemption.
#: td-057's types half moved 120 of ``types.ts``'s 126 declarations verbatim
#: into six ``lanes/<lane>/types.ts`` modules (the largest, ``improve``, at 497
#: lines) behind an erasable ``export type`` barrel, landing the root module at
#: 295. Its client half then moved 48 of ``toolClient.ts``'s 51 methods into
#: six ``lanes/<lane>/client.ts`` groups composed onto one prototype, landing
#: the root module at 347 -- the three that stayed (vault/topic administration)
#: belong to the shell, not to any lane. A paid-down exemption is removed,
#: never kept -- the same rule that retired ``guillotine/report.py`` above.
#:
#: An empty mapping is the intended terminal state, not a placeholder: rules 1
#: and 3 have nothing to bind, and rule 2 -- no non-baseline module may cross
#: 800 -- now guards every ``.ts``/``.tsx`` module under ``dashboard/src``
#: without exception.
DASHBOARD_OVER_CEILING_BASELINE: dict[str, int] = {}


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
