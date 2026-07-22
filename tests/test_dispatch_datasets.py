"""Validation-path suite for the `datasets` dispatcher.

The dispatcher lives in `mcp_server/tools_dispatch_datasets.py`
(`register_dispatch_datasets_tools`) and wraps `datasets_inventory`,
`datasets_records`, `datasets_bootstrap`, `datasets_bootstrap_train`, and
`datasets_freeze` from `mcp_server/tools_datasets.py`.

Equivalence-vs-deprecated-alias coverage was removed once the deprecated flat
tools (`datasets_inventory`, etc.) were deleted from the server -- there is no
longer a second surface to compare against. Only the dispatcher's own
validation behavior (unknown action, missing required argument) is covered
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

VALID_ACTIONS = {"inventory", "records", "bootstrap", "bootstrap_train", "freeze"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_datasets import register_dispatch_datasets_tools

    return build_dispatch_server(register_dispatch_datasets_tools)


def test_datasets_dispatcher_registers_a_single_tool_documenting_the_five_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "datasets" in tools
    rendered = f"{tools['datasets'].description or ''} {tools['datasets'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "datasets", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_records_missing_role_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "datasets", {"action": "records", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "role" in text
