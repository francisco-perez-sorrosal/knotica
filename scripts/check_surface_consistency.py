#!/usr/bin/env python3
"""Gate the published surface against the code that publishes it.

`docs/reference.md` is the only complete description of what this project
exposes -- every tool, every CLI subcommand, every slash-command alias -- and
nothing checked it. The result is the defect this gate exists to end: the doc
said "33 tools ... 9 dispatchers and 24 flat" while the server registered 35,
and `commands/setup.md` told operators to use a `compile_run` tool that stopped
existing when `dec-045`/`dec-050` folded it into the `compile` dispatcher. Both
sat wrong for weeks. Neither is the kind of error a reader catches, because a
reader has no reason to doubt an integer.

It matters more than ordinary doc rot. This surface is what a *model* routes on,
so a stale name is not a cosmetic defect -- it is an instruction to call
something that will fail. And the lane rename ahead will rewrite every one of
these tables at once; a rename against an already-drifted baseline cannot be
verified, because there is nothing trustworthy to diff against.

Two checks, both exact and both fail-closed.

1. **The tool surface.** The four tool tables' names must equal what
   `build_server()` actually registers -- no extra row, no missing row. Each
   section heading publishes its own row count, and the summary paragraph
   publishes four more integers; all of them are **derived from the tables**
   rather than hard-coded here, so adding a tool forces the doc to move and this
   script never needs editing to keep up.

2. **The command surfaces.** `COMMAND_NAMES` must equal the CLI subcommand
   table, and the `commands/*.md` files must equal the plugin-alias table.

**An unparseable document is a failure, not a skip.** If the summary sentence is
reworded past the shape parsed here, the gate fails and says so. A gate that
silently stops checking when its input changes is worse than no gate: it reports
green for a surface nobody is looking at any more.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE = REPO_ROOT / "docs" / "reference.md"
COMMANDS_DIR = REPO_ROOT / "commands"

#: The four `### ` sections whose tables together enumerate every registered tool.
TOOL_SECTIONS = ("Read tools", "Write tools", "Other flat tools", "Action dispatchers")

#: Files in `commands/` that are not slash commands.
NOT_A_COMMAND = frozenset({"CLAUDE.md"})

#: A `## `/`### ` heading, optionally publishing its own row count after an em dash
#: ("### Read tools — 5, zero commits"). The count group must absorb the rest of
#: the line: left outside it, the non-greedy label matches a single character.
_HEADING = re.compile(r"^#{2,4} (?P<label>[^\n—]+?) *(?:— *(?P<count>\d+)[^\n]*)?$", re.M)
_ROW = re.compile(r"^\| *`(?P<name>[^`]+)` *\|", re.M)
#: The summary sentence, whitespace-normalized first so a re-wrap cannot break it.
_SUMMARY = re.compile(
    r"(?P<total>\d+) tools are registered on the server: (?P<dispatchers>\d+) "
    r"action-parameterized \*\*dispatchers\*\* and (?P<flat>\d+) flat, fixed-behavior tools "
    r"\((?P<read>\d+) read \+ (?P<write>\d+) write \+ (?P<other>\d+) grouped by purpose below\)"
)


def _registered_tools() -> set[str]:
    """Every tool name the real server publishes."""
    import anyio

    from knotica.mcp_server.server import build_server

    return {tool.name for tool in anyio.run(build_server().list_tools)}


def _sections(text: str) -> dict[str, tuple[int | None, list[str]]]:
    """Each heading's published count and the tool names in the table beneath it."""
    matches = list(_HEADING.finditer(text))
    found: dict[str, tuple[int | None, list[str]]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        declared = match.group("count")
        found[match.group("label").strip()] = (
            int(declared) if declared else None,
            [row.group("name") for row in _ROW.finditer(body)],
        )
    return found


def _check_tools(text: str, sections: dict[str, tuple[int | None, list[str]]]) -> list[str]:
    """Table names equal the registry, and every published integer equals a row count."""
    failures: list[str] = []
    missing_sections = [name for name in TOOL_SECTIONS if name not in sections]
    if missing_sections:
        return [f"docs/reference.md has no '### {name} — N' section" for name in missing_sections]

    documented: list[str] = []
    for name in TOOL_SECTIONS:
        declared, rows = sections[name]
        documented.extend(rows)
        if declared != len(rows):
            failures.append(
                f"'### {name} — {declared}' heading disagrees with its own table ({len(rows)} rows)"
            )

    registered = _registered_tools()
    for undocumented in sorted(registered - set(documented)):
        failures.append(f"tool {undocumented!r} is registered but appears in no reference table")
    for phantom in sorted(set(documented) - registered):
        failures.append(f"reference table lists {phantom!r}, which the server does not register")
    duplicated = sorted({name for name in documented if documented.count(name) > 1})
    for name in duplicated:
        failures.append(f"tool {name!r} appears in more than one reference table")

    failures.extend(_check_summary(text, sections))
    return failures


def _check_summary(text: str, sections: dict[str, tuple[int | None, list[str]]]) -> list[str]:
    """The prose integers, derived from the tables rather than hard-coded here."""
    summary = _SUMMARY.search(" ".join(text.split()))
    if summary is None:
        return [
            "docs/reference.md's tool-summary sentence no longer matches the shape this gate "
            "parses — reword it back, or update _SUMMARY in scripts/check_surface_consistency.py"
        ]
    rows = {name: len(sections[name][1]) for name in TOOL_SECTIONS}
    flat = rows["Read tools"] + rows["Write tools"] + rows["Other flat tools"]
    expected = {
        "read": rows["Read tools"],
        "write": rows["Write tools"],
        "other": rows["Other flat tools"],
        "dispatchers": rows["Action dispatchers"],
        "flat": flat,
        "total": flat + rows["Action dispatchers"],
    }
    return [
        f"tool-summary says {field}={int(summary.group(field))}, tables say {value}"
        for field, value in expected.items()
        if int(summary.group(field)) != value
    ]


def _check_cli(sections: dict[str, tuple[int | None, list[str]]]) -> list[str]:
    """The CLI subcommand table equals the one declaration of the subcommand set."""
    from knotica.cli import COMMAND_NAMES

    if "Subcommands" not in sections:
        return ["docs/reference.md has no '### Subcommands' section"]
    # The table documents second-level commands too (`okf check`, `service install`,
    # `prompt <operation>`), so the comparable key is each row's FIRST token — the
    # top-level name `COMMAND_NAMES` actually declares. Comparing whole cells would
    # report every documented sub-command as a phantom.
    documented = {name.split()[0] for name in sections["Subcommands"][1] if name.split()}
    return _diff("CLI subcommand", set(COMMAND_NAMES), documented, "reference.md's CLI table")


def _check_plugin_aliases(sections: dict[str, tuple[int | None, list[str]]]) -> list[str]:
    """The alias table equals the slash commands actually shipped in `commands/`."""
    if "Plugin aliases" not in sections:
        return ["docs/reference.md has no '## Plugin aliases' section"]
    shipped = {path.stem for path in COMMANDS_DIR.glob("*.md") if path.name not in NOT_A_COMMAND}
    documented = {name.removeprefix("/knotica:") for name in sections["Plugin aliases"][1]}
    return _diff("slash command", shipped, documented, "reference.md's alias table")


def _diff(label: str, actual: set[str], documented: set[str], where: str) -> list[str]:
    """Both directions of a set mismatch, named so the fix is obvious."""
    return [
        f"{label} {name!r} exists but is absent from {where}"
        for name in sorted(actual - documented)
    ] + [
        f"{where} lists {label} {name!r}, which does not exist"
        for name in sorted(documented - actual)
    ]


def main() -> int:
    if not REFERENCE.is_file():
        print(f"surface consistency check FAILED: {REFERENCE} is missing", file=sys.stderr)
        return 1
    text = REFERENCE.read_text(encoding="utf-8")
    sections = _sections(text)

    failures = _check_tools(text, sections) + _check_cli(sections) + _check_plugin_aliases(sections)
    if failures:
        print(
            f"surface consistency check FAILED ({len(failures)} finding(s)):",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    tools = sum(len(sections[name][1]) for name in TOOL_SECTIONS)
    print(
        f"surface consistency check OK — {tools} tools, "
        f"{len(sections['Subcommands'][1])} CLI subcommands, "
        f"{len(sections['Plugin aliases'][1])} slash commands, all matching the code"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
