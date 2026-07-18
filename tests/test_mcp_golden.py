"""Behavioral tests for golden_review_load / golden_review_save MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from support.vault import run_git

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


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    assert "error" not in body
    assert getattr(result, "isError", False) is False
    return body


def _seed_staging(vault: Path, *, n: int = 2) -> Path:
    path = vault / TOPIC / ".knotica" / "datasets" / "golden.staging.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(n):
        rows.append(
            {
                "question": f"What is concept {i}?",
                "reference_answer": f"Concept {i} is defined in the wiki.",
                "citations": [],
                "pages_used": [],
            }
        )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_golden_tools_registered() -> None:
    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "golden_review_load" in names
    assert "golden_review_save" in names


def test_golden_review_load_missing_staging(vault_config: Path) -> None:
    del vault_config
    body = payload_of(call_tool("golden_review_load", {"topic": TOPIC}))
    assert body["error"]["code"] == "PAGE_NOT_FOUND"


def test_golden_review_load_and_save(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_staging(template_vault, n=2)
    loaded = assert_success(call_tool("golden_review_load", {"topic": TOPIC}))
    assert loaded["topic"] == TOPIC
    assert loaded["vault_name"]
    assert loaded["vault_path"]
    assert len(loaded["candidates"]) == 2
    assert loaded["floor"] == 20

    accepted = [loaded["candidates"][0]]
    saved = assert_success(
        call_tool(
            "golden_review_save",
            {"topic": TOPIC, "accepted_json": json.dumps(accepted)},
        )
    )
    assert saved["count"] == 1
    reviewed = template_vault / TOPIC / ".knotica" / "datasets" / "golden.staging.reviewed.jsonl"
    assert reviewed.is_file()
    # Save goes through VaultTransaction → one commit.
    assert "golden_review" in run_git(template_vault, "log", "-1", "--pretty=%s")
