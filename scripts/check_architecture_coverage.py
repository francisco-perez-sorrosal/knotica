#!/usr/bin/env python3
"""Gate the one artifact pair in this repo that nothing else gates.

`make verify` checks the test topology and the ADR corpus. It did not check the
architecture documents -- and in a repo that lands seventeen commits in a day,
the ungated artifact is the one that lags. Three `core/` modules were added and
appeared zero times in either `.ai-state/DESIGN.md` or `docs/architecture.md`;
the topology tracked them and the architecture record did not, which made the
lag harder to see rather than easier (td-038).

Two checks, both exact and both fail-closed.

1. **Inventory.** Every package under `src/knotica/` publishes its module count
   in `DESIGN.md` § 3's inventory table, and that count must equal what is on
   disk. This is the check that bites: adding a module to a package moves the
   real count away from the published one, and no amount of prose elsewhere
   hides an integer that no longer matches.

2. **Citations resolve.** Every `src/knotica/...` path cited in either document
   must exist on disk, so a rename or deletion cannot leave a dangling claim
   behind. `docs/architecture.md`'s header promises exactly this ("Every path in
   the table below was re-checked on disk"); until now nothing held it to it.

**Scope, stated so it is not over-read.** This gate proves every module is
*accounted for*, not that every module is *described*. `DESIGN.md`'s `core/` row
is deliberately a residual -- "read it as a subtraction, never as `core/**`" --
so demanding a sentence per module would reverse a recorded decision rather than
enforce one. What the counts catch is the defect that actually occurred: a module
arriving (or leaving) without the architecture record being told. A module that
is counted but never described is outside this gate's reach and stays a matter
for review.

The count includes `__init__.py`, which is the convention the `okf/`,
`guillotine/`, and `service/` rows already used before this script existed.

Exit 0 when healthy, 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN = REPO_ROOT / ".ai-state" / "DESIGN.md"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
PACKAGE_ROOT = REPO_ROOT / "src" / "knotica"

#: One row of DESIGN.md § 3's inventory table: a backticked package path, then its count.
INVENTORY_ROW_RE = re.compile(r"^\|\s*`(src/knotica/[^`]*)`\s*\|\s*(\d+)\s*\|", re.MULTILINE)

#: Any `src/knotica/...` path cited in prose, backticked or bare. `*` is part of
#: the character class because the documents legitimately cite globs
#: (`src/knotica/core/loop*.py`); stopping short of one would silently truncate
#: the path and then report the truncation as a dangling citation.
CITATION_RE = re.compile(r"`?(src/knotica/[A-Za-z0-9_./*]*)`?")

#: Paths the documents cite as designed-but-not-built. Each entry is a claim the
#: documents make about the future, not a stale reference to a deleted thing --
#: so it is listed here by name rather than silently tolerated by a pattern.
PLANNED_PATHS = {
    "src/knotica/agent/": "SIA outer-loop runners, Phase 3b — `Status: Planned` in DESIGN.md § 3",
}


def _packages() -> dict[str, int]:
    """Every package directory under ``src/knotica/``, mapped to its module count.

    A directory is a package when it holds at least one ``.py`` file; the count
    is every ``.py`` directly inside it, ``__init__.py`` included.
    """
    packages: dict[str, int] = {}
    for directory in [PACKAGE_ROOT, *PACKAGE_ROOT.rglob("*")]:
        if not directory.is_dir() or "__pycache__" in directory.parts:
            continue
        modules = [path for path in directory.glob("*.py")]
        if not modules:
            continue
        relative = directory.relative_to(REPO_ROOT).as_posix() + "/"
        packages[relative] = len(modules)
    return packages


def _declared_counts(design: str) -> dict[str, int]:
    """Package → published module count, read from DESIGN.md § 3's inventory table."""
    return {path: int(count) for path, count in INVENTORY_ROW_RE.findall(design)}


def _check_inventory(design: str) -> list[str]:
    """Every package is declared, and every declaration matches the tree."""
    failures: list[str] = []
    actual = _packages()
    declared = _declared_counts(design)

    for package, count in sorted(actual.items()):
        if package not in declared:
            failures.append(
                f"{package} holds {count} module(s) but has no row in DESIGN.md § 3's "
                f"inventory table — a package the architecture record does not account for"
            )
        elif declared[package] != count:
            failures.append(
                f"{package} holds {count} module(s), but DESIGN.md § 3 publishes "
                f"{declared[package]} — the architecture record is behind the code"
            )

    for package in sorted(set(declared) - set(actual)):
        failures.append(
            f"{package} is declared in DESIGN.md § 3's inventory table but is not a package on disk"
        )
    return failures


def _resolves(citation: str) -> bool:
    """Whether a cited path is on disk — a glob resolves when it matches anything."""
    if "*" in citation:
        return any(REPO_ROOT.glob(citation))
    return (REPO_ROOT / citation).exists()


def _check_citations(documents: dict[Path, str]) -> list[str]:
    """Every `src/knotica/...` path either resolves on disk or is a named Planned path."""
    failures: list[str] = []
    for path, text in documents.items():
        cited = {match.rstrip(".,;:") for match in CITATION_RE.findall(text)}
        for citation in sorted(cited):
            if citation in PLANNED_PATHS or _resolves(citation):
                continue
            failures.append(
                f"{path.relative_to(REPO_ROOT)}: cites `{citation}`, which does not exist on disk"
            )
    return failures


def main() -> int:
    documents = {DESIGN: DESIGN.read_text(encoding="utf-8")}
    documents[ARCHITECTURE] = ARCHITECTURE.read_text(encoding="utf-8")

    failures = _check_inventory(documents[DESIGN]) + _check_citations(documents)

    if failures:
        print(
            f"architecture coverage check FAILED ({len(failures)} finding(s)):",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    packages = _packages()
    print(
        f"architecture coverage OK — {len(packages)} package(s), "
        f"{sum(packages.values())} module(s) accounted for"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
