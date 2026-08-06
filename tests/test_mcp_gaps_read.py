"""Behavioral contract tests for ``gaps_read`` -- the P1 gap queue, read back.

Split from ``test_mcp_suggestions.py`` by cohesion: that file fronts
``suggestions.jsonl`` (the queue *after* discovery has found candidate
sources), this one fronts ``gaps.jsonl`` (the queue before it). They share a
registrar, not a subject, and the shared file crossed the 800-line ceiling.

What this tool exists to fix: ``gap_report`` wrote gaps that nothing could read
back. No MCP tool exposed ``gaps.jsonl`` and no dashboard pane touched it, so a
filed gap was observable only by opening the file by hand and stayed invisible
until a discovery drain promoted it into a suggestion.

Drives the FastMCP server through the official in-memory transport so
assertions pin the *wire* contract, matching ``test_mcp_status.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.transaction import VaultTransaction

TOPIC = "agentic-systems"

ERROR_CODES = frozenset(
    {
        "NOT_CONFIGURED",
        "TOPIC_NOT_FOUND",
        "INVALID_CURSOR",
        "INVALID_ARGUMENT",
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
# Gap-record seed builder (direct construction -- the classify-and-file path is
# covered by tests/test_gap_classifier.py; this file needs only a well-formed
# record to read back)
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
