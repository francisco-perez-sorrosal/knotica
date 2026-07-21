"""Behavioral tests for the MCP ``wiki_status`` / ``metrics_read`` tools.

Drives the FastMCP server through the official in-memory transport so
assertions pin the *wire* contract (success payloads + ``NOT_CONFIGURED``),
matching ``test_mcp_read.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from knotica.core.records import MetricsComponents, MetricsRecord
from support.vault import run_git

TOPIC = "agentic-systems"

ERROR_CODES = frozenset(
    {
        "NOT_CONFIGURED",
        "TOPIC_NOT_FOUND",
        "PAGE_NOT_FOUND",
        "RESERVED_NAME",
        "SOURCE_EXISTS",
        "INVALID_FRONTMATTER",
        "SECRET_SCRUBBED",
        "LOCK_BUSY",
        "GIT_ERROR",
        "INVALID_CURSOR",
        "INVALID_ARGUMENT",
    }
)


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


def call_tool(tool: str, args: dict[str, Any], *, server: Any | None = None) -> Any:
    srv = server if server is not None else _build_server()
    return anyio.run(_call, srv, tool, args)


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def error_of(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict) and "error" in body
    assert getattr(result, "isError", False) is True
    return body["error"]


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    if isinstance(body, dict):
        assert "error" not in body, f"expected success, got error: {body!r}"
    assert getattr(result, "isError", False) is False
    return body


def assert_error_shape(err: dict[str, Any], code: str | None = None) -> None:
    assert set(err) >= {"code", "message", "fix", "retryable"}
    assert err["code"] in ERROR_CODES
    assert isinstance(err["retryable"], bool)
    assert err["retryable"] is False
    if code is not None:
        assert err["code"] == code


def _seed_metrics(vault: Path, *, generations: int = 3, base_scalar: float = 0.57) -> None:
    path = vault / TOPIC / ".knotica" / "metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for gen in range(generations):
        record = MetricsRecord(
            topic=TOPIC,
            timestamp=f"2026-07-17T12:00:{gen:02d}Z",
            generation=gen,
            harness_version="test",
            scalar=base_scalar + gen * 0.01,
            components=MetricsComponents(
                qa_accuracy=0.8,
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.05,
            ),
            n_examples=20,
            corpus_ref="git:" + "b" * 40,
            artifact_ref=f"{TOPIC}/.knotica/eval-runs/gen-{gen}/manifest.json",
        )
        lines.append(record.to_json_line())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Registration + unconfigured contract
# ---------------------------------------------------------------------------


def test_status_tools_are_registered() -> None:
    server = _build_server()

    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "wiki_status" in names
    assert "metrics_read" in names


@pytest.mark.parametrize("tool,args", [("wiki_status", {}), ("metrics_read", {"topic": TOPIC})])
def test_status_tools_return_not_configured_when_unconfigured(
    unconfigured_env: Path, tool: str, args: dict[str, Any]
) -> None:
    del unconfigured_env
    err = error_of(call_tool(tool, args))
    assert_error_shape(err, code="NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# wiki_status
# ---------------------------------------------------------------------------


def test_wiki_status_reports_template_topic_counts(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("wiki_status", {}))
    assert body["schema_version"] == 1
    assert body["compile_ready_threshold"] == 30
    assert body["eval_min_golden"] == 20
    assert body["vault_name"]
    assert body["vault_path"]
    assert body["vault_path"] == body["vault"]
    assert isinstance(body["available_vaults"], list)
    assert body["available_vaults"]
    assert body["available_vaults"][0]["name"] == body["vault_name"]
    assert body["available_vaults"][0]["ready"] is True
    assert "gate" in body and body["gate"]["state"] == "unknown"
    assert body["gate"]["baseline"] is None
    assert body["loop"]["stage"] is None
    assert body["loop"].get("last_decision") is None
    assert body["loop"].get("candidate_branch") is None
    topics = {t["topic"]: t for t in body["topics"]}
    assert TOPIC in topics
    row = topics[TOPIC]
    assert row["pages"] >= 1
    assert row["curated"] == 0
    assert row["to_compile_ready"] == 30
    assert row["compile_ready"] is False
    assert row["compiled"] is None
    assert "compile" in body
    assert isinstance(row["lint_violations"], int)
    assert row["last_eval"] is None
    assert body["gate"]["last_scalar"] is None


def test_wiki_status_surfaces_last_eval_and_gate_scalar(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_metrics(template_vault, generations=2, base_scalar=0.5707)
    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    assert len(body["topics"]) == 1
    last = body["topics"][0]["last_eval"]
    assert last is not None
    assert last["generation"] == 1
    assert last["scalar"] == pytest.approx(0.5807)
    assert body["gate"]["state"] == "unknown"
    assert body["gate"]["baseline"] is None
    assert body["gate"]["last_scalar"] == pytest.approx(0.5807)


def test_wiki_status_missing_topic_is_topic_not_found(vault_config: Path) -> None:
    del vault_config
    err = error_of(call_tool("wiki_status", {"topic": "no-such-topic"}))
    assert_error_shape(err, code="TOPIC_NOT_FOUND")


def test_wiki_status_is_read_only(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    before = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert_success(call_tool("wiki_status", {}))
    assert_success(call_tool("metrics_read", {"topic": TOPIC}))
    after = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert before == after


# ---------------------------------------------------------------------------
# metrics_read
# ---------------------------------------------------------------------------


def test_metrics_read_empty_when_no_history(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("metrics_read", {"topic": TOPIC}))
    assert body["topic"] == TOPIC
    assert body["records"] == []
    assert body["has_more"] is False
    assert body["skipped_malformed"] == 0


def test_metrics_read_windows_ascending(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_metrics(template_vault, generations=5)
    body = assert_success(call_tool("metrics_read", {"topic": TOPIC, "limit": 2}))
    gens = [r["generation"] for r in body["records"]]
    assert gens == [3, 4]
    assert body["has_more"] is True
    assert body["next_before_generation"] == 3

    older = assert_success(
        call_tool(
            "metrics_read",
            {"topic": TOPIC, "limit": 10, "before_generation": 3},
        )
    )
    assert [r["generation"] for r in older["records"]] == [0, 1, 2]
    assert older["has_more"] is False


def test_metrics_read_rejects_bad_limit(vault_config: Path) -> None:
    del vault_config
    err = error_of(call_tool("metrics_read", {"topic": TOPIC, "limit": 0}))
    assert_error_shape(err, code="INVALID_ARGUMENT")


def test_metrics_read_rejects_negative_before_generation(vault_config: Path) -> None:
    del vault_config
    err = error_of(call_tool("metrics_read", {"topic": TOPIC, "before_generation": -1}))
    assert_error_shape(err, code="INVALID_ARGUMENT")


def test_metrics_read_rejects_empty_topic(vault_config: Path) -> None:
    del vault_config
    err = error_of(call_tool("metrics_read", {"topic": ""}))
    assert_error_shape(err, code="TOPIC_NOT_FOUND")


def test_llm_availability_distinguishes_missing_deps_from_missing_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from knotica.core import status as status_module

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert status_module._llm_availability() == {
        "available": False,
        "mode": None,
        "reason": "credentials",
    }

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    status_module._anthropic_installed.cache_clear()
    monkeypatch.setattr(status_module, "_anthropic_installed", lambda: False)
    assert status_module._llm_availability() == {
        "available": False,
        "mode": "api_key",
        "reason": "deps",
    }

    monkeypatch.setattr(status_module, "_anthropic_installed", lambda: True)
    assert status_module._llm_availability() == {
        "available": True,
        "mode": "api_key",
        "reason": None,
    }
