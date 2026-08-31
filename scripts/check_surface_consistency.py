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
  * `<tool> action=<x>` -- the dispatcher must still be registered and `<x>`
    must be one of *its* actions, read from the module's own `_ACTIONS` or, for
    a lane, generated from the process model. Both halves are checked: a
    dispatcher whose tool the lanes absorbed fails even when the action is
    still valid, because prose telling a model to call a name that no longer
    resolves is the same defect as naming an action that never existed.
  * `knotica <cmd>` and `/knotica:<alias>` -- resolved against `COMMAND_NAMES`
    and `commands/`.

**Code position includes a fenced block and an `allowed-tools:` entry**, not just
an inline span. A slash command's canonical invocation usually lives in exactly
one fence, and `commands/guillotine.md` published `knotica guillotine` in both
its fence and its frontmatter for months after the CLI nested its lanes, with
this gate green throughout. Inside a fence only the *call-form* rules run: a
fence often carries output rather than an invocation, and a bare identifier there
is as likely to be a JSON key as a tool name.

It will not catch a wholly-removed tool whose name has no dispatcher shape. That
is a stated limit, not an oversight: a rule wide enough to catch it flags
`next_cursor` too, and a gate that cries wolf gets muted.

4. **Published call-forms in the rest of `docs/` and in `DESIGN.md` § 4.** Checks
   1 and 2 gate `docs/reference.md`'s four tool tables and nothing else, and
   check 3 gates `src/` and `commands/`. That left the eight *other* documents
   under `docs/` and the design canon ungated, and both drifted: a pre-release
   review found `docs/gap-fill.md`'s entry-point table publishing seven call
   signatures that return unknown-tool, `docs/new-knowledge-base.md` routing a
   README-promoted walkthrough through dissolved panes, and `DESIGN.md` § 4
   declaring a 35-tool surface two sections after § 3b correctly said 21. None
   was catchable: `check_architecture_coverage.py` gates package counts, this
   script gated one file, and nothing read the rest.

   **Extraction is conservative on purpose**, and mirrors
   `tests/test_fix_text_call_forms.py`'s discipline rather than duplicating its
   corpora (that test covers `src/`'s `fix=` text, the vault-template prompts
   and `commands/`; this covers `docs/` and `DESIGN.md`). Only a backticked span
   or a fenced line is a candidate, and only two shapes inside one are inspected
   (the second, needing call-position prose, cannot occur in a fence):

     * a span carrying ``action=`` -- the head must be a registered tool, the
       action one of *its* actions, and any ``<verb>_action`` must both belong to
       the selected verb and name one of that verb's own actions;
     * a bare identifier in **call position** (after "call", "route through") that
       names a lane verb -- reachable only as an action, so offering it bare is
       exactly the `create_topic` mistake. Call position is the whole rule: prose
       names a verb as a *subject* constantly ("the `loop` verb", "`datasets`
       inventory"), and inspecting every such mention reports 150 findings and 0
       defects. Measured on this tree before the rule was narrowed.

   Parameter names, statuses, filenames, prose and CLI lines fall outside both
   and are never inspected.

   **Deliberately-historical names get a marker, not an exemption.** A migration
   mapping table and a breaking-change note *must* print the dead name -- that is
   their entire job. Wrap such a region in
   ``<!-- surface-history-begin: why -->`` / ``<!-- surface-history-end -->`` and
   this check skips it. The marker is visible in the source, greppable, and
   scoped: it cannot silence a whole file by accident.

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
DOCS_DIR = REPO_ROOT / "docs"
REFERENCE = DOCS_DIR / "reference.md"
DESIGN = REPO_ROOT / ".ai-state" / "DESIGN.md"
COMMANDS_DIR = REPO_ROOT / "commands"
SRC_DIR = REPO_ROOT / "src"
SESSION_HOOK = REPO_ROOT / "hooks" / "session_start.sh"
SKILL = REPO_ROOT / "skills" / "wiki-maintenance" / "SKILL.md"

#: The nine topical dispatchers, each of which declares its own `_ACTIONS`.
#: Eight of them no longer register a tool -- the lanes absorbed them -- but
#: their action tuples are still the vocabulary published prose is resolved
#: against, which is what lets check 3 catch a reference to a name that is now
#: gone. `_actions()` reads them from the module either way.
TOPICAL_DISPATCHERS = (
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

#: The six process lanes. Their action tables are generated from the process
#: model rather than declared as an `_ACTIONS` tuple, so they resolve through
#: `lane_actions()`. Without them, `learn action=create_topic` in a description
#: is unresolvable and check 3 silently stops covering the surface prose that
#: replaced the topical dispatchers'.
LANE_DISPATCHERS = ("home", "learn", "answer", "improve", "fill", "tend")

#: Every name a `<tool> action=<x>` reference may resolve against.
DISPATCHERS = TOPICAL_DISPATCHERS + LANE_DISPATCHERS

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
#: A fenced block's body. `[^`]` on the info string so an inline span cannot be
#: mistaken for an opening fence.
_FENCE = re.compile(r"^ *(?P<ticks>```|~~~)[^\n`]*\n(?P<body>.*?)^ *(?P=ticks) *$", re.M | re.S)
#: An `allowed-tools:` frontmatter entry, inline or as an indented list.
_ALLOWED_TOOLS = re.compile(
    r"^allowed-tools:(?P<inline>[^\n]*)\n(?P<items>(?:[ \t]+-[^\n]*\n)*)", re.M
)
#: The region markers that exempt a deliberately-historical name (a migration
#: mapping table, a breaking-change note). Scoped, greppable, visible in source.
#: Shared by checks 3 and 4 -- both now read fenced blocks, and a migration note
#: prints its dead invocation in a fence as often as in a span.
_HISTORY_REGION = re.compile(
    r"<!--\s*surface-history-begin.*?-->.*?<!--\s*surface-history-end\s*-->", re.S
)


def _fenced_and_frontmatter(text: str) -> list[str]:
    """Invocation-bearing lines that live outside every inline backtick span.

    A fenced block is code position by this gate's own rule -- the
    ordinary-English trap that forces the backtick requirement everywhere else
    cannot apply inside one -- and a slash command's canonical invocation
    usually lives in exactly one fence. `allowed-tools:` frontmatter is code
    position for the same reason: every entry names a command the runtime is
    being told to permit. Neither was scanned, which is how
    `commands/guillotine.md` shipped `knotica guillotine` in *both* long after
    the CLI nested its lanes, with this gate green throughout.

    One line per fragment, so a call-form is resolved against the line that
    published it rather than against a whole block of unrelated output.
    """
    lines: list[str] = []
    for block in _FENCE.finditer(text):
        lines.extend(block.group("body").splitlines())
    for allowed in _ALLOWED_TOOLS.finditer(text):
        lines.append(allowed.group("inline"))
        lines.extend(item.lstrip(" \t-") for item in allowed.group("items").splitlines())
    return [stripped for line in lines if (stripped := line.strip())]


def _actions() -> dict[str, tuple[str, ...]]:
    """Each dispatcher's action table, read from whatever declares it.

    A topical dispatcher declares an `_ACTIONS` tuple in its own module; a lane
    generates one from the process model. Both are read live, so neither can be
    restated here and drift.
    """
    from knotica.mcp_server.tools_dispatch_lane_common import lane_actions

    resolved: dict[str, tuple[str, ...]] = {}
    for name in TOPICAL_DISPATCHERS:
        module = importlib.import_module(f"knotica.mcp_server.tools_dispatch_{name}")
        actions = getattr(module, "_ACTIONS", None)
        if actions:
            resolved[name] = tuple(actions)
    for lane in LANE_DISPATCHERS:
        actions = lane_actions(lane)
        if actions:
            resolved[lane] = actions
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

    Invocations are only recognised **in code position** -- inside a backtick span,
    a fenced block or an `allowed-tools:` entry for prose, or bare in a shell
    script. "knotica" is used adjectivally all over this project ("a knotica
    vault", "your knotica wiki", "knotica so that..."), so matching
    `knotica <word>` in running prose reports the English word after it as a
    subcommand. Measured: that mistake produced 17 false findings and 0 real ones.

    Only the **call-form** rules run over a fence, never the bare-identifier one:
    a fence often carries output rather than an invocation, and a bare token
    there is as likely to be a JSON key or a filename as a tool name.
    """
    failures: list[str] = []
    # Code position: a shell script is all code; prose is code inside backticks,
    # plus the fences and `allowed-tools:` entries a reader also executes verbatim.
    if shell:
        spans, blocks = [text], []
    else:
        scannable = _HISTORY_REGION.sub("", text)
        spans = [span.strip() for span in _BACKTICKED.findall(scannable)]
        blocks = _fenced_and_frontmatter(scannable)

    for token in spans:
        if token in live.tools or "_" not in token or " " in token:
            continue
        head, _, tail = token.partition("_")
        # A consolidated-away tool: `<dispatcher>_<that dispatcher's own action>`.
        if tail in live.actions.get(head, ()):
            failures.append(
                f"{where}: `{token}` names no registered tool — "
                f"`{head}` was consolidated, so this is `{head} action={tail}`"
            )

    for fragment in spans + blocks:
        for match in _DISPATCH_CALL.finditer(fragment):
            tool, action = match.group("tool"), match.group("action")
            if tool not in live.actions:
                continue
            if tool not in live.tools:
                failures.append(
                    f"{where}: `{tool} action={action}` — {tool!r} is no longer registered; "
                    f"its actions were absorbed into a lane"
                )
            elif action not in live.actions[tool]:
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
    failures.extend(_check_published_forms(live))
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


# ---------------------------------------------------------------------------
# Check 4 -- published call-forms in the rest of docs/ and in DESIGN.md
# ---------------------------------------------------------------------------

#: A bare snake_case identifier -- the only bare shape that can name a tool.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
#: A placeholder inside a call-form: an ellipsis or an angle-bracket slot. A form
#: written `improve action=loop loop_action=...` is teaching the *shape*, so its
#: argument values are not names to resolve.
_PLACEHOLDER = re.compile(r"[.\u2026<>]")
#: The prose that puts a following span in *call position*. Deliberately short:
#: each phrase is an imperative aimed at the reader, so a name after one is being
#: offered as an executable step rather than mentioned as a subject. This is the
#: only route by which a bare name is inspected -- documentation names a verb as a
#: subject constantly ("the `loop` verb", "`datasets` inventory"), and treating
#: every such mention as a call reports 150 findings and 0 defects.
_CALL_POSITION = re.compile(r"(?:[Cc]alls?|[Cc]alling|routes? through|runs? through)\s+`([^`\n]+)`")


def _lane_verbs(live: _Surface) -> frozenset[str]:
    """Every verb some lane declares -- the vocabulary a bare name is resolved against."""
    return frozenset(verb for lane in LANE_DISPATCHERS for verb in live.actions.get(lane, ()))


def _enumerated(value: str) -> list[str]:
    """The action names in one argument value.

    Documentation writes an action *set* where code writes one value --
    ``vault action=use/add/create``, ``arena_action=status|history`` -- and in a
    Markdown table the pipe arrives escaped. Split on both separators and drop the
    escape so an enumeration is checked member-by-member rather than reported
    whole as a name nothing declares.
    """
    return [one for one in re.split(r"[/|]", value.replace("\\", "")) if one]


def _call_form_problem(span: str, live: _Surface) -> str | None:
    """Why ``span`` is not a call a client can make, or ``None`` when it is."""
    tokens = span.split()
    head = tokens[0]
    if "=" in head:  # `action=status`, with the tool named in surrounding prose
        return None
    arguments: dict[str, str] = {}
    for token in tokens[1:]:
        key, separator, value = token.partition("=")
        if separator:
            arguments[key] = value.strip("\"'`,.")
    verb = arguments.get("action")
    if verb is None:
        return None
    if _PLACEHOLDER.search(verb):  # `<lane> action=<verb>` teaches shape, not a name
        return None
    if head not in live.tools:
        return f"`{span}` — {head!r} is not a registered tool"
    legal = live.actions.get(head)
    if legal is None:
        return f"`{span}` — {head!r} takes no `action`"
    for one in _enumerated(verb):
        if one not in legal:
            return f"`{span}` — {head!r} has no action {one!r} (has: {', '.join(legal)})"
    for key, value in arguments.items():
        if key == "action" or not key.endswith("_action"):
            continue
        owner = key.removesuffix("_action")
        if owner != verb:
            return f"`{span}` — {key!r} selects inside {owner!r}, but the action is {verb!r}"
        inner = live.actions.get(owner)
        if inner is None or _PLACEHOLDER.search(value):
            continue
        for one in _enumerated(value):
            if one not in inner:
                return f"`{span}` — {owner!r} has no {key} {one!r} (has: {', '.join(inner)})"
    return None


def _published_form_failures(where: str, text: str, live: _Surface) -> list[str]:
    """Every dead call-form published in one document."""
    verbs = _lane_verbs(live)
    scannable = _HISTORY_REGION.sub("", text)
    in_call_position = set(_CALL_POSITION.findall(scannable))
    failures: list[str] = []
    # A fenced line is code position too, and a doc's canonical call-form usually
    # lives in one. Only the `action=` rule runs over it: the bare-name rule needs
    # `_CALL_POSITION` prose, which by construction cannot occur inside a fence.
    for span in _BACKTICKED.findall(scannable) + _fenced_and_frontmatter(scannable):
        cleaned = span.strip()
        if "action=" in cleaned:
            problem = _call_form_problem(cleaned, live)
            if problem is not None:
                failures.append(f"{where}: {problem}")
            continue
        if span not in in_call_position:
            continue
        bare = cleaned.split("(")[0].strip()
        if _IDENTIFIER.match(bare) and bare not in live.tools and bare in verbs:
            failures.append(
                f"{where}: `{span}` is offered as a call but names no registered tool — "
                f"it is a lane action, reachable only as `<lane> action={bare}`"
            )
    return failures


def _check_published_forms(live: _Surface) -> list[str]:
    """`docs/**/*.md` and the design canon, resolved against the live registry."""
    failures: list[str] = []
    documents = sorted(DOCS_DIR.rglob("*.md"))
    # Absence is not a finding here: the architecture-coverage gate owns DESIGN.md's
    # existence, and the synthetic trees the gate tests run against have no .ai-state/.
    if DESIGN.is_file():
        documents.append(DESIGN)
    for path in documents:
        failures.extend(
            _published_form_failures(
                path.relative_to(REPO_ROOT).as_posix(),
                path.read_text(encoding="utf-8"),
                live,
            )
        )
    return failures


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
