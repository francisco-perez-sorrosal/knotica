#!/usr/bin/env python3
"""Run one test group from `.ai-state/TEST_TOPOLOGY.md`, or validate the file against disk.

The topology maps `DESIGN.md` §3 components onto eleven test groups so a change
touching one subsystem can run a scoped subset instead of the 2443-test, ~296 s
full suite. Praxion's pipeline agents already consume it -- the planner tags each
step with the groups it touches and the implementer translates those into a
scoped invocation -- but a human at a terminal had no way to use it without
transcribing paths out of markdown by hand.

This script closes that gap by *deriving* the invocation from the topology rather
than restating it. The direction matters: `TEST_TOPOLOGY.md` stays the single
source of truth, because it is what sentinel audits (TT01-TT06) and what
`/refresh-topology` regenerates on drift. Copying the group membership into
`pyproject.toml` would fork that truth into two files that must agree, with the
audited one no longer authoritative.

`--check` is the other half. Until now the topology had no mechanical consumer,
so drift surfaced only as stale prose that a periodic audit might catch. Wired
into `make verify`, a renamed or deleted test file becomes a failing gate
instead.

Usage:
    python scripts/test_group.py --list                 # groups, tiers, sizes
    python scripts/test_group.py --check                # validate against disk
    python scripts/test_group.py notes-overlay          # run one group
    python scripts/test_group.py notes-overlay -x -q    # ... with pytest flags

Exit codes: 0 success; 1 a check failed or pytest failed; 2 usage error
(unknown group, unreadable topology). pytest's own exit code is propagated
unchanged when running a group.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_PATH = REPO_ROOT / ".ai-state" / "TEST_TOPOLOGY.md"

# The topology's group definitions are fenced ```yaml blocks. Prose and tables
# in the same file are ignored by construction.
YAML_BLOCK = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)

# Every field the trunk schema requires of a group block.
REQUIRED_FIELDS = (
    "id",
    "title",
    "subsystems",
    "tier",
    "selectors",
    "file_dependencies",
    "parallel_safe",
    "shared_fixture_scope",
)

# The only selector strategy this project registers. Anything else is a real
# topology change that needs a translator here -- failing loudly beats guessing
# an invocation, which is the trunk's "do not invent a selector" rule.
SUPPORTED_STRATEGY = "pytest-globs"

# The `## Subsystems` table names every group independently of the YAML blocks,
# which makes it the one source that can answer "did we parse them all?".
# Bounded to the component table itself -- the notes below it carry their own
# tables whose first column is also a backticked group id.
SUBSYSTEMS_TABLE = re.compile(r"^## Subsystems$(.*?)^### ", re.DOTALL | re.MULTILINE)
SUBSYSTEMS_ROW = re.compile(r"^\|[^|]+\|\s*`([a-z][a-z0-9-]*)`\s*\|", re.MULTILINE)

# The un-grouped table documents every test file that legitimately belongs to
# no group (test infrastructure and dev-tooling gates covering code outside
# `src/knotica/`). Bounded to the table whose header names it -- the fitness
# tables' first column is also a backticked `test_*.py`, but those files are
# group-claimed and must stay visible to the orphan walk.
UNGROUPED_TABLE = re.compile(r"^\| Un-grouped file \|.*\n(?:\|.*\n)+", re.MULTILINE)
UNGROUPED_ROW = re.compile(r"^\|\s*`(test_[a-z0-9_]+\.py)`", re.MULTILINE)


def declared_group_ids(text: str) -> set[str]:
    """Return the group ids the `## Subsystems` table names.

    Parsed from the table rather than from the YAML blocks on purpose: a check
    that derives both sides from the same parse cannot detect that parse
    silently dropping half the file.
    """
    table = SUBSYSTEMS_TABLE.search(text)
    if table is None:
        sys.exit("error: could not locate the '## Subsystems' table — the topology is malformed")
    return set(SUBSYSTEMS_ROW.findall(table.group(1)))


def documented_ungrouped(text: str) -> set[str]:
    """Return the file names the topology's un-grouped table sanctions.

    Parsed from the table rather than restated here: the topology is the single
    membership declaration, so leaving a file out of every group is legal only
    when that same document says why. A new exception earns its row before the
    check will honour it.
    """
    table = UNGROUPED_TABLE.search(text)
    if table is None:
        sys.exit("error: could not locate the un-grouped table — the topology is malformed")
    return set(UNGROUPED_ROW.findall(table.group(0)))


def load_groups() -> list[dict[str, Any]]:
    """Parse every group block out of the topology, in file order."""
    if not TOPOLOGY_PATH.exists():
        sys.exit(
            f"error: no topology at {TOPOLOGY_PATH.relative_to(REPO_ROOT)}\n"
            "       create one with /refresh-topology --init"
        )

    groups: list[dict[str, Any]] = []
    for raw in YAML_BLOCK.findall(TOPOLOGY_PATH.read_text(encoding="utf-8")):
        try:
            block = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            sys.exit(f"error: malformed YAML block in the topology: {exc}")
        if isinstance(block, dict) and "id" in block:
            groups.append(block)

    if not groups:
        sys.exit("error: the topology contains no group blocks")
    return groups


def selector_args(group: dict[str, Any]) -> list[str]:
    """Return the pytest path arguments a group selects."""
    args: list[str] = []
    for selector in group.get("selectors", []):
        strategy = selector.get("strategy")
        if strategy != SUPPORTED_STRATEGY:
            sys.exit(
                f"error: group '{group['id']}' uses selector strategy "
                f"'{strategy}', which this script cannot translate.\n"
                f"       Only '{SUPPORTED_STRATEGY}' is registered for Python."
            )
        args.extend(selector.get("arg", []))
    return args


def count_test_files(args: list[str]) -> int:
    """Count the test files a group's selector args resolve to.

    Directory args expand: `tests/core/notes/` is a single arg standing for
    thirteen files. Reporting the arg count instead would disagree with the
    topology's own runtime table for the three directory-selecting groups, and
    a reader comparing the two columns would have no way to tell which is wrong.
    """
    total = 0
    for arg in args:
        path = REPO_ROOT / arg
        if path.is_dir():
            total += sum(1 for p in path.rglob("test_*.py") if "__pycache__" not in p.parts)
        elif path.exists():
            total += 1
    return total


def cmd_list(groups: list[dict[str, Any]]) -> int:
    """Print each group with its tier and the number of test files it covers."""
    width = max(len(str(g["id"])) for g in groups)
    print(f"{'GROUP'.ljust(width)}  {'TIER':<12} FILES  TITLE")
    for group in groups:
        files = count_test_files(selector_args(group))
        title = str(group.get("title", ""))
        print(f"{str(group['id']).ljust(width)}  {group.get('tier', '?'):<12} {files:>5}  {title}")
    print(f"\n{len(groups)} groups. Run one with: make test-group GROUP=<id>")
    return 0


def cmd_check(groups: list[dict[str, Any]]) -> int:
    """Validate the topology against the filesystem. Returns 1 on any failure."""
    failures: list[str] = []
    seen_ids: set[str] = set()

    # Completeness first. Everything below validates the blocks we parsed; only
    # this compares that set against an independent declaration of what should
    # exist. Without it a parser regression that matched half the file would
    # report "OK -- 5 groups" and read as a pass.
    declared = declared_group_ids(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    parsed = {str(g["id"]) for g in groups}
    for missing in sorted(declared - parsed):
        failures.append(f"{missing}: named in the Subsystems table but has no group block")
    for extra in sorted(parsed - declared):
        failures.append(f"{extra}: has a group block but is absent from the Subsystems table")

    for group in groups:
        gid = str(group["id"])
        if gid in seen_ids:
            failures.append(f"{gid}: duplicate group id")
        seen_ids.add(gid)

        for field in REQUIRED_FIELDS:
            if field not in group:
                failures.append(f"{gid}: missing required field '{field}'")

        for arg in selector_args(group):
            # A wildcard silently resolves to nothing under shell=False, so the
            # group would appear to pass while running fewer tests than it claims.
            if "*" in arg or "?" in arg:
                failures.append(f"{gid}: selector arg contains a wildcard -> {arg}")
            elif not (REPO_ROOT / arg).exists():
                failures.append(f"{gid}: selector arg does not exist -> {arg}")

        for dep in group.get("file_dependencies", []):
            if not list(REPO_ROOT.glob(dep)):
                failures.append(f"{gid}: file_dependencies matches nothing -> {dep}")

    # The orphan walk: every test file the tree holds is either claimed by some
    # group's selectors or documented in the topology's un-grouped table. This
    # is the converse of the selector checks above -- those prove the topology's
    # claims resolve, only this proves the tree has no file the topology never
    # heard of, which is how a new test silently falls out of the scoped inner
    # loop while the full suite stays green.
    claimed: set[Path] = set()
    for group in groups:
        for arg in selector_args(group):
            path = REPO_ROOT / arg
            if path.is_dir():
                claimed.update(p for p in path.rglob("test_*.py") if "__pycache__" not in p.parts)
            elif path.exists():
                claimed.add(path)
    sanctioned = documented_ungrouped(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    for path in sorted((REPO_ROOT / "tests").rglob("test_*.py")):
        if "__pycache__" in path.parts or path in claimed or path.name in sanctioned:
            continue
        failures.append(
            f"{path.relative_to(REPO_ROOT)}: no group claims it and the un-grouped table "
            "does not document it -- add it to a group's selector args, or give it a row"
        )

    if failures:
        print(f"topology check FAILED ({len(failures)} problem(s)):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nThe topology has drifted from the tree. Reconcile it with /refresh-topology.",
            file=sys.stderr,
        )
        return 1

    print(f"topology check OK — {len(groups)} groups, every selector and dependency resolves")
    return 0


def cmd_run(groups: list[dict[str, Any]], name: str, pytest_args: list[str]) -> int:
    """Run one group's tests, propagating pytest's exit code."""
    match = next((g for g in groups if str(g["id"]) == name), None)
    if match is None:
        available = ", ".join(sorted(str(g["id"]) for g in groups))
        print(f"error: unknown group '{name}'\n       available: {available}", file=sys.stderr)
        return 2

    args = selector_args(match)
    print(f"# {match['id']} — {match.get('title', '')}")
    print(f"# {count_test_files(args)} test file(s), tier={match.get('tier', '?')}\n")
    # pytest writes straight to the terminal fd while our own stdout is block-
    # buffered when piped, so without this flush the header lands *after* the
    # test output it introduces.
    sys.stdout.flush()
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["pytest", *args, *pytest_args],
        cwd=REPO_ROOT,
        check=False,
    )
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a test group defined in .ai-state/TEST_TOPOLOGY.md.",
    )
    parser.add_argument("group", nargs="?", help="group id to run (see --list)")
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="extra arguments forwarded to pytest",
    )
    parser.add_argument("--list", action="store_true", help="list the groups and exit")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the topology against the filesystem and exit",
    )
    parsed = parser.parse_args()

    groups = load_groups()

    if parsed.list:
        return cmd_list(groups)
    if parsed.check:
        return cmd_check(groups)
    if not parsed.group:
        parser.print_help()
        return 2
    return cmd_run(groups, parsed.group, parsed.pytest_args)


if __name__ == "__main__":
    sys.exit(main())
