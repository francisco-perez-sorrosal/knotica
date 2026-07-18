"""MCP compile tools — compile_promote registration and dry-run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.compile_run import run_compile
from support.trainset import populate_query_trainset
from knotica.programs.query import bootstrap_query_artifact
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


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
    raise AssertionError(f"no payload: {result!r}")


def test_compile_promote_is_registered() -> None:
    from knotica.mcp_server.server import build_server

    mcp = build_server()
    names = {tool.name for tool in mcp._tool_manager.list_tools()}  # noqa: SLF001
    assert "compile_promote" in names


def test_compile_promote_mcp_dry_run(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    populate_query_trainset(store, template_vault, TOPIC)
    result = run_compile(
        store,
        template_vault,
        TOPIC,
        use_mipro=False,
        optimize_fn=lambda s, t, train, **k: bootstrap_query_artifact(s, t, train, golden_n=20),
        compare_fn=lambda *a: (0.41, 0.72),
    )
    assert result.branch is not None

    payload = payload_of(
        call_tool(
            "compile_promote",
            {"topic": TOPIC, "branch": result.branch, "mode": "dry-run"},
        )
    )
    assert payload["mode"] == "dry-run"
    assert payload["merged"] is False
    assert payload["branch"] == result.branch
