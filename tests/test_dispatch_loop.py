"""Validation-path suite for the `loop` dispatcher.

The dispatcher lives in `mcp_server/tools_dispatch_loop.py`
(`register_dispatch_loop_tools`) and wraps the four thin `loop_*` tools
(`loop_run_once`, `loop_set_baseline`, `loop_baseline_policy`,
`loop_rebaseline`).

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

VALID_ACTIONS = {"run_once", "set_baseline", "baseline_policy", "rebaseline"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_loop import register_dispatch_loop_tools

    return build_dispatch_server(register_dispatch_loop_tools)


def test_loop_dispatcher_registers_a_single_tool_documenting_the_four_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "loop" in tools
    rendered = f"{tools['loop'].description or ''} {tools['loop'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "loop", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_set_baseline_missing_scalar_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "loop", {"action": "set_baseline", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "scalar" in text


def test_baseline_policy_missing_policy_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    """Mirrors ``set_baseline``'s ``_require_scalar`` guard: an absent
    ``policy`` must be caught before falling through to
    ``LoopRunner.set_baseline_policy`` (which would otherwise report
    ``NOT_CONFIGURED`` for an empty string, an argument problem misfiled as a
    configuration one). See LEARNINGS.md for the discrepancy this test caught
    mid-session, since resolved."""
    del vault_config
    result = call_tool(_dispatch_server(), "loop", {"action": "baseline_policy", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "policy" in text
