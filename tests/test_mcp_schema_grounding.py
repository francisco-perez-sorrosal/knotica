"""Census: every published parameter is grounded, and every enum is derived.

Before this band, the whole surface published 236 parameters with **zero**
schema descriptions and one advisory enum: each property was
``{"title": "Suggestion Id", "type": "string"}``, a title auto-derived from the
name that carried nothing the name did not. The legal values of ``decision``,
``mode``, ``status``, ``target`` and ``verdict`` lived only in tool-description
prose, kilobytes from the field they constrain.

The three groups below hold the fix mechanically rather than by diligence:

* **G1** -- coverage. At least 95% of the surface's properties carry a
  ``description``, and the exceptions are named one by one, so an
  *un*-annotated new parameter fails here instead of joining a silent tail.
* **G2** -- derivation. Each ``<verb>_action`` enum equals its own module's
  ``_ACTIONS`` constant, by import. A restated list would pass a shape check
  and drift on the next action added; reading the constant cannot.
* **G3** -- the advisory rule. No enum is enforced by pydantic. A ``Literal``
  would have the host answer an unknown action with a raw validation string,
  replacing the typed ``{code, message, fix, retryable}`` envelope and losing
  the ``record_rejected_action`` signal -- so the rejection must still come
  back as an envelope, not as a schema error.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from support.dispatch import build_full_server, call_tool, list_tools, payload_of, tool_schema

#: Floor from the finding this band closes. Coverage sits at ~97%; the seven
#: exceptions below are the deliberate remainder, not headroom to spend.
_MINIMUM_DESCRIBED_FRACTION = 0.95

#: The parameters that deliberately publish no description, and why. Each is a
#: lane union whose contributing verbs mean genuinely different things by the
#: name, so `_optional` degrades to the plain type rather than publishing one
#: verb's semantics over another's (see `tools_dispatch_lane_common._optional`).
#: The grounding survives on each verb's own flat schema.
_UNDESCRIBED_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # `notes` filters by intent; `note_capture` sets one.
        ("answer", "intent"),
        ("tend", "intent"),
        # dry-run|apply, best|latest and compile|loop all arrive as `mode`.
        ("fill", "mode"),
        ("improve", "mode"),
        ("tend", "mode"),
        # gap statuses vs suggestion statuses vs journal-event statuses.
        ("fill", "status"),
        # `datasets` counts examples (int); `notes` names a promotion target.
        ("improve", "target"),
    }
)

#: Each lane-renamed selector and the module whose `_ACTIONS` it must equal.
_ACTION_SELECTOR_MODULES: dict[str, str] = {
    "arena_action": "knotica.mcp_server.tools_dispatch_arena",
    "branches_action": "knotica.mcp_server.tools_dispatch_branches",
    "compile_action": "knotica.mcp_server.tools_dispatch_compile",
    "datasets_action": "knotica.mcp_server.tools_dispatch_datasets",
    "golden_action": "knotica.mcp_server.tools_dispatch_golden",
    "loop_action": "knotica.mcp_server.tools_dispatch_loop",
    "notes_action": "knotica.mcp_server.tools_dispatch_notes",
    "suggestions_review_action": "knotica.mcp_server.tools_suggestions",
}
# `vault` is unlaned, so its own `action` is never renamed -- it has its own
# test below rather than a row here.


def _properties(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(schema.get("properties") or {})


def _published_enum(prop: dict[str, Any]) -> list[str] | None:
    """The advisory enum on a property, whether bare or under an ``anyOf`` arm."""
    if "enum" in prop:
        return list(prop["enum"])
    for arm in prop.get("anyOf") or []:
        if "enum" in arm:
            return list(arm["enum"])
    return None


@pytest.fixture(scope="module")
def surface() -> list[Any]:
    return list_tools(build_full_server())


# --- G1: coverage -----------------------------------------------------------


def test_at_least_95_percent_of_parameters_carry_a_description(surface: list[Any]) -> None:
    total = 0
    described = 0
    for tool in surface:
        for prop in _properties(tool.inputSchema).values():
            total += 1
            described += bool(prop.get("description"))
    assert total > 0
    assert described / total >= _MINIMUM_DESCRIBED_FRACTION, (
        f"{described}/{total} parameters described "
        f"({described / total:.1%} < {_MINIMUM_DESCRIBED_FRACTION:.0%})"
    )


def test_undescribed_parameters_are_exactly_the_named_exceptions(surface: list[Any]) -> None:
    missing = {
        (tool.name, name)
        for tool in surface
        for name, prop in _properties(tool.inputSchema).items()
        if not prop.get("description")
    }
    assert missing == set(_UNDESCRIBED_ALLOWLIST)


# --- G2: derivation ---------------------------------------------------------


def test_lane_selector_enum_is_the_lane_action_table() -> None:
    from knotica.mcp_server.tools_dispatch_lane_common import lane_actions

    server = build_full_server()
    for lane in ("learn", "answer", "improve", "fill", "tend"):
        published = _published_enum(_properties(tool_schema(server, lane))["action"])
        assert published == list(lane_actions(lane)), lane


@pytest.mark.parametrize(("selector", "module_name"), sorted(_ACTION_SELECTOR_MODULES.items()))
def test_wrapped_verb_action_enum_equals_its_modules_actions(
    surface: list[Any], selector: str, module_name: str
) -> None:
    expected = list(importlib.import_module(module_name)._ACTIONS)
    seen = [
        _published_enum(_properties(tool.inputSchema)[selector])
        for tool in surface
        if selector in _properties(tool.inputSchema)
    ]
    assert seen, f"{selector} is published by no tool"
    assert all(published == expected for published in seen), (selector, seen, expected)


def test_flat_vault_action_enum_equals_its_modules_actions(surface: list[Any]) -> None:
    from knotica.mcp_server.tools_dispatch_vault import _ACTIONS

    published = _published_enum(_properties(tool_schema(build_full_server(), "vault"))["action"])
    assert published == list(_ACTIONS)


# --- G3: advisory, never enforced -------------------------------------------


def test_an_unknown_action_still_returns_a_typed_envelope_not_a_validation_string() -> None:
    """The enum must be advisory: a bad value reaches `_reject`, not pydantic."""
    result = call_tool(build_full_server(), "fill", {"action": "suggestion_review"})
    payload = payload_of(result)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["error"]["fix"].startswith("Pass action as one of:")
