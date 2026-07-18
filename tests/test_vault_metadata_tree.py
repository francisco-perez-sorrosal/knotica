"""Tests for vault metadata tree (core + MCP tool)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.vault_metadata_tree import gather_vault_metadata_tree
from knotica.store import LocalFSStore


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


def _paths(nodes: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        out.add(node["path"])
        children = node.get("children") or []
        out.update(_paths(children))
    return out


def _seed_metadata_tree(vault: Path, topic: str = "agentic-systems") -> None:
    root_knotica = vault / ".knotica" / "prompts"
    root_knotica.mkdir(parents=True, exist_ok=True)
    (root_knotica / "query.md").write_text("# query\n", encoding="utf-8")

    topic_knotica = vault / topic / ".knotica"
    (topic_knotica / "datasets").mkdir(parents=True, exist_ok=True)
    (topic_knotica / "datasets" / "qa.jsonl").write_text("{}\n", encoding="utf-8")
    (topic_knotica / "loop-state.json").write_text('{"schema_version":1}\n', encoding="utf-8")


def test_gather_vault_metadata_tree_lists_existing_paths(template_vault: Path) -> None:
    _seed_metadata_tree(template_vault)
    store = LocalFSStore(template_vault)
    payload = gather_vault_metadata_tree(store, template_vault)

    assert payload["schema_version"] == 1
    assert payload["topic"] is None
    paths = _paths(payload["children"])
    assert "SCHEMA.md" in paths
    assert "log.md" in paths
    assert ".knotica/prompts/query.md" in paths
    assert "agentic-systems/.knotica/loop-state.json" in paths
    assert "agentic-systems/.knotica/datasets/qa.jsonl" in paths
    assert "agentic-systems/SCHEMA.md" in paths


def test_gather_vault_metadata_tree_topic_scope(template_vault: Path) -> None:
    _seed_metadata_tree(template_vault)
    store = LocalFSStore(template_vault)
    payload = gather_vault_metadata_tree(store, template_vault, topic="agentic-systems")

    assert payload["topic"] == "agentic-systems"
    top_paths = {node["path"] for node in payload["children"]}
    assert "SCHEMA.md" in top_paths
    assert ".knotica" in top_paths
    assert "agentic-systems" in top_paths
    assert all(not path.startswith("sources/") for path in top_paths)


def test_gather_vault_metadata_tree_includes_file_stats(template_vault: Path) -> None:
    target = template_vault / ".knotica" / "prompts" / "query.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello metadata\n", encoding="utf-8")

    store = LocalFSStore(template_vault)
    payload = gather_vault_metadata_tree(store, template_vault)
    paths = _paths(payload["children"])
    assert ".knotica/prompts/query.md" in paths

    def find_file(nodes: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
        for node in nodes:
            if node["path"] == path:
                return node
            child = find_file(node.get("children") or [], path)
            if child:
                return child
        return None

    file_node = find_file(payload["children"], ".knotica/prompts/query.md")
    assert file_node is not None
    assert file_node["kind"] == "file"
    assert file_node["size"] == target.stat().st_size
    assert file_node["mtime"]


def test_vault_metadata_tree_tool_registered() -> None:
    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "vault_metadata_tree" in names


def test_vault_metadata_tree_mcp_payload(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_metadata_tree(template_vault)
    payload = payload_of(call_tool("vault_metadata_tree", {}))
    assert "error" not in payload
    assert payload["schema_version"] == 1
    paths = _paths(payload["children"])
    assert ".knotica/prompts/query.md" in paths
    assert "agentic-systems/.knotica/loop-state.json" in paths


def test_vault_metadata_tree_topic_filter(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_metadata_tree(template_vault)
    payload = payload_of(call_tool("vault_metadata_tree", {"topic": "agentic-systems"}))
    assert payload["topic"] == "agentic-systems"
    assert any(node["path"] == "agentic-systems" for node in payload["children"])


def test_vault_metadata_tree_missing_topic(vault_config: Path) -> None:
    del vault_config
    result = call_tool("vault_metadata_tree", {"topic": "no-such-topic"})
    assert result.isError
    payload = payload_of(result)
    assert "error" in payload
