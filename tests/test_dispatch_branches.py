"""Validation-path suite for the `branches` dispatcher.

The dispatcher lives in `mcp_server/tools_dispatch_branches.py`
(`register_dispatch_branches_tools`) and wraps `branch_scoreboard`,
`loop_promote`, `branch_promote`, and `branch_delete` from
`mcp_server/tools_scoreboard.py`.

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

VALID_ACTIONS = {"scoreboard", "promote_loop", "promote", "delete"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_branches import register_dispatch_branches_tools

    return build_dispatch_server(register_dispatch_branches_tools)


def test_branches_dispatcher_registers_a_single_tool_documenting_the_four_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "branches" in tools
    rendered = f"{tools['branches'].description or ''} {tools['branches'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "branches", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_promote_loop_missing_branch_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "branches", {"action": "promote_loop", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "branch" in text


def test_delete_missing_branch_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "branches", {"action": "delete", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "branch" in text


def test_promote_missing_kind_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(
        _dispatch_server(),
        "branches",
        {"action": "promote", "topic": TOPIC, "branch": "loop/agentic-systems/deadbeef"},
    )
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "kind" in text
