"""Validation-path suite for the `golden` dispatcher.

The dispatcher lives in `mcp_server/tools_dispatch_golden.py`
(`register_dispatch_golden_tools`) and wraps `golden_review_load`/
`golden_review_save` from `mcp_server/tools_golden.py`.

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

VALID_ACTIONS = {"load", "save"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_golden import register_dispatch_golden_tools

    return build_dispatch_server(register_dispatch_golden_tools)


def test_golden_dispatcher_registers_a_single_tool_documenting_the_two_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "golden" in tools
    rendered = f"{tools['golden'].description or ''} {tools['golden'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "golden", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_save_missing_accepted_json_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "golden", {"action": "save", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "accepted_json" in text
