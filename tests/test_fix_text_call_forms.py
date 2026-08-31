"""Every call-form remediation text names is one a client can actually call.

Under `dec-050` there is no alias layer: a name the lane re-cut removed from
the published surface returns an unknown-tool error, loudly. So a `fix=` string
or a seeded prompt that names a removed verb is a hard dead end for a model in
the one turn where it was supposed to self-recover -- it reads the fix, calls
the name, and now has two failures and no path forward.

Four corpora carry that risk, and all four are gated here: the canonical
`DEFAULT_FIX` table, every `fix` string literal under `src/knotica/`, the
vault-template prompts (which are simultaneously the MCP-prompt UX surface and
the DSPy/SIA-evolvable substrate -- a name that dangles there gets optimized
*around* rather than fixed), and the plugin's slash commands.

**Both vocabularies are derived, never listed here.** The registered tool names
come from `list_tools()` on the real server; the legal ``<lane> action=<verb>``
pairs from `lane_actions`, which is itself a projection of `LANE_MEMBERSHIP`;
and a wrapped dispatcher's ``<verb>_action`` values from that verb's own module
``_ACTIONS`` tuple. Adding an action cannot make this file stale.

Extraction is deliberately conservative -- a backticked span is only a
candidate when it is unambiguously a call, so the gate stays low-noise:

1. a span carrying ``action=`` (a dispatcher call-form);
2. a bare identifier that names a **lane verb** -- reachable as an action, so
   naming it bare is exactly the `create_topic` mistake;
3. a bare identifier in **call position** (after "call", "route through",
   "runs through"), which is how a removed pre-dispatcher name like
   `golden_review_save` reads.

Parameter names, statuses, filenames, `knotica ...` CLI lines and prose all
fall outside those three and are never inspected.
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from support.dispatch import build_full_server, list_tool_names

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPO_ROOT / "src" / "knotica"
_PROMPT_ROOT = _REPO_ROOT / "vault-template" / ".knotica" / "prompts"
_COMMAND_ROOT = _REPO_ROOT / "commands"

#: A backticked span, the unit of inspection in every corpus.
_SPAN = re.compile(r"`([^`\n]+)`")

#: A bare snake_case identifier -- the only bare shape that can name a tool.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

#: The prose that puts a following span in *call position*. Deliberately short:
#: each phrase is an imperative aimed at the client, so a name that follows one
#: is being offered as an executable next step, not mentioned.
_CALL_POSITION = re.compile(
    r"(?:[Cc]alls?|[Cc]alling|routes? through|runs? through|through the)\s+`([^`\n]+)`"
)


# ---------------------------------------------------------------------------
# The vocabularies, all derived.
# ---------------------------------------------------------------------------


@functools.cache
def _registered_tools() -> frozenset[str]:
    """Every tool name a client sees, read off the real server's registry."""
    return frozenset(list_tool_names(build_full_server()))


@functools.cache
def _lanes() -> tuple[str, ...]:
    from knotica.core import process_model

    return tuple(process_model.LANES)


@functools.cache
def _surface_parameters() -> frozenset[str]:
    """Every parameter name some handler on the surface takes.

    A name that is *also* an argument (``notes``, ``index_entry``) is ambiguous
    in a backtick span, so rule 2 steps aside for it and only rule 3 -- an
    explicit "call this" -- can implicate it.
    """
    from knotica.mcp_server.tools_dispatch_lane_common import _flat_handlers

    return frozenset(
        name
        for handler in _flat_handlers().values()
        for name in inspect.signature(handler, eval_str=True).parameters
    )


@functools.cache
def _lane_verbs() -> frozenset[str]:
    """Verbs a lane declares that are unambiguously verbs -- never also an argument."""
    from knotica.mcp_server.tools_dispatch_lane_common import lane_actions

    declared = frozenset(verb for lane in _lanes() for verb in lane_actions(lane))
    return declared - _surface_parameters()


