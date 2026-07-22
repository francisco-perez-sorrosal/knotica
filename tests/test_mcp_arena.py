"""MCP arena_status / arena_history tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.arena import VariantSpec, race_variants
from knotica.store import LocalFSStore


#: `arena_status`/`arena_history` were removed -- the flat aliases were fully
#: retired, not deprecated; route each through the `arena` dispatcher.
_DISPATCHER_ACTIONS = {
    "arena_status": ("arena", "status"),
    "arena_history": ("arena", "history"),
}


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    dispatcher, action = _DISPATCHER_ACTIONS.get(tool, (tool, None))
    call_args = args if action is None else {"action": action, **args}
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(dispatcher, call_args)


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
    raise AssertionError(f"no payload: {result!r}")


def test_arena_tools_registered() -> None:
    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "arena" in names
    assert "query" in names
    assert "wiki_query" not in names


def test_arena_status_idle_then_after_race(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    idle = payload_of(call_tool("arena_status", {"topic": "agentic-systems"}))
    assert "error" not in idle
    assert idle["stage"] == "idle"
    assert idle["variants"] == []

    store = LocalFSStore(template_vault)
    race_variants(
        store,
        template_vault,
        "agentic-systems",
        [
            VariantSpec(id="v1", label="a", body="# a\n"),
            VariantSpec(id="v2", label="b", body="# b\n"),
        ],
        baseline_scalar=0.5,
        score=lambda _t, _r, body: 0.9 if "# b" in body else 0.1,
    )
    status = payload_of(call_tool("arena_status", {"topic": "agentic-systems"}))
    assert status["stage"] == "completed"
    assert status["winner_id"] == "v2"
    history = payload_of(call_tool("arena_history", {"topic": "agentic-systems", "limit": 5}))
    assert history["races"]
