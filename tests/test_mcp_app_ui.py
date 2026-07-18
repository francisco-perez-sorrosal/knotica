"""Behavioral tests for the MCP App dashboard mount (M4).

Pins the wire contract a Claude Desktop host sees:

- ``ui://knotica/dashboard`` is advertised and reads as
  ``text/html;profile=mcp-app`` containing the packaged artifact;
- ``open_dashboard`` is registered with ``_meta.ui.resourceUri`` pointing at
  that resource, and returns graceful fallback text for hosts without Apps.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.types import TextContent
from pydantic import AnyUrl

from knotica.dashboard import dashboard_html
from knotica.mcp_server.app_ui import DASHBOARD_URI, MCP_APP_MIME

TOPIC = "agentic-systems"


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _list_resources(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.list_resources()


async def _read_resource(server: Any, uri: str) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.read_resource(AnyUrl(uri))


async def _list_tools(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.list_tools()


async def _call_tool(server: Any, name: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(name, args)


def test_dashboard_ui_resource_is_advertised() -> None:
    result = anyio.run(_list_resources, _build_server())
    uris = [str(r.uri) for r in result.resources]
    assert DASHBOARD_URI in uris, f"dashboard ui:// resource missing: {uris!r}"


def test_dashboard_ui_resource_reads_as_mcp_app_html() -> None:
    result = anyio.run(_read_resource, _build_server(), DASHBOARD_URI)
    assert result.contents, "ui:// dashboard returned empty contents"
    first = result.contents[0]
    mime = getattr(first, "mimeType", None)
    text = getattr(first, "text", "") or ""
    assert mime == MCP_APP_MIME, f"expected {MCP_APP_MIME}, got {mime!r}"
    assert '<div id="app">' in text
    # Same bytes the HTTP mount serves — one artifact, two transports.
    assert text == dashboard_html()


def test_open_dashboard_tool_carries_ui_resource_meta() -> None:
    listed = anyio.run(_list_tools, _build_server())
    tool = next((t for t in listed.tools if t.name == "open_dashboard"), None)
    assert tool is not None, "open_dashboard tool not registered"
    meta = getattr(tool, "meta", None) or {}
    ui = meta.get("ui") if isinstance(meta, dict) else None
    assert isinstance(ui, dict), f"open_dashboard missing meta.ui: {meta!r}"
    assert ui.get("resourceUri") == DASHBOARD_URI
    # Legacy key still present for older hosts (qr-server crib).
    assert meta.get("ui/resourceUri") == DASHBOARD_URI


def test_open_dashboard_returns_graceful_fallback_text() -> None:
    result = anyio.run(_call_tool, _build_server(), "open_dashboard", {"topic": TOPIC})
    assert getattr(result, "isError", False) is False
    texts = [
        block.text
        for block in (getattr(result, "content", []) or [])
        if isinstance(block, TextContent) or getattr(block, "type", None) == "text"
    ]
    body = "\n".join(texts)
    assert TOPIC in body
    assert "MCP Apps" in body
    assert "knotica mcp --http" in body
    assert f"topic={TOPIC}" in body