@functools.cache
def _action_set(name: str) -> tuple[str, ...] | None:
    """``name``'s legal action strings, or ``None`` when it takes no ``action``.

    Three sources, one convention: a lane's table is generated from the process
    model; every other dispatcher -- wrapped verb or flat tool -- declares its
    own ``_ACTIONS`` tuple next to its validator, so reading that constant is
    the same lookup in both cases.
    """
    from knotica.mcp_server.tools_dispatch_lane_common import _flat_handlers, lane_actions

    if name in _lanes():
        return lane_actions(name)
    handler = _flat_handlers().get(name)
    if handler is not None:
        module: Any = inspect.getmodule(handler)
        return getattr(module, "_ACTIONS", None)
    try:
        module = importlib.import_module(f"knotica.mcp_server.tools_dispatch_{name}")
    except ModuleNotFoundError:
        return None
    return getattr(module, "_ACTIONS", None)


# ---------------------------------------------------------------------------
# Validation of one candidate span.
# ---------------------------------------------------------------------------


def _head_and_arguments(span: str) -> tuple[str, dict[str, str]]:
    tokens = span.split()
    arguments = {}
    for token in tokens[1:]:
        key, separator, value = token.partition("=")
        if separator:
            arguments[key] = value.strip("\"'`,.")
    return tokens[0], arguments


def _call_form_problem(span: str, tools: frozenset[str]) -> str | None:
    """Why ``span`` is not a call a client can make, or ``None`` when it is."""
    head, arguments = _head_and_arguments(span)
    if "=" in head:
        # `action=status` with the tool named in the surrounding prose.
        return None
    verb = arguments.get("action")
    if verb is None:
        return None
    if head not in tools:
        return f"{head!r} is not a registered tool"
    legal = _action_set(head)
    if legal is None:
        return f"{head!r} is not a tool that takes an `action`"
    if verb not in legal:
        return f"{head!r} has no action {verb!r} (has: {', '.join(legal)})"
    for key, value in arguments.items():
        if key == "action" or not key.endswith("_action"):
            continue
        owner = key.removesuffix("_action")
        if owner != verb:
            return f"{key!r} selects inside {owner!r}, but the action is {verb!r}"
        inner = _action_set(owner)
        if inner is not None and value not in inner:
            return f"{owner!r} has no {key} {value!r} (has: {', '.join(inner)})"
    return None


def _bare_name_problem(name: str, tools: frozenset[str]) -> str | None:
    if name in tools:
        return None
    return f"{name!r} is not a registered tool"


def _problem(span: str, tools: frozenset[str], *, call_position: bool) -> str | None:
    """The reason ``span`` names something uncallable, or ``None``.

    ``call_position`` widens rule 2 (lane verbs) to rule 3 (any bare name the
    prose tells the client to call), which is what catches a pre-dispatcher
    name that belongs to no current vocabulary at all.
    """
    cleaned = span.strip().split("(")[0].strip()
    if "action=" in span:
        return _call_form_problem(span.strip(), tools)
    if not _IDENTIFIER.match(cleaned):
        return None
    if call_position or cleaned in _lane_verbs():
        return _bare_name_problem(cleaned, tools)
    return None


# ---------------------------------------------------------------------------
# The corpora.
# ---------------------------------------------------------------------------


def _string_literals(node: ast.AST) -> str:
    """Every string constant under ``node``, joined -- f-strings included."""
    return " ".join(
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    )


