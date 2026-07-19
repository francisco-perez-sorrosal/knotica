"""Behavioral contract tests for the MCP suggestion-queue tools.

Derived from ``INTERFACE_DESIGN.md`` §D1/D3/D4/D5 -- never from the
implementation. Two tools front the committed
``suggestions.jsonl`` queue: ``suggestions_read`` (pure, no ``discovery``
import) and ``suggestions_review`` (the ``dry-run|apply`` two-phase mutating
tool, ``action in {approve, reject, defer, mark_ingested}``). Drives the
FastMCP server through the official in-memory transport so assertions pin the
*wire* contract, matching ``test_mcp_status.py``.

RED-first: ``knotica.mcp_server.tools_suggestions`` does not exist yet when
this file is written (paired implementer step lands concurrently) -- every
production symbol is resolved lazily inside a helper or the test body so
collection succeeds and the first run fails with an import/registration
error, not a collection error. This file was written without reading the
implementer's code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from knotica.core.transaction import VaultTransaction
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
        "LLM_API_ERROR",
        "SEARCH_API_ERROR",
        "SUGGESTION_NOT_FOUND",
    }
)


# ---------------------------------------------------------------------------
# MCP call harness (mirrors test_mcp_status.py -- each tool test file
# duplicates this small seam per the project's established convention)
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
    if code is not None:
        assert err["code"] == code


# ---------------------------------------------------------------------------
# Suggestion-record seed builder (direct construction -- the join logic from
# gap -> candidate -> record is already covered by tests/test_gapfill.py; this
# file only needs a record shaped per INTERFACE_DESIGN.md §D2 to seed the
# queue the tools read/mutate)
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
    """Commit suggestion records directly -- test-only seeding, bypassing the
    drain so the read/decide tools are under test in isolation from
    ``refresh_suggestions_for_gaps``."""
    from knotica.core.gapfill import suggestions_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(vault)
    path = suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)


# ---------------------------------------------------------------------------
# Registration + unconfigured contract
# ---------------------------------------------------------------------------


def test_suggestion_tools_are_registered() -> None:
    server = _build_server()

    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "suggestions_read" in names
    assert "suggestions_review" in names
    assert "gap_report" in names


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("suggestions_read", {"topic": TOPIC}),
        (
            "suggestions_review",
            {"topic": TOPIC, "suggestion_id": "abc", "action": "approve"},
        ),
        ("gap_report", {"topic": TOPIC, "question": "Why does X outperform Y?"}),
    ],
)
def test_suggestion_tools_return_not_configured_when_unconfigured(
    unconfigured_env: Path, tool: str, args: dict[str, Any]
) -> None:
    del unconfigured_env
    err = error_of(call_tool(tool, args))
    assert_error_shape(err, code="NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# suggestions_read -- envelope shape, status filter, pagination
# ---------------------------------------------------------------------------


def test_suggestions_read_empty_queue_is_an_honest_zero_state(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC}))
    assert body["suggestions"] == []
    assert body["total_count"] == 0
    assert body["status_counts"] == {
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "deferred": 0,
        "ingested": 0,
    }
    assert body["has_more"] is False
    assert body["skipped_malformed"] == 0


def test_suggestions_read_filters_by_status(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_suggestions(
        template_vault,
        [
            _suggestion_record(suggestion_id="pend-1", status="pending"),
            _suggestion_record(suggestion_id="appr-1", status="approved"),
            _suggestion_record(suggestion_id="rej-1", status="rejected"),
        ],
    )
    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC, "status": "pending"}))
    assert [s["suggestion_id"] for s in body["suggestions"]] == ["pend-1"]
    assert body["total_count"] == 1
    assert body["status_counts"] == {
        "pending": 1,
        "approved": 1,
        "rejected": 1,
        "deferred": 0,
        "ingested": 0,
    }, "status_counts is always the full breakdown regardless of the active filter"


def test_suggestions_read_paginates_via_cursor(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    records = [
        _suggestion_record(suggestion_id=f"pend-{i}", status="pending", rank=i) for i in range(5)
    ]
    _seed_suggestions(template_vault, records)

    first = assert_success(
        call_tool("suggestions_read", {"topic": TOPIC, "status": "pending", "limit": 2})
    )
    assert len(first["suggestions"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"]

    second = assert_success(
        call_tool(
            "suggestions_read",
            {
                "topic": TOPIC,
                "status": "pending",
                "limit": 2,
                "cursor": first["next_cursor"],
            },
        )
    )
    assert len(second["suggestions"]) == 2
    first_ids = {s["suggestion_id"] for s in first["suggestions"]}
    second_ids = {s["suggestion_id"] for s in second["suggestions"]}
    assert first_ids.isdisjoint(second_ids), "a cursor page must never repeat a prior page's rows"


def test_suggestions_read_orders_by_proposed_at_not_detected_generation(
    vault_config: Path, template_vault: Path
) -> None:
    """F1 regression guard: a ``reported``/``retracted`` suggestion carries no
    eval generation (a constant zero), so ordering must key on ``proposed_at``
    -- a real timestamp every suggestion carries -- or the deliberate channel
    is always paged last regardless of how recently it was proposed."""
    del vault_config
    _seed_suggestions(
        template_vault,
        [
            _suggestion_record(
                suggestion_id="stale-measured",
                proposed_at="2026-07-01T00:00:00Z",
                detected_generation=42,
            ),
            _suggestion_record(
                suggestion_id="fresh-reported",
                proposed_at="2026-07-19T09:00:00Z",
                detected_generation=0,
            ),
        ],
    )

    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC, "status": "pending"}))

    assert [s["suggestion_id"] for s in body["suggestions"]] == [
        "fresh-reported",
        "stale-measured",
    ], "the most recently proposed suggestion must sort first, regardless of generation"


def test_suggestions_read_is_visible_across_a_fresh_process_read(
    vault_config: Path, template_vault: Path
) -> None:
    """The writer and the stateless MCP reader are separate processes
    sharing state only through committed git -- a fresh tool call (own store
    instance) must see a suggestion written moments before."""
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="cross-proc")])

    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC, "status": "pending"}))

    assert [s["suggestion_id"] for s in body["suggestions"]] == ["cross-proc"]


# ---------------------------------------------------------------------------
# suggestions_review -- dry-run previews without mutating; apply commits once
# ---------------------------------------------------------------------------


def test_dry_run_approve_previews_without_writing(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    from knotica.core.gapfill import suggestions_path

    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="dry-approve")])
    before_bytes = (
        template_vault / TOPIC / ".knotica" / "suggestions" / "suggestions.jsonl"
    ).read_bytes()
    before_sha = run_git(template_vault, "rev-parse", "HEAD").strip()

    body = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "dry-approve",
                "action": "approve",
                "mode": "dry-run",
            },
        )
    )

    assert body["mode"] == "dry-run"
    assert body["from_status"] == "pending"
    assert body["to_status"] == "approved"
    after_bytes = (template_vault / suggestions_path(TOPIC)).read_bytes()
    after_sha = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert after_bytes == before_bytes, "a dry-run preview must mutate NOTHING on disk"
    assert after_sha == before_sha, "a dry-run preview must never create a commit"


def test_apply_approve_flips_status_in_exactly_one_commit(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    before_sha = run_git(template_vault, "rev-parse", "HEAD").strip()
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="apply-approve")])
    seeded_sha = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert seeded_sha != before_sha

    body = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "apply-approve",
                "action": "approve",
                "mode": "apply",
            },
        )
    )

    assert body["mode"] == "apply"
    assert body["committed"] is True
    assert body["to_status"] == "approved"
    after_sha = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert after_sha != seeded_sha, "apply must create a new commit"

    read_back = assert_success(
        call_tool("suggestions_read", {"topic": TOPIC, "status": "approved"})
    )
    assert read_back["suggestions"][0]["suggestion_id"] == "apply-approve"
    assert read_back["suggestions"][0]["decided_at"] is not None


# ---------------------------------------------------------------------------
# suggestions_review -- reject requires a non-empty reason
# ---------------------------------------------------------------------------


def test_reject_without_a_reason_is_a_typed_error_and_writes_nothing(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="reject-empty")])

    err = error_of(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "reject-empty",
                "action": "reject",
                "mode": "apply",
                "reason": "",
            },
        )
    )

    assert_error_shape(err)
    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC, "status": "pending"}))
    assert [s["suggestion_id"] for s in body["suggestions"]] == ["reject-empty"], (
        "a refused reject must never mutate the record -- never a silent discard"
    )


def test_reject_with_a_non_empty_reason_persists_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="reject-reasoned")])

    body = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "reject-reasoned",
                "action": "reject",
                "mode": "apply",
                "reason": "reputability too low for this topic",
            },
        )
    )
    assert body["to_status"] == "rejected"

    # status="all" hides terminal rejected/ingested rows by design (D4) -- read
    # the terminal state back through its own status filter.
    read_back = assert_success(
        call_tool("suggestions_read", {"topic": TOPIC, "status": "rejected"})
    )
    rejected = next(s for s in read_back["suggestions"] if s["suggestion_id"] == "reject-reasoned")
    assert rejected["decided_reason"] == "reputability too low for this topic"


# ---------------------------------------------------------------------------
# suggestions_review -- error contract (D3)
# ---------------------------------------------------------------------------


def test_unknown_suggestion_id_is_suggestion_not_found(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="known-one")])

    err = error_of(
        call_tool(
            "suggestions_review",
            {"topic": TOPIC, "suggestion_id": "no-such-id", "action": "approve"},
        )
    )

    assert_error_shape(err, code="SUGGESTION_NOT_FOUND")


def test_bad_mode_is_a_typed_error(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="bad-mode")])

    err = error_of(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "bad-mode",
                "action": "approve",
                "mode": "yolo",
            },
        )
    )

    assert_error_shape(err)


def test_bad_action_is_a_typed_error(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="bad-action")])

    err = error_of(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "bad-action",
                "action": "obliterate",
                "mode": "apply",
            },
        )
    )

    assert_error_shape(err)


# ---------------------------------------------------------------------------
# gap_report -- NL-reported gaps from Claude Desktop (piece B, dec-025)
# ---------------------------------------------------------------------------


def _gaps_jsonl_bytes(vault: Path) -> bytes:
    return (vault / TOPIC / ".knotica" / "gaps" / "gaps.jsonl").read_bytes()


def test_gap_report_happy_path_writes_an_open_reported_gap(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    before_sha = run_git(template_vault, "rev-parse", "HEAD").strip()

    body = assert_success(
        call_tool(
            "gap_report",
            {"topic": TOPIC, "question": "Why does ReAct outperform Reflexion here?"},
        )
    )

    after_sha = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert after_sha != before_sha, "a genuine new report must land its own commit"
    gaps = _gaps_jsonl_bytes(template_vault).decode("utf-8").strip().splitlines()
    assert len(gaps) == 1
    persisted = json.loads(gaps[0])
    assert persisted["origin"] == "reported"
    assert persisted["status"] == "open"
    assert persisted["fault_class"] == "genuine_gap"
    assert persisted["question"] == "Why does ReAct outperform Reflexion here?"
    # The tool must not fabricate provenance: the envelope surfaces the actual
    # persisted identity, not a synthesized/unrelated one.
    assert body["qa_id"] == persisted["qa_id"]


@pytest.mark.parametrize("question", ["", "   "])
def test_gap_report_rejects_a_missing_or_blank_question(
    vault_config: Path, template_vault: Path, question: str
) -> None:
    del vault_config, template_vault

    err = error_of(call_tool("gap_report", {"topic": TOPIC, "question": question}))

    assert_error_shape(err)


def test_gap_report_rejects_a_blank_topic(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault

    err = error_of(call_tool("gap_report", {"topic": "", "question": "Any question?"}))

    assert_error_shape(err, code="TOPIC_NOT_FOUND")


def test_repeated_identical_gap_report_surfaces_the_same_id_not_a_fabricated_second_one(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    question = "What is the memory footprint of long-context Transformers?"

    first = assert_success(call_tool("gap_report", {"topic": TOPIC, "question": question}))
    second = assert_success(call_tool("gap_report", {"topic": TOPIC, "question": question}))

    assert second["qa_id"] == first["qa_id"], (
        "a repeated identical report must surface the existing gap's real id -- never a "
        "fabricated new one"
    )
    gaps = _gaps_jsonl_bytes(template_vault).decode("utf-8").strip().splitlines()
    assert len(gaps) == 1, (
        "the dedup must be honestly reflected on disk -- a second report of the same "
        "question must not spam the queue"
    )


def test_gap_report_write_is_visible_to_an_independently_built_server_instance(
    vault_config: Path, template_vault: Path
) -> None:
    """The writer and a subsequent reader are two independently constructed
    FastMCP server instances -- state is carried only by the committed vault
    on disk, never by in-process server state (dec-001 stateless-server)."""
    del vault_config
    writer_server = _build_server()
    call_tool(
        "gap_report",
        {"topic": TOPIC, "question": "Does prompt caching bias eval cost?"},
        server=writer_server,
    )

    reader_server = _build_server()
    assert reader_server is not writer_server
    gaps = _gaps_jsonl_bytes(template_vault).decode("utf-8").strip().splitlines()
    assert len(gaps) == 1, (
        "the report must be durable on disk regardless of which server instance reads it"
    )
