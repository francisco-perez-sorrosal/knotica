"""Behavioral tests for the operation-guide tool over an in-memory client session.

``read_protocol`` is the tool that makes a plain "ingest this paper" complete the
whole protocol on a client (e.g. Claude Desktop) whose UI does not surface MCP
prompts: it returns the same vault-resolved operation body the ``prompts/get``
handler serves, so the model can load the steps from a natural-language request
rather than firing one tool call and stopping. These assertions pin the wire
contract a real MCP client sees (``tools/list`` + ``tools/call``), plus the
server-level ``instructions`` nudge that points the model at the tool.

Async coroutines are driven from sync bodies via ``anyio.run``; server imports
are deferred into helpers to match the sibling MCP band tests.
"""

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.prompts import get_prompt


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


async def _call_tool(server: Any, name: str, arguments: dict[str, str]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(name, arguments)


async def _initialize(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        return await session.initialize()


async def _list_tool_names(server: Any) -> set[str]:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        result = await session.list_tools()
        return {t.name for t in result.tools}


def _payload(result: Any) -> dict[str, Any]:
    """The tool's structured envelope, from structuredContent or the JSON text."""
    if result.structuredContent is not None:
        return dict(result.structuredContent)
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# The tool is registered alongside the rest of the surface.
# ---------------------------------------------------------------------------


def test_read_protocol_is_a_registered_tool() -> None:
    """The guide tool joins the deterministic tool surface so a client can call
    it from natural language (unlike a prompt, which the user must insert)."""
    assert "read_protocol" in anyio.run(_list_tool_names, _build_server())


# ---------------------------------------------------------------------------
# Configured: returns the vault's resolved protocol body — one source of truth.
# ---------------------------------------------------------------------------


def test_read_protocol_returns_the_configured_ingest_protocol(
    vault_config: Path, template_vault: Path
) -> None:
    """A configured call returns the vault ingest body, marked configured, and
    the body carries the full multi-step sequence (not just storing a source)."""
    result = anyio.run(_call_tool, _build_server(), "read_protocol", {"operation": "ingest"})
    assert result.isError is False
    payload = _payload(result)
    assert payload["operation"] == "ingest"
    assert payload["configured"] is True
    assert "store_source" in payload["protocol"]
    assert "write_page" in payload["protocol"], "the ingest protocol must include writing pages"


def test_read_protocol_body_equals_the_prompt_resolver_output(
    vault_config: Path, template_vault: Path
) -> None:
    """Single source of truth: the tool serves the byte-identical body the MCP
    prompt surface and the CLI serve, via the shared core.prompts resolver."""
    result = anyio.run(_call_tool, _build_server(), "read_protocol", {"operation": "query"})
    assert _payload(result)["protocol"] == get_prompt("query", "").body


# ---------------------------------------------------------------------------
# Unconfigured: graceful setup guidance (mirrors the prompt surface), not error.
# ---------------------------------------------------------------------------


def test_read_protocol_unconfigured_returns_setup_guidance_not_an_error(
    unconfigured_env: Path,
) -> None:
    """With no vault, the tool degrades like the prompt surface: a success
    result carrying setup guidance (configured=False), never a transport fault."""
    result = anyio.run(_call_tool, _build_server(), "read_protocol", {"operation": "ingest"})
    assert result.isError is False
    payload = _payload(result)
    assert payload["configured"] is False
    assert "not configured" in payload["protocol"].lower()


# ---------------------------------------------------------------------------
# Unknown operation: rejected at the schema layer (Literal enum), naming valids.
# ---------------------------------------------------------------------------


def test_read_protocol_rejects_an_unknown_operation(vault_config: Path) -> None:
    """The operation argument is a Literal enum, so the SDK rejects an unknown
    operation before the body runs and names the allowed values to the model."""
    result = anyio.run(_call_tool, _build_server(), "read_protocol", {"operation": "frobnicate"})
    assert result.isError is True
    assert "ingest" in result.content[0].text, "the rejection should name valid operations"


# ---------------------------------------------------------------------------
# Server instructions (the nudge): point the model at read_protocol up front.
# ---------------------------------------------------------------------------


def test_server_instructions_point_the_model_at_read_protocol() -> None:
    """The initialize handshake carries instructions that tell the model the
    operations are multi-step and to load one via read_protocol before acting —
    the nudge that makes a plain request complete the whole sequence."""
    result = anyio.run(_initialize, _build_server())
    instructions = result.instructions or ""
    assert "read_protocol" in instructions
    assert "ingest" in instructions
