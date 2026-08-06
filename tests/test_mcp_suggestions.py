"""Behavioral contract tests for the MCP suggestion-queue tools.

Derived from ``INTERFACE_DESIGN.md`` §D1/D3/D4/D5 -- never from the
implementation. Two tools front the committed
``suggestions.jsonl`` queue: ``suggestions_read`` (pure, no ``discovery``
import) and ``suggestions_review`` (the ``dry-run|apply`` two-phase mutating
tool, ``action in {approve, reject, defer, mark_ingested}``). Two more front
the ``gaps.jsonl`` queue upstream of it: ``gap_report`` (write) and
``gaps_read`` (read). Drives the
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
        "INVALID_ARGUMENT",
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
    assert "gaps_read" in names


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("suggestions_read", {"topic": TOPIC}),
        (
            "suggestions_review",
            {"topic": TOPIC, "suggestion_id": "abc", "action": "approve"},
        ),
        ("gap_report", {"topic": TOPIC, "question": "Why does X outperform Y?"}),
        ("gaps_read", {"topic": TOPIC}),
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
# suggestions_read -- gate_outcome surfacing (null pre-gate, full object post-gate)
# ---------------------------------------------------------------------------


def test_suggestions_read_reports_gate_outcome_as_null_for_a_record_not_yet_gated(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(
        template_vault, [_suggestion_record(suggestion_id="pre-gate", status="approved")]
    )

    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC, "status": "approved"}))

    record = next(s for s in body["suggestions"] if s["suggestion_id"] == "pre-gate")
    assert record["gate_outcome"] is None


def test_suggestions_read_surfaces_the_full_gate_outcome_after_a_gate_verdict_is_recorded(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    from knotica.core.gapfill import apply_gate_outcome
    from knotica.store import LocalFSStore

    _seed_suggestions(
        template_vault, [_suggestion_record(suggestion_id="gated-refused", status="approved")]
    )
    refused_outcome: dict[str, object] = {
        "verdict": "refused",
        "scalar": 0.9201,
        "baseline_scalar": 0.9655,
        "ref": "loop/x/agentic-systems/source-a1b2c3d4",
        "reason": "regressed 3 previously-passing golden questions",
        "regressed_questions": ["q-0001", "q-0007", "q-0012"],
    }
    store = LocalFSStore(template_vault)
    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "gated-refused",
        verdict="refused",
        gate_outcome=refused_outcome,
    )

    body = assert_success(call_tool("suggestions_read", {"topic": TOPIC, "status": "approved"}))

    record = next(s for s in body["suggestions"] if s["suggestion_id"] == "gated-refused")
    assert record["gate_outcome"] == refused_outcome, (
        "the full gate outcome object must round-trip through the wire dict, not a subset"
    )


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


def test_dry_run_preview_carries_the_decision_envelope_context_and_provenance(
    vault_config: Path, template_vault: Path
) -> None:
    """Additive decision-envelope enrichment (SYSTEMS_PLAN §Interfaces) -- the
    dry-run preview must still carry every pre-existing field plus the new
    context/provenance so the card is self-contained without a second call."""
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="dry-envelope")])

    body = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "dry-envelope",
                "action": "approve",
                "mode": "dry-run",
            },
        )
    )

    assert body["from_status"] == "pending" and body["to_status"] == "approved"
    assert body["decision_id"] == "dry-envelope"
    assert body["context"]["gap_question"] and body["context"]["why_wiki_fell_short"]
    assert set(body["provenance"]) >= {"source_url", "reputability", "origin", "citation_hint"}


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


def test_bad_mode_is_invalid_argument_not_invalid_cursor(
    vault_config: Path, template_vault: Path
) -> None:
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

    assert_error_shape(err, code="INVALID_ARGUMENT")
    assert "mode" in err["fix"].lower()


def test_bad_action_is_invalid_argument_not_invalid_cursor(
    vault_config: Path, template_vault: Path
) -> None:
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

    assert_error_shape(err, code="INVALID_ARGUMENT")
    assert "action" in err["fix"].lower()


def test_bad_status_filter_on_suggestions_read_is_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config

    err = error_of(call_tool("suggestions_read", {"topic": TOPIC, "status": "nonsense"}))

    assert_error_shape(err, code="INVALID_ARGUMENT")


def test_limit_out_of_bounds_on_suggestions_read_is_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config

    err = error_of(call_tool("suggestions_read", {"topic": TOPIC, "limit": 51}))

    assert_error_shape(err, code="INVALID_ARGUMENT")


def test_malformed_cursor_on_suggestions_read_still_returns_invalid_cursor(
    vault_config: Path, template_vault: Path
) -> None:
    """The argument-validation split narrows INVALID_CURSOR's meaning -- it must
    not vanish: a genuinely malformed pagination token on the very same tool is
    still a cursor problem, not an argument problem."""
    del vault_config

    err = error_of(
        call_tool("suggestions_read", {"topic": TOPIC, "cursor": "not-a-real-cursor-token"})
    )

    assert_error_shape(err, code="INVALID_CURSOR")


def test_gap_report_rejects_too_many_reference_pages_as_invalid_argument(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config

    err = error_of(
        call_tool(
            "gap_report",
            {
                "topic": TOPIC,
                "question": "Why does ReAct outperform Reflexion here?",
                "reference_pages": [f"page-{i}" for i in range(21)],
            },
        )
    )

    assert_error_shape(err, code="INVALID_ARGUMENT")


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


# ---------------------------------------------------------------------------
# gaps_read -- the P1 queue, readable before discovery promotes anything
#
# `gap_report` above proves a gap lands on disk. These prove it can be read
# back: before this tool, `gaps.jsonl` had a writer and no reader on the MCP
# surface at all, so a filed gap was invisible to every client and to the
# dashboard until a discovery drain turned it into a suggestion.
# ---------------------------------------------------------------------------


def _gap_record(
    *,
    gap_id: str,
    status: str = "open",
    origin: str = "measured",
    fault_class: str = "genuine_gap",
    detected_at: str = "2026-07-19T07:30:00Z",
    **overrides: object,
):
    """A gap shaped per the P1 record contract, seeded directly.

    Direct construction for the same reason `_suggestion_record` uses it: the
    classify-and-file path is covered by tests/test_gap_classifier.py, and this
    file needs only a well-formed record to read back.
    """
    from knotica.core.records import GapEvidence, GapRecord

    payload: dict[str, object] = {
        "gap_id": gap_id,
        "topic": TOPIC,
        "qa_id": f"golden-{gap_id}",
        "fault_class": fault_class,
        "status": status,
        "classifier_version": 1,
        "detected_generation": 42,
        "detected_at": detected_at,
        "scalar_at_detection": 0.5,
        "baseline_scalar": 0.6,
        "question": f"What does {gap_id} fail to answer?",
        "reference_pages": ("speculative-decoding",),
        "reference_pages_exist": True,
        "evidence": GapEvidence(
            quality_delta=-0.1,
            qa_accuracy_delta=-0.1,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=41,
        ),
        "manifest_ref": "",
        "origin": origin,
    }
    payload.update(overrides)
    return GapRecord(**payload)


def _seed_gaps(vault: Path, records) -> None:
    """Commit gap records directly, bypassing the classifier and gap_report."""
    from knotica.core.gap_classifier import gaps_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(vault)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed gaps for test") as txn:
        txn.write(gaps_path(TOPIC), body)


def test_gaps_read_empty_queue_is_an_honest_zero_state(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("gaps_read", {"topic": TOPIC}))

    assert body["gaps"] == []
    assert body["total_count"] == 0
    assert body["status_counts"] == {"open": 0, "resolved": 0, "dismissed": 0}
    assert body["origin_counts"] == {"measured": 0, "reported": 0, "retracted": 0}
    assert body["has_more"] is False
    assert body["skipped_malformed"] == 0


def test_gaps_read_defaults_to_open(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="a1", status="open"),
            _gap_record(gap_id="b2", status="resolved"),
            _gap_record(gap_id="c3", status="dismissed"),
        ],
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC}))

    assert [gap["gap_id"] for gap in body["gaps"]] == ["a1"]
    assert body["status_counts"] == {"open": 1, "resolved": 1, "dismissed": 1}


def test_gaps_read_all_filter_includes_terminal_gaps(
    vault_config: Path, template_vault: Path
) -> None:
    """`all` means all three statuses -- deliberately unlike suggestions_read.

    There, `all` is a *non-terminal* view that hides rejected/ingested. A gap's
    terminal statuses are resolved and dismissed, so carrying that convention
    over would leave `all` returning only open gaps: a synonym for the default,
    and a filter that answers a different question than the one asked.
    """
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="a1", status="open"),
            _gap_record(gap_id="b2", status="resolved"),
            _gap_record(gap_id="c3", status="dismissed"),
        ],
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC, "status": "all"}))

    assert body["total_count"] == 3
    assert sorted(gap["gap_id"] for gap in body["gaps"]) == ["a1", "b2", "c3"]


def test_gaps_read_shows_dilution_gaps_that_a_drain_would_skip(
    vault_config: Path, template_vault: Path
) -> None:
    """A reader answers "what is on this queue", not "what may a drain query for".

    `gapfill._open_genuine_gaps` drops `dilution` because discovery has nothing
    to search for on one. Reusing it here would have hidden a real, open gap
    from every surface -- the exact class of invisibility this tool exists to end.
    """
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="a1", fault_class="genuine_gap"),
            _gap_record(gap_id="b2", fault_class="dilution"),
        ],
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC}))

    assert sorted(gap["gap_id"] for gap in body["gaps"]) == ["a1", "b2"]


def test_gaps_read_orders_newest_first_so_a_reported_gap_is_not_buried(
    vault_config: Path, template_vault: Path
) -> None:
    """Ordering keys on detected_at, not detected_generation.

    A reported gap carries a constant-zero generation by construction (no eval
    backs it), so a generation sort sinks every hand-filed gap below every
    measured one -- burying precisely the gaps a human just filed and came
    looking for.
    """
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(
                gap_id="older-measured",
                origin="measured",
                detected_at="2026-07-01T00:00:00Z",
                detected_generation=99,
            ),
            _gap_record(
                gap_id="newer-reported",
                origin="reported",
                detected_at="2026-08-06T00:00:00Z",
                detected_generation=0,
            ),
        ],
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC}))

    assert [gap["gap_id"] for gap in body["gaps"]] == ["newer-reported", "older-measured"]


def test_gaps_read_counts_origins_so_a_surface_can_badge_them(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="a1", origin="measured"),
            _gap_record(gap_id="b2", origin="reported"),
            _gap_record(gap_id="c3", origin="reported"),
            _gap_record(gap_id="d4", origin="retracted"),
        ],
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC}))

    assert body["origin_counts"] == {"measured": 1, "reported": 2, "retracted": 1}


def test_gaps_read_skips_a_malformed_line_instead_of_hiding_the_queue(
    vault_config: Path, template_vault: Path
) -> None:
    """One corrupt line must cost one record, not the whole queue.

    `parse_gaps_jsonl` raises on the first bad line, which on a display surface
    turns a single bad record into a total blackout.
    """
    del vault_config
    from knotica.core.gap_classifier import gaps_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(template_vault)
    good = _gap_record(gap_id="a1").to_json_line()
    body_text = f"{good}\n{{not json at all\n"
    with VaultTransaction(
        store, template_vault, "test_seed", TOPIC, "seed a corrupt gap queue"
    ) as txn:
        txn.write(gaps_path(TOPIC), body_text)

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC}))

    assert [gap["gap_id"] for gap in body["gaps"]] == ["a1"]
    assert body["skipped_malformed"] == 1


def test_gaps_read_paginates_via_cursor(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id=f"g{index}", detected_at=f"2026-07-{index + 10:02d}T00:00:00Z")
            for index in range(3)
        ],
    )

    first = assert_success(call_tool("gaps_read", {"topic": TOPIC, "limit": 2}))
    assert len(first["gaps"]) == 2
    assert first["has_more"] is True
    assert first["total_count"] == 3

    second = assert_success(
        call_tool("gaps_read", {"topic": TOPIC, "limit": 2, "cursor": first["next_cursor"]})
    )
    assert len(second["gaps"]) == 1
    assert second["has_more"] is False

    paged = [gap["gap_id"] for gap in first["gaps"] + second["gaps"]]
    assert sorted(paged) == ["g0", "g1", "g2"], "pagination must not drop or repeat a gap"


def test_a_gaps_cursor_cannot_be_replayed_against_suggestions_read(
    vault_config: Path, template_vault: Path
) -> None:
    """The two queues mint cursors under different sort contracts.

    Both tools page with the same opaque token type, so without distinct sort
    ids a cursor from one queue would decode cleanly against the other and
    silently page into unrelated records.
    """
    del vault_config
    _seed_gaps(
        template_vault,
        [_gap_record(gap_id=f"g{index}") for index in range(3)],
    )
    gaps_cursor = assert_success(call_tool("gaps_read", {"topic": TOPIC, "limit": 2}))[
        "next_cursor"
    ]
    assert gaps_cursor

    err = error_of(call_tool("suggestions_read", {"topic": TOPIC, "cursor": gaps_cursor}))
    assert_error_shape(err, code="INVALID_CURSOR")


def test_bad_status_filter_on_gaps_read_is_invalid_argument(vault_config: Path) -> None:
    del vault_config
    err = error_of(call_tool("gaps_read", {"topic": TOPIC, "status": "pending"}))
    assert_error_shape(err, code="INVALID_ARGUMENT")
    assert "pending" in err["message"], (
        "the message must name the rejected value -- 'pending' is a *suggestion* "
        "status, and confusing the two queues is the likeliest caller mistake"
    )


def test_the_gaps_read_status_vocabulary_matches_the_record_contract() -> None:
    """The tool's fixed status order must cover the record vocabulary exactly.

    `_GAP_STATUS_VALUES` is hand-ordered (lifecycle, not alphabetical) so it
    cannot be derived from the frozenset; this pins the two together so a new
    status cannot be added to records and silently missing from status_counts.
    """
    from knotica.core.records import GAP_STATUSES
    from knotica.mcp_server.tools_suggestions import _GAP_STATUS_VALUES

    assert set(_GAP_STATUS_VALUES) == GAP_STATUSES
