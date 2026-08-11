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

3. **Referential integrity of prose that names a tool.** Checks 1 and 2 gate the
   reference doc. This one gates every *other* place a name is published --
   `description=`/`fix=` strings the model reads, `commands/*.md` bodies, the
   session-start hook, and the skill's routing description -- because that is
   where `compile_run` survived for weeks.

**What check 3 flags, and what it deliberately does not.** Naming a rule for
"identifier of tool-name shape" is the whole difficulty: the loose version flags
every field name. Measured on this tree, 189 `description=`/`fix=` literals
contain just ten distinct snake_case identifiers, and most are parameters or
config keys (`confirm_nonce`, `eval_window`, `pages_used`) that must never be
flagged. `golden_review` is a real module. So the rule is not "looks like a
tool"; it is three shapes that can only be tool references:

  * `<dispatcher>_<action>` where both halves are live but the whole is not a
    registered tool -- exactly what consolidation leaves behind (`compile_run`
    is `compile` + action `run`, `loop_run_once` is `loop` + `run_once`). No
    parameter or config key has this shape, because it requires the suffix to be
    one of that specific dispatcher's own actions.
  * `<tool> action=<x>` -- the dispatcher must be live and `<x>` must be one of
    *its* actions, read from the module's own `_ACTIONS`.
  * `knotica <cmd>` and `/knotica:<alias>` -- resolved against `COMMAND_NAMES`
    and `commands/`.

It will not catch a wholly-removed tool whose name has no dispatcher shape. That
is a stated limit, not an oversight: a rule wide enough to catch it flags
`next_cursor` too, and a gate that cries wolf gets muted.

**An unparseable document is a failure, not a skip.** If the summary sentence is
reworded past the shape parsed here, the gate fails and says so. A gate that
silently stops checking when its input changes is worse than no gate: it reports
green for a surface nobody is looking at any more.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE = REPO_ROOT / "docs" / "reference.md"
COMMANDS_DIR = REPO_ROOT / "commands"
SRC_DIR = REPO_ROOT / "src"
SESSION_HOOK = REPO_ROOT / "hooks" / "session_start.sh"
SKILL = REPO_ROOT / "skills" / "wiki-maintenance" / "SKILL.md"

#: The nine dispatchers, each of which declares its own action tuple.
DISPATCHERS = (
    "arena",
    "branches",
    "compile",
    "datasets",
    "golden",
    "loop",
    "notes",
    "vault",
    "vault_health",
)

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


# ---------------------------------------------------------------------------
# Check 3 -- referential integrity of prose that names a tool
# ---------------------------------------------------------------------------

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_DISPATCH_CALL = re.compile(r"\b(?P<tool>[a-z][a-z_]*) +action=(?P<action>[a-z_]+)")
_CLI_CALL = re.compile(r"\bknotica +(?P<cmd>[a-z][a-z-]*)")
_SLASH_CALL = re.compile(r"/knotica:(?P<alias>[a-z][a-z-]*)")
#: Shell comments, stripped before scanning: the hook's prose says "knotica is
#: not configured", and `is` is not a subcommand.
_SH_COMMENT = re.compile(r"^\s*#.*$", re.M)


def _actions() -> dict[str, tuple[str, ...]]:
    """Each dispatcher's own `_ACTIONS`, read from the module that declares it."""
    resolved: dict[str, tuple[str, ...]] = {}
    for name in DISPATCHERS:
        module = importlib.import_module(f"knotica.mcp_server.tools_dispatch_{name}")
        actions = getattr(module, "_ACTIONS", None)
        if actions:
            resolved[name] = tuple(actions)
    return resolved


