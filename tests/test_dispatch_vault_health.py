"""Validation-path suite for the `vault_health` dispatcher.

The dispatcher lives in `mcp_server/tools_dispatch_vault_health.py`
(`register_dispatch_vault_health_tools`) and wraps `doctor_run`,
`doctor_repair`, `okf_check`, `okf_repair`, `vault_lint`, and
`vault_metadata_tree` from `mcp_server/tools_vault.py`.

Equivalence-vs-deprecated-alias coverage was removed once the deprecated flat
tools were deleted from the server -- there is no longer a second surface to
compare against. Only the dispatcher's own validation behavior is covered
here.
"""

from __future__ import annotations

from pathlib import Path

from support.dispatch import (
    build_dispatch_server,
    call_tool,
    list_tools,
    rendered_error_text,
)

VALID_ACTIONS = {"doctor", "repair", "okf_check", "okf_repair", "lint", "metadata_tree"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_vault_health import (
        register_dispatch_vault_health_tools,
    )

    return build_dispatch_server(register_dispatch_vault_health_tools)


def test_vault_health_dispatcher_registers_a_single_tool_documenting_the_six_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "vault_health" in tools
    rendered = f"{tools['vault_health'].description or ''} {tools['vault_health'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(vault_config: Path) -> None:
    result = call_tool(_dispatch_server(), "vault_health", {"action": "explode"})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"