def _fix_strings(path: Path) -> Iterator[str]:
    """Fix text in a module: ``fix=`` kwargs, ``"fix"`` dict values, ``*FIX*`` constants."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "fix":
                    yield _string_literals(keyword.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "fix":
                    yield _string_literals(value)
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and "FIX" in t.id for t in node.targets):
                yield _string_literals(node.value)


def _python_findings(tools: frozenset[str]) -> list[str]:
    findings = []
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        for text in _fix_strings(path):
            findings.extend(_findings_in(text, str(path.relative_to(_REPO_ROOT)), tools))
    return findings


def _markdown_findings(root: Path, tools: frozenset[str]) -> list[str]:
    findings = []
    for path in sorted(root.glob("*.md")):
        findings.extend(
            _findings_in(path.read_text(encoding="utf-8"), str(path.relative_to(_REPO_ROOT)), tools)
        )
    return findings


def _findings_in(text: str, where: str, tools: frozenset[str]) -> list[str]:
    in_call_position = set(_CALL_POSITION.findall(text))
    findings = []
    for span in _SPAN.findall(text):
        problem = _problem(span, tools, call_position=span in in_call_position)
        if problem is not None:
            findings.append(f"{where}: `{span}` -- {problem}")
    return findings


@pytest.fixture(scope="module")
def tools() -> frozenset[str]:
    return _registered_tools()


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


def test_the_canonical_fix_table_names_only_call_forms_a_client_can_reach(
    tools: frozenset[str],
) -> None:
    from knotica.core.errors import DEFAULT_FIX

    findings = [
        finding
        for code, text in DEFAULT_FIX.items()
        for finding in _findings_in(text, f"DEFAULT_FIX[{code.value}]", tools)
    ]
    assert not findings, "DEFAULT_FIX names uncallable forms:\n" + "\n".join(findings)


def test_every_fix_string_in_the_package_names_only_call_forms_a_client_can_reach(
    tools: frozenset[str],
) -> None:
    findings = _python_findings(tools)
    assert not findings, "fix text names uncallable forms:\n" + "\n".join(findings)


def test_the_seeded_prompts_name_only_call_forms_a_client_can_reach(
    tools: frozenset[str],
) -> None:
    findings = _markdown_findings(_PROMPT_ROOT, tools)
    assert not findings, "vault-template prompts name uncallable forms:\n" + "\n".join(findings)


def test_the_slash_commands_name_only_call_forms_a_client_can_reach(
    tools: frozenset[str],
) -> None:
    findings = _markdown_findings(_COMMAND_ROOT, tools)
    assert not findings, "commands name uncallable forms:\n" + "\n".join(findings)


# ---------------------------------------------------------------------------
# The guard bites -- each rule rejects the exact shape it exists to catch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Rule 2: a lane verb named bare, the `create_topic` shape.
        "Call `list_topics` to see valid topics, or `create_topic` to make a new one.",
        # Rule 3: a pre-dispatcher name in call position, the `golden_review_save` shape.
        "call `golden_review_save` to stage held-out candidates",
        # Rule 1: an inner dispatcher addressed as if it were a lane.
        "Rebaseline with `loop action=rebaseline mode=latest`.",
        # Rule 1: the right lane, a selector named the pre-lane way.
        "Approve it first: `fill action=suggestions_review action=approve`.",
        # Rule 1: a real lane, an action that lane does not declare.
        "Call `tend action=create_topic`.",
        # Rule 1: a real wrapped verb, an inner action it does not declare.
        "Call `tend action=notes notes_action=rename`.",
    ],
)
def test_the_gate_rejects_the_dead_forms_it_exists_to_catch(
    text: str, tools: frozenset[str]
) -> None:
    assert _findings_in(text, "<probe>", tools)


@pytest.mark.parametrize(
    "text",
    [
        "Call `list_topics` to see valid topics, or `learn action=create_topic` to make a new one.",
        "call `improve action=golden golden_action=save` to stage held-out candidates",
        "`improve action=loop loop_action=rebaseline mode=latest topic={topic}`.",
        "Restart the search without a cursor.",
        "Run `knotica doctor` / `/knotica:doctor` to inspect and offer rollback.",
        "pass `index_entry` to `write_page` instead of writing `index.md`",
        "call the `vault` MCP tool with `action=status`",
    ],
)
def test_the_gate_passes_the_live_forms_and_the_prose_around_them(
    text: str, tools: frozenset[str]
) -> None:
    assert not _findings_in(text, "<probe>", tools)
