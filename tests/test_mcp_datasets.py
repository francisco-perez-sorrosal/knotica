"""MCP datasets_inventory / records / freeze tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import pytest

from knotica.core.golden_review import save_golden_review
from support.trainset import populate_query_trainset
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


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    assert "error" not in body, body
    assert getattr(result, "isError", False) is False
    return body


def test_datasets_tools_registered() -> None:
    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "datasets_inventory" in names
    assert "datasets_records" in names
    assert "datasets_bootstrap" in names
    assert "datasets_bootstrap_train" in names
    assert "datasets_freeze" in names


def test_datasets_inventory_and_records(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)

    inv = assert_success(call_tool("datasets_inventory", {"topic": TOPIC}))
    assert inv["topic"] == TOPIC
    assert len(inv["files"]) == 5
    assert inv["files"][0]["label"] == "Trainset"

    records = assert_success(
        call_tool("datasets_records", {"topic": TOPIC, "role": "trainset", "limit": 3})
    )
    assert records["role"] == "trainset"
    assert len(records["records"]) == 3


def test_datasets_freeze_mcp(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC, golden_if_missing=False)

    accepted = [
        {
            "question": f"Freeze MCP concept {i}?",
            "reference_answer": f"Answer {i}",
            "citations": [],
            "pages_used": [],
        }
        for i in range(20)
    ]
    save_golden_review(store, template_vault, TOPIC, accepted)

    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        body = assert_success(call_tool("datasets_freeze", {"topic": TOPIC}))
    assert body["n_frozen"] == 20
    assert body["manifest"]["split"] == "held_out"


def test_datasets_bootstrap_train_registered_and_fails_clean_without_credentials(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    payload = payload_of(call_tool("datasets_bootstrap_train", {"topic": TOPIC}))

    assert "error" in payload, "credential-less bootstrap must return a typed error envelope"
    message = json.dumps(payload["error"]).lower()
    assert "anthropic_api_key" in message or "oauth" in message or "credential" in message
