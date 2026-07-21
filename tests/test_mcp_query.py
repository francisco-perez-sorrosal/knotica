"""MCP ``query`` tool — unified wiki-answer API (no wiki_query twin)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio

from knotica.core.query_engine import QueryResult
from knotica.evals.llm import Completion, FakeLLMClient, TokenUsage
from knotica.evals.runner import MessagesApiRunner
from knotica.evals.config import WORKER_SNAPSHOT


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


def test_query_tool_registered() -> None:
    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "query" in names
    assert "wiki_query" not in names


def test_query_tool_answers_via_facade(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    fake = FakeLLMClient(
        [
            Completion(
                text=json.dumps(
                    {
                        "answer": "AWM induces workflows from trajectories.",
                        "citations": ["wang2024awm"],
                    }
                ),
                usage=TokenUsage(input_tokens=5, output_tokens=8),
            )
        ]
    )
    runner = MessagesApiRunner(llm_client=fake, worker_snapshot=WORKER_SNAPSHOT)

    def _fake_answer(store, topic, question, **_kwargs):
        return QueryResult(
            answer="AWM induces workflows from trajectories.",
            citations=["wang2024awm"],
            pages_used=["agentic-systems/agent-workflow-memory.md"],
            topic=topic,
            question=question,
        )

    with patch("knotica.mcp_server.tools_query.answer_question", side_effect=_fake_answer):
        payload = payload_of(
            call_tool(
                "query",
                {
                    "topic": "agentic-systems",
                    "question": "What is AWM?",
                },
            )
        )
    assert "error" not in payload
    assert payload["answer"].startswith("AWM")
    assert payload["citations"] == ["wang2024awm"]
    assert "engine" not in payload
    del runner  # silence unused when patch active


def test_query_tool_rejects_an_empty_question_as_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    """An empty question is an argument problem, not a stale cursor."""
    del vault_config, template_vault
    result = call_tool("query", {"topic": "agentic-systems", "question": "   "})
    assert result.isError
    payload = payload_of(result)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
