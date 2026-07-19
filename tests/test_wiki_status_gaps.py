"""Behavioral contract tests for the ``wiki_status`` gaps sub-block.

Derived from the gap-discovery queue's provenance model -- never from the implementation.
``wiki_status`` gains a per-topic ``gaps`` count block distinguishing open
knowledge-gap records by ``origin`` (``measured``/``reported``/``retracted``)
so a dashboard or interactive client discovers the gap-discovery queue's
provenance mix without reading ``gaps.jsonl`` directly. Additive to the
existing ``wiki_status`` contract -- no existing field changes, and the block
must stay honestly all-zero when no ``gaps.jsonl`` exists.

RED-first: the ``gaps`` block may not exist on ``gather_wiki_status``'s payload
yet when this file is written (paired implementer step lands concurrently) --
production symbols are resolved lazily inside test bodies so collection
succeeds and the first run fails on a missing key, not a collection error.
Written without reading the implementer's code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.transaction import VaultTransaction

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# MCP call harness (mirrors test_wiki_status_suggestions.py)
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
    rows = {row["topic"]: row for row in body["topics"]}
    assert TOPIC in rows, f"expected a row for {TOPIC!r}, got {sorted(rows)}"
    return rows[TOPIC]


def _gaps_block(body: dict[str, Any]) -> dict[str, Any]:
    """Locate the gaps count block -- probes the per-topic row first (mirrors
    the existing ``suggestions`` field), falling back to a top-level key for a
    whole-vault call. Diagnostic on total absence."""
    row = _topic_row(body)
    if "gaps" in row:
        return row["gaps"]
    if "gaps" in body:
        return body["gaps"]
    raise AssertionError(
        f"wiki_status payload carries no 'gaps' block on the topic row or top "
        f"level; row keys={sorted(row)}, body keys={sorted(body)}"
    )


# ---------------------------------------------------------------------------
# Seed helper (direct construction, mirrors test_gapfill.py's _seed_gaps)
# ---------------------------------------------------------------------------


def _gap_evidence(**overrides: object):
    from knotica.core.records import GapEvidence

    payload: dict[str, object] = {
        "quality_delta": -0.5,
        "qa_accuracy_delta": -0.5,
        "citation_validity_delta": 0.0,
        "retrieval_trace": (),
        "pages_added": (),
        "pages_removed": (),
        "prior_generation": 4,
    }
    payload.update(overrides)
    return GapEvidence(**payload)


def _gap_record(*, gap_id: str, qa_id: str, origin: str, status: str = "open", **overrides: object):
    from knotica.core.records import GapRecord

    payload: dict[str, object] = {
        "gap_id": gap_id,
        "topic": TOPIC,
        "qa_id": qa_id,
        "fault_class": "genuine_gap",
        "status": status,
        "classifier_version": 1,
        "detected_generation": 5,
        "detected_at": "2026-07-19T08:00:00Z",
        "scalar_at_detection": 0.9493,
        "baseline_scalar": 0.96,
        "question": f"What is the gap behind {qa_id}?",
        "reference_pages": (),
        "reference_pages_exist": False,
        "evidence": _gap_evidence(),
        "manifest_ref": "agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
        "origin": origin,
    }
    payload.update(overrides)
    return GapRecord(**payload)


def _seed_gaps(vault: Path, records: list[Any]) -> None:
    from knotica.core.gap_classifier import gaps_path
    from knotica.store import LocalFSStore

    store = LocalFSStore(vault)
    path = gaps_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed gaps for test") as txn:
        txn.write(path, body)


# ---------------------------------------------------------------------------
# Counts by origin against a seeded gaps.jsonl
# ---------------------------------------------------------------------------


def test_wiki_status_gaps_counts_by_origin_match_a_seeded_measured_reported_retracted_mix(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="gap-m1", qa_id="golden-m1", origin="measured"),
            _gap_record(gap_id="gap-m2", qa_id="golden-m2", origin="measured"),
            _gap_record(gap_id="gap-r1", qa_id="golden-r1", origin="reported"),
            _gap_record(gap_id="gap-x1", qa_id="golden-x1", origin="retracted"),
        ],
    )

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    block = _gaps_block(body)

    assert block["measured"] == 2
    assert block["reported"] == 1
    assert block["retracted"] == 1


def test_wiki_status_gaps_counts_exclude_non_open_records(
    vault_config: Path, template_vault: Path
) -> None:
    """A ``resolved``/``dismissed`` gap is no longer an actionable queue entry
    -- the origin counts must reflect ``open`` records only, mirroring the
    drain's own open-genuine_gap filter."""
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="gap-open", qa_id="golden-open", origin="measured", status="open"),
            _gap_record(
                gap_id="gap-resolved",
                qa_id="golden-resolved",
                origin="measured",
                status="resolved",
            ),
        ],
    )

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    block = _gaps_block(body)

    assert block["measured"] == 1, "a resolved gap must not inflate the open-queue count"


# ---------------------------------------------------------------------------
# Absent queue -> honest zero state, no exception
# ---------------------------------------------------------------------------


def test_wiki_status_gaps_is_all_zero_when_the_queue_is_absent(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault  # no gaps.jsonl ever written

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    block = _gaps_block(body)

    assert block["measured"] == 0
    assert block["reported"] == 0
    assert block["retracted"] == 0


# ---------------------------------------------------------------------------
# Additive -- existing wiki_status fields are untouched
# ---------------------------------------------------------------------------


def test_wiki_status_gaps_block_is_additive_to_the_existing_contract(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_gaps(template_vault, [_gap_record(gap_id="gap-a", qa_id="golden-a", origin="measured")])

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))

    assert body["schema_version"] == 1
    row = _topic_row(body)
    assert "pages" in row and "curated" in row and "last_eval" in row and "suggestions" in row, (
        "existing per-topic fields (including the suggestions block) must survive the "
        "new gaps block unchanged"
    )
    _gaps_block(body)  # must be present without disturbing the above
