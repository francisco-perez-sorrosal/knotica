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
import pytest

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
    from knotica.mcp_server.tools_gaps import _GAP_STATUS_VALUES

    assert set(_GAP_STATUS_VALUES) == GAP_STATUSES


# ---------------------------------------------------------------------------
# gapfill_discover -- the billed hop from the gap queue to the suggestion queue
#
# Discovery was CLI-only, which left the P1 -> P3 hop unreachable from the two
# surfaces a gap is filed and read on. These pin the two-phase gate, because the
# failure mode of getting it wrong is spending the user's money without asking.
# ---------------------------------------------------------------------------


def _seed_one_open_gap(vault: Path) -> None:
    _seed_gaps(vault, [_gap_record(gap_id="drainable", fault_class="genuine_gap")])


@pytest.fixture
def no_live_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the drain's service to None so a confirmed phase 2 cannot bill.

    This is not belt-and-braces. `resolve_api_key` falls back to `./.env` after
    the process environment, and this repo's own `.env.example` invites a key
    there -- so on a maintainer's machine a search key *does* resolve under
    pytest, and any test passing a valid confirm would issue real, billed search
    calls on every run. Confirmed by measurement, not assumed.

    Every test that reaches phase 2 must take this fixture. Phase-1 tests do not
    need it: the preview constructs the service to report `provider_configured`
    but never calls `discover`, so it stays free either way.
    """
    monkeypatch.setattr(
        "knotica.mcp_server.tools_gaps.build_default_discovery_service",
        lambda *args, **kwargs: None,
    )


def test_a_bare_discover_call_previews_and_never_bills(
    vault_config: Path, template_vault: Path
) -> None:
    """Phase 1 mints a nonce and stages nothing.

    `refresh_suggestions_for_gaps` is the billing boundary; a preview that
    reached it would spend before the user ever saw a cost.
    """
    del vault_config
    _seed_one_open_gap(template_vault)

    body = assert_success(call_tool("gapfill_discover", {"topic": TOPIC}))

    assert body["action"] == "gapfill_discover"
    assert body["open_gaps"] == 1
    assert body["would_drain"] == 1
    assert body["confirm_nonce"]
    assert body["ttl"] > 0
    assert "suggestions_staged" not in body, "a preview must not report a drain it did not run"
    assert not (template_vault / TOPIC / ".knotica" / "suggestions").exists(), (
        "phase 1 must stage nothing"
    )


def test_max_gaps_caps_what_the_preview_quotes(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    _seed_gaps(
        template_vault,
        [_gap_record(gap_id=f"g{index}") for index in range(3)],
    )

    body = assert_success(call_tool("gapfill_discover", {"topic": TOPIC, "max_gaps": 1}))

    assert body["open_gaps"] == 3
    assert body["would_drain"] == 1, "the quote must reflect the cap, not the queue"


def test_the_preview_counts_only_gaps_a_drain_would_query_for(
    vault_config: Path, template_vault: Path
) -> None:
    """`dilution` gaps are visible in gaps_read but not drainable.

    Counting them here would quote a drain larger than the one that runs --
    an over-estimate on a billed action, which is the wrong direction to be wrong.
    """
    del vault_config
    _seed_gaps(
        template_vault,
        [
            _gap_record(gap_id="a1", fault_class="genuine_gap"),
            _gap_record(gap_id="b2", fault_class="dilution"),
        ],
    )

    body = assert_success(call_tool("gapfill_discover", {"topic": TOPIC}))

    assert body["open_gaps"] == 1


def test_a_wrong_confirm_falls_back_to_a_fresh_preview_rather_than_running(
    vault_config: Path, template_vault: Path
) -> None:
    """A bad nonce must not execute, and must not leak whether one was live."""
    del vault_config
    _seed_one_open_gap(template_vault)
    minted = assert_success(call_tool("gapfill_discover", {"topic": TOPIC}))["confirm_nonce"]

    body = assert_success(
        call_tool("gapfill_discover", {"topic": TOPIC, "confirm": "not-the-nonce"})
    )

    assert "confirm_nonce" in body, "a mismatch falls through to phase 1"
    assert body["confirm_nonce"] != minted
    assert "suggestions_staged" not in body


def test_a_nonce_is_single_use(
    vault_config: Path, template_vault: Path, no_live_discovery: None
) -> None:
    """Consuming deletes the file, so a replayed confirm cannot bill twice.

    The only test here that passes a *valid* confirm, and therefore the only one
    that reaches the drain -- hence `no_live_discovery`. What is under test is
    that the nonce is gone afterwards, not the drain itself.
    """
    del vault_config, no_live_discovery
    _seed_one_open_gap(template_vault)
    nonce = assert_success(call_tool("gapfill_discover", {"topic": TOPIC}))["confirm_nonce"]

    first = assert_success(call_tool("gapfill_discover", {"topic": TOPIC, "confirm": nonce}))
    assert "suggestions_staged" in first, "a matching nonce must execute"

    replay = assert_success(call_tool("gapfill_discover", {"topic": TOPIC, "confirm": nonce}))
    assert "confirm_nonce" in replay, "the same nonce must not execute a second time"
    assert "suggestions_staged" not in replay


def test_a_run_eval_nonce_cannot_confirm_a_discovery_drain(
    vault_config: Path, template_vault: Path
) -> None:
    """Nonces are keyed per action, so one billed action cannot authorize another.

    Both actions mint the same token shape into the same directory; only the
    per-action `kind` in the filename keeps a cheap confirmation from unlocking
    an expensive one.
    """
    del vault_config
    from knotica.mcp_server import confirm_nonce

    _seed_one_open_gap(template_vault)
    foreign = confirm_nonce.mint(template_vault, "run-eval", TOPIC, {})

    body = assert_success(call_tool("gapfill_discover", {"topic": TOPIC, "confirm": foreign}))

    assert "confirm_nonce" in body, "a run-eval nonce must not confirm a drain"
    assert "suggestions_staged" not in body


def test_negative_max_gaps_is_invalid_argument(vault_config: Path) -> None:
    del vault_config
    err = error_of(call_tool("gapfill_discover", {"topic": TOPIC, "max_gaps": -1}))
    assert_error_shape(err, code="INVALID_ARGUMENT")


def test_discover_is_registered_and_needs_configuration(unconfigured_env: Path) -> None:
    del unconfigured_env
    err = error_of(call_tool("gapfill_discover", {"topic": TOPIC}))
    assert_error_shape(err, code="NOT_CONFIGURED")


def test_a_stale_confirm_is_logged_distinctly_from_a_real_one(
    vault_config: Path, template_vault: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The three legs of a billed action must be distinguishable in the log.

    `record_dispatch` emits only tool/action/topic, which is byte-identical for a
    free preview, a confirm that billed, and a confirm whose nonce had gone stale
    and silently fell back to a preview. Telling those apart once required
    standing up an instrumented server and driving the real UI through it,
    because the log could not answer "did that click cost anything?".
    """
    import logging

    del vault_config
    _seed_one_open_gap(template_vault)

    with caplog.at_level(logging.INFO, logger="knotica.mcp_server.dispatch_telemetry"):
        call_tool("gapfill_discover", {"topic": TOPIC})
        call_tool("gapfill_discover", {"topic": TOPIC, "confirm": "not-a-live-nonce"})

    outcomes = [record.getMessage() for record in caplog.records if "two-phase" in record.message]

    assert any("outcome=preview" in line and "billed=False" in line for line in outcomes), outcomes
    assert any("outcome=stale-confirm" in line for line in outcomes), (
        "a confirm that bought nothing must say so; it is indistinguishable from a "
        "successful one at the tool surface"
    )
    assert not any("outcome=confirmed" in line for line in outcomes), (
        "nothing here presented a live nonce, so nothing may claim to have billed"
    )
