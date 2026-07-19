"""Behavioral contract tests for the ``wiki_status`` suggestions/gapfill block.

Derived from ``INTERFACE_DESIGN.md`` §D6.1 -- never from the implementation. ``wiki_status`` gains a per-topic ``suggestions``
count block (``pending``, ``approved_awaiting_ingest``, ``deferred``,
``rejected``, ``ingested``, ``newest_proposed_at``) so a dashboard or an
interactive client discovers a pending-approval / approved-backlog queue
without reading ``suggestions.jsonl`` directly. Additive to the existing
``wiki_status`` contract -- no existing field changes.

RED-first: the ``suggestions`` block does not exist on ``gather_wiki_status``'s
payload yet when this file is written (paired implementer step lands
concurrently) -- production symbols are resolved lazily inside test bodies so
collection succeeds and the first run fails on a missing key, not a collection
error. Written without reading the implementer's code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.transaction import VaultTransaction

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# MCP call harness (mirrors test_mcp_status.py)
# ---------------------------------------------------------------------------


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


def _topic_row(body: dict[str, Any]) -> dict[str, Any]:
    """Locate the seeded topic's row in wiki_status's ``topics`` list."""
    rows = {row["topic"]: row for row in body["topics"]}
    assert TOPIC in rows, f"expected a row for {TOPIC!r}, got {sorted(rows)}"
    return rows[TOPIC]


def _suggestions_block(body: dict[str, Any]) -> dict[str, Any]:
    """Locate the suggestions/gapfill count block -- probes the per-topic row
    first (mirrors the existing ``last_eval`` per-topic field), falling back to
    a top-level key for a whole-vault call. Diagnostic on total absence."""
    row = _topic_row(body)
    if "suggestions" in row:
        return row["suggestions"]
    if "gapfill" in row:
        return row["gapfill"]
    if "suggestions" in body:
        return body["suggestions"]
    if "gapfill" in body:
        return body["gapfill"]
    raise AssertionError(
        f"wiki_status payload carries no 'suggestions'/'gapfill' block on the topic row "
        f"or top level; row keys={sorted(row)}, body keys={sorted(body)}"
    )


# ---------------------------------------------------------------------------
# Seed helper (direct construction, mirrors test_mcp_suggestions.py)
# ---------------------------------------------------------------------------


def _suggestion_record(*, suggestion_id: str, status: str = "pending", **overrides: object):
    from knotica.core.records import SuggestionRecord

    payload: dict[str, object] = {
        "suggestion_id": suggestion_id,
        "topic": TOPIC,
        "gap_id": f"gap-{suggestion_id}",
        "qa_id": f"golden-{suggestion_id}",
        "fault_class": "genuine_gap",
        "question": "How does speculative decoding interact with draft-model verification?",
        "reference_pages": ("speculative-decoding",),
        "rank": 1,
        "query_text": "speculative decoding draft model verification",
        "candidate": {
            "url": f"https://arxiv.org/abs/{suggestion_id}",
            "title": "Accelerating LLM Inference with Speculative Decoding",
            "snippet": "We propose...",
            "source_provider": "fake",
            "doi": None,
            "citation_count": 412,
            "schema_version": 1,
        },
        "status": status,
        "proposed_at": "2026-07-19T07:30:00Z",
        "decided_at": None,
        "decided_reason": None,
        "ingested_at": None,
        "detected_generation": 42,
    }
    payload.update(overrides)
    return SuggestionRecord(**payload)


def _seed_suggestions(vault: Path, records) -> None:
    from knotica.core.gapfill import suggestions_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(vault)
    path = suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)


# ---------------------------------------------------------------------------
# Counts against a seeded queue
# ---------------------------------------------------------------------------


def test_wiki_status_suggestions_counts_match_a_seeded_queue_exactly(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(
        template_vault,
        [
            _suggestion_record(suggestion_id="s-pending-1", status="pending"),
            _suggestion_record(suggestion_id="s-pending-2", status="pending"),
            _suggestion_record(
                suggestion_id="s-approved-1",
                status="approved",
                decided_at="2026-07-19T08:00:00Z",
            ),
            _suggestion_record(
                suggestion_id="s-deferred-1",
                status="deferred",
                decided_at="2026-07-19T08:05:00Z",
            ),
            _suggestion_record(
                suggestion_id="s-rejected-1",
                status="rejected",
                decided_at="2026-07-19T08:10:00Z",
                decided_reason="not relevant",
            ),
            _suggestion_record(
                suggestion_id="s-ingested-1",
                status="ingested",
                decided_at="2026-07-19T08:15:00Z",
                ingested_at="2026-07-19T09:00:00Z",
            ),
        ],
    )

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    block = _suggestions_block(body)

    assert block["pending"] == 2
    assert block["approved_awaiting_ingest"] == 1, (
        "approved_awaiting_ingest counts approved-but-not-yet-ingested rows only"
    )
    assert block["deferred"] == 1
    assert block["rejected"] == 1
    assert block["ingested"] == 1
    assert block["newest_proposed_at"] == "2026-07-19T07:30:00Z"


# ---------------------------------------------------------------------------
# Absent queue -> honest zero/absent state, no exception
# ---------------------------------------------------------------------------


def test_wiki_status_suggestions_is_all_zero_when_the_queue_is_absent(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault  # no suggestions.jsonl ever written

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    block = _suggestions_block(body)

    assert block["pending"] == 0
    assert block["approved_awaiting_ingest"] == 0
    assert block["deferred"] == 0
    assert block["rejected"] == 0
    assert block["ingested"] == 0
    assert block["newest_proposed_at"] is None, (
        "an absent queue must report None, never a fabricated timestamp"
    )


# ---------------------------------------------------------------------------
# Additive -- existing wiki_status fields are untouched
# ---------------------------------------------------------------------------


def test_wiki_status_suggestions_block_is_additive_to_the_existing_contract(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="s-additive")])

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))

    assert body["schema_version"] == 1
    assert "gate" in body and "loop" in body
    row = _topic_row(body)
    assert "pages" in row and "curated" in row and "last_eval" in row, (
        "existing per-topic fields must survive the new suggestions block unchanged"
    )
    _suggestions_block(body)  # must be present without disturbing the above