def _literal(node: ast.AST) -> str:
    """The literal text of a string constant or the literal parts of an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _description_prose() -> list[tuple[str, str]]:
    """(where, text) for every `description=`/`fix=`/`*_DESCRIPTION` string in `src/`."""
    out: list[tuple[str, str]] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover -- src/ always parses; fail loudly if not
            return [(path.as_posix(), "")]
        where = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in {"description", "fix"}:
                        out.append((where, _literal(keyword.value)))
            if isinstance(node, ast.Assign):
                names = [getattr(t, "id", "") for t in node.targets]
                if any(name.endswith("_DESCRIPTION") for name in names):
                    out.append((where, _literal(node.value)))
    return out


def _scan(where: str, text: str, live: _Surface, *, shell: bool = False) -> list[str]:
    """Every dead tool reference in one blob of published prose.

    Invocations are only recognised **in code position** -- inside a backtick span
    for prose, or bare in a shell script. "knotica" is used adjectivally all over
    this project ("a knotica vault", "your knotica wiki", "knotica so that..."),
    so matching `knotica <word>` in running prose reports the English word after
    it as a subcommand. Measured: that mistake produced 17 false findings and 0
    real ones.
    """
    failures: list[str] = []
    # Code position: a shell script is all code; prose is code only inside backticks.
    code = [text] if shell else [span.strip() for span in _BACKTICKED.findall(text)]

    for token in code:
        if token in live.tools or "_" not in token or " " in token:
            continue
        head, _, tail = token.partition("_")
        # A consolidated-away tool: `<dispatcher>_<that dispatcher's own action>`.
        if tail in live.actions.get(head, ()):
            failures.append(
                f"{where}: `{token}` names no registered tool — "
                f"`{head}` was consolidated, so this is `{head} action={tail}`"
            )

    for fragment in code:
        for match in _DISPATCH_CALL.finditer(fragment):
            tool, action = match.group("tool"), match.group("action")
            if tool in live.actions and action not in live.actions[tool]:
                failures.append(
                    f"{where}: `{tool} action={action}` — {tool!r} has no such action "
                    f"(valid: {'|'.join(live.actions[tool])})"
                )
        for match in _CLI_CALL.finditer(fragment):
            if match.group("cmd") not in live.commands:
                failures.append(f"{where}: `knotica {match.group('cmd')}` is not a CLI subcommand")
        if not shell:
            failures.extend(_slash_failures(where, fragment, live))
    return failures


def _slash_failures(where: str, text: str, live: _Surface) -> list[str]:
    """Dead `/knotica:<alias>` references.

    Split out from :func:`_scan` because it is the one rule that needs *no* code
    position: `/knotica:` cannot occur by accident in English, so a shell comment
    is scanned too — a stale alias in a comment still misinforms the next reader,
    and suppressing it was pure lost coverage.
    """
    return [
        f"{where}: `/knotica:{match.group('alias')}` ships no command file"
        for match in _SLASH_CALL.finditer(text)
        if match.group("alias") not in live.slash
    ]


class _Surface:
    """The live vocabulary every published reference is resolved against."""

    def __init__(self) -> None:
        import anyio

        from knotica.cli import COMMAND_NAMES
        from knotica.mcp_server.server import build_server

        self.tools = {tool.name for tool in anyio.run(build_server().list_tools)}
        self.commands = set(COMMAND_NAMES)
        self.slash = {
            path.stem for path in COMMANDS_DIR.glob("*.md") if path.name not in NOT_A_COMMAND
        }
        self.actions = _actions()


def _check_references() -> list[str]:
    """Every published name that no longer resolves, across four surfaces."""
    live = _Surface()
    failures: list[str] = []

    for where, text in _description_prose():
        failures.extend(_scan(where, text, live))

    for path in sorted(COMMANDS_DIR.glob("*.md")):
        if path.name in NOT_A_COMMAND:
            continue
        failures.extend(
            _scan(path.relative_to(REPO_ROOT).as_posix(), path.read_text(encoding="utf-8"), live)
        )

    if SESSION_HOOK.is_file():
        where = SESSION_HOOK.relative_to(REPO_ROOT).as_posix()
        text = SESSION_HOOK.read_text(encoding="utf-8")
        # Two passes, because the two rules need different scopes: `knotica <word>`
        # is only an invocation in code (the hook's own comments read "knotica is
        # not configured"), while `/knotica:<alias>` is unambiguous everywhere.
        failures.extend(_scan(where, _SH_COMMENT.sub("", text), live, shell=True))
        failures.extend(_slash_failures(where, text, live))
    else:
        failures.append(f"{SESSION_HOOK.relative_to(REPO_ROOT).as_posix()} is missing")

    failures.extend(_check_routing_contract(live))
    return failures


def _check_routing_contract(live: _Surface) -> list[str]:
    """The skill description and the server instructions are one contract, kept twice.

    Both tell a model when to reach for knotica, and they are maintained
    independently -- so a tool named in one and absent from the other is a
    routing surface that drifted without anyone editing a shared file.
    """
    if not SKILL.is_file():
        return [f"{SKILL.relative_to(REPO_ROOT).as_posix()} is missing"]
    frontmatter = re.match(r"\A---\n(.*?)\n---\n", SKILL.read_text(encoding="utf-8"), re.S)
    if frontmatter is None:
        return [f"{SKILL.relative_to(REPO_ROOT).as_posix()} has no YAML frontmatter"]

    from knotica.mcp_server.server import _INSTRUCTIONS

    # Unambiguous names only: `loop`, `vault`, `notes` and friends are ordinary
    # English in a routing description ('the self-improvement loop'), and matching
    # them as tool references reports prose as drift.
    described = {
        name
        for name in live.tools
        if "_" in name and re.search(rf"\b{name}\b", frontmatter.group(1))
    }
    return [
        f"skills/wiki-maintenance/SKILL.md names tool {name!r} in its routing description, "
        "but server.py::_INSTRUCTIONS does not — the two are one contract kept in two places"
        for name in sorted(described)
        if not re.search(rf"\b{name}\b", _INSTRUCTIONS)
    ]


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

    failures = (
        _check_tools(text, sections)
        + _check_cli(sections)
        + _check_plugin_aliases(sections)
        + _check_references()
    )
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
