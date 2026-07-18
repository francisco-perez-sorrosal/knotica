"""MCP ``baseline_probe`` tool contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from knotica.core.metrics import BASELINE_PROBE_HARNESS_VERSION

TOPIC = "agentic-systems"


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(tool: str, args: dict[str, Any]) -> Any:
    return anyio.run(_call, _build_server(), tool, args)


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    if isinstance(body, dict):
        assert "error" not in body, f"expected success, got error: {body!r}"
    assert getattr(result, "isError", False) is False
    return body


def test_baseline_probe_tool_is_registered() -> None:
    server = _build_server()

    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "baseline_probe" in names


def test_baseline_probe_persists_and_wiki_status_reads_it(
    vault_config: Path, template_vault: Path
) -> None:
    _ = vault_config, template_vault

    body = assert_success(call_tool("baseline_probe", {"topic": TOPIC}))
    assert body["harness_version"] == BASELINE_PROBE_HARNESS_VERSION
    assert body["runner_mode"] == "zero_anchor"
    assert body["persisted"] is True
    assert body["scalar"] == pytest.approx(0.0)

    status = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    assert status["gate"]["last_scalar"] == pytest.approx(0.0)
    assert status["topics"][0]["last_eval"]["harness_version"] == BASELINE_PROBE_HARNESS_VERSION
