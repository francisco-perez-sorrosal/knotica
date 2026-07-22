"""Validation-path suite for the `arena` dispatcher.

The dispatcher lives in `mcp_server/tools_dispatch_arena.py`
(`register_dispatch_arena_tools`) and wraps `arena_status`/`arena_history`
from `mcp_server/tools_arena.py`.

Equivalence-vs-deprecated-alias coverage was removed once the deprecated flat
tools were deleted from the server -- there is no longer a second surface to
compare against. Only the dispatcher's own validation behavior is covered
here.
"""

from __future__ import annotations

from pathlib import Path

from support.dispatch import (
    TOPIC,
    build_dispatch_server,
    call_tool,
    list_tools,
    rendered_error_text,
)

VALID_ACTIONS = {"status", "history"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_arena import register_dispatch_arena_tools

    return build_dispatch_server(register_dispatch_arena_tools)


def test_arena_dispatcher_registers_a_single_tool_documenting_the_two_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "arena" in tools
    rendered = f"{tools['arena'].description or ''} {tools['arena'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "arena", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"
