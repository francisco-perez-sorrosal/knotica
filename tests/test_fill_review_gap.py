"""Behavioral contract tests for the Fill lane's ``review_gap`` action -- the
human gap ``dismiss``/``reopen`` transition, surfaced without a new MCP
registration.

``apply_gap_decision`` (``core/gapfill.py``) already mediates the gap
lifecycle's human path; what does not yet exist is a way to *reach* it other
than importing the core function directly. This module proves the transition
is reachable as ``fill(action="review_gap", ...)`` -- the same routing
guarantee ``test_lane_dispatchers.py`` proves for every other absorbed verb --
and closes the one gap ``tests/test_gap_lifecycle.py`` deliberately left open:
that module pins the legality table against ``apply_gap_decision`` directly
and, by its own docstring, against a ``GapDecisionResult`` that echoes
``reason`` but persists it nowhere. This file is the dispatcher-boundary
proof, plus the persistence proof neither file has made yet.

RED-first: ``review_gap`` is not yet declared in
``process_model.LANE_MEMBERSHIP`` when this file is written -- the paired
implementation lands concurrently -- so every call through
``fill(action="review_gap", ...)`` fails today with the dispatcher's own
"action must be one of ..." rejection. Collection succeeds against the tree as
it stands (nothing here imports a not-yet-existing symbol); the RED is an
assertion failure (or, for the reason-persistence test, an ``AttributeError``
on a field that does not exist yet) inside the test that drives the action,
never a collection error hiding the rest of the file.

Two load-bearing API-shape assumptions, both unavoidable because the
``review_gap`` tool did not exist to consult when this suite was authored --
full reasoning in ``LEARNINGS_test-engineer_step36.md``; the paired
implementation wins on conflict:

1. ``review_gap``'s own parameters are ``topic``, ``gap_id``, ``decision``,
   ``reason`` (default ``""``), ``vault`` -- lifted verbatim from
   ``knotica.core.gapfill.apply_gap_decision``'s keyword names. None of them
   is named ``action``, so the lane's own selector needs no ``<verb>_action``
   rename (``tools_dispatch_lane_common.py``'s collision rule).
2. The dismiss reason is persisted on ``GapRecord.decided_reason`` -- the
   exact field name ``SuggestionRecord`` already carries for
   ``apply_decision``'s sibling transition, and the name
   ``GapDecisionResult``'s own docstring cites as the pattern this step must
   match.

Zero network, zero billing: every writer under test is pure vault I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from knotica.core.errors import DEFAULT_FIX, ErrorCode
from knotica.core.gap_classifier import gaps_path
from knotica.core.records import GapEvidence, GapRecord, parse_gaps_jsonl
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore
from support.dispatch import TOPIC, build_full_server, call_tool, list_tools, payload_of

# ---------------------------------------------------------------------------
# Record builder + seeding (mirrors tests/test_gap_lifecycle.py's convention)
# ---------------------------------------------------------------------------


def _gap_evidence() -> GapEvidence:
    return GapEvidence(
        quality_delta=-0.12,
        qa_accuracy_delta=-0.12,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
        prior_generation=4,
    )


def _gap_record(*, gap_id: str, status: str = "open") -> GapRecord:
    """A well-formed P1 gap record, built directly -- seeding a non-``open``
    status is impossible through the classifier's own ``write_gap_records``
    path (which forces ``open``), yet is exactly what the illegal-transition
    cases below need."""
    return GapRecord(
        gap_id=gap_id,
        topic=TOPIC,
        qa_id=f"golden-{gap_id}",
        fault_class="genuine_gap",
        status=status,
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question=f"What does {gap_id} leave unanswered?",
        reference_pages=("speculative-decoding",),
        reference_pages_exist=False,
        evidence=_gap_evidence(),
        manifest_ref=f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    )


def _seed_gaps(store: LocalFSStore, root: Path, records: list[GapRecord]) -> None:
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, root, "test_seed", TOPIC, "seed gaps for test") as txn:
        txn.write(gaps_path(TOPIC), body)


def _gaps_of(store: LocalFSStore) -> dict[str, GapRecord]:
    """The persisted gap queue, keyed by ``gap_id`` -- read back off disk, not
    off the tool's response, so a persistence bug cannot hide behind an
    honest-looking echo."""
    return {record.gap_id: record for record in parse_gaps_jsonl(store.read_text(gaps_path(TOPIC)))}


# ---------------------------------------------------------------------------
# Assertion helpers over the fill lane's ``CallToolResult``
# ---------------------------------------------------------------------------


def assert_success(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict) and "error" not in body, f"expected success, got {body!r}"
    assert getattr(result, "isError", False) is False
    return body


def error_of(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict) and "error" in body, f"expected an error envelope, got {body!r}"
    assert getattr(result, "isError", False) is True
    return body["error"]


def assert_rejected_by_review_gap_itself(error: dict[str, Any]) -> None:
    """Distinguish a rejection from *inside* ``review_gap`` (the verb was
    reached and refused the request) from the lane's own generic "no such
    action" rejection (the verb was never reached at all).

    Both share ``code=INVALID_ARGUMENT`` today, since the lane's own
    "must be one of ..." rejection is the *only* ``review_gap``-shaped
    failure that exists before the paired implementation lands -- so a test
    that checked only the error code would pass now for the wrong reason
    (the action is unknown) rather than the reason it claims to test (the
    action's own legality check fired). This assertion is what turns those
    cases genuinely red instead of coincidentally green.
    """
    assert "must be one of" not in error["message"], (
        f"expected review_gap's own validation to reject this, not the lane's generic "
        f"unknown-action rejection: {error!r}"
    )


def _review_gap(server: Any, *, gap_id: str, decision: str, reason: str | None = None) -> Any:
    args: dict[str, Any] = {
        "action": "review_gap",
        "topic": TOPIC,
        "gap_id": gap_id,
        "decision": decision,
    }
    if reason is not None:
        args["reason"] = reason
    return call_tool(server, "fill", args)


# ---------------------------------------------------------------------------
# The legality table, through the dispatcher
# ---------------------------------------------------------------------------


def test_review_gap_dismiss_through_the_fill_lane_moves_an_open_gap_to_dismissed(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-open")])
    server = build_full_server()

    assert_success(
        _review_gap(server, gap_id="gap-open", decision="dismiss", reason="not worth sourcing")
    )

    assert _gaps_of(store)["gap-open"].status == "dismissed"


def test_review_gap_dismiss_through_the_fill_lane_requires_a_reason(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-no-reason")])
    server = build_full_server()

    error = error_of(_review_gap(server, gap_id="gap-no-reason", decision="dismiss"))

    assert_rejected_by_review_gap_itself(error)
    assert error["code"] == ErrorCode.INVALID_ARGUMENT.value
    assert error["fix"] != DEFAULT_FIX[ErrorCode.INVALID_ARGUMENT], (
        "a dismiss with no reason must say a reason is required, not fall back to "
        "the generic 'correct the named argument' text"
    )
    assert _gaps_of(store)["gap-no-reason"].status == "open", "a refused dismiss mutates nothing"


@pytest.mark.parametrize("source_status", ["resolved", "dismissed"])
def test_review_gap_dismiss_through_the_fill_lane_is_rejected_from_a_non_open_status(
    vault_config: Path, template_vault: Path, source_status: str
) -> None:
    """Only an open gap is a human's to dismiss. A resolved gap was already
    answered by a merged source and a dismissed one is already dismissed."""
    del vault_config
    store = LocalFSStore(template_vault)
    gap_id = f"gap-{source_status}-dismiss"
    _seed_gaps(store, template_vault, [_gap_record(gap_id=gap_id, status=source_status)])
    server = build_full_server()

    error = error_of(_review_gap(server, gap_id=gap_id, decision="dismiss", reason="trying anyway"))

    assert_rejected_by_review_gap_itself(error)
    assert error["code"] == ErrorCode.INVALID_ARGUMENT.value
    assert error["fix"] != DEFAULT_FIX[ErrorCode.INVALID_ARGUMENT]
    assert _gaps_of(store)[gap_id].status == source_status, "a refused decision mutates nothing"


def test_review_gap_reopen_through_the_fill_lane_moves_a_dismissed_gap_back_to_open(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-dismissed", status="dismissed")])
    server = build_full_server()

    assert_success(
        _review_gap(server, gap_id="gap-dismissed", decision="reopen", reason="changed my mind")
    )

    assert _gaps_of(store)["gap-dismissed"].status == "open"


@pytest.mark.parametrize("source_status", ["open", "resolved"])
def test_review_gap_reopen_through_the_fill_lane_is_rejected_from_a_non_dismissed_status(
    vault_config: Path, template_vault: Path, source_status: str
) -> None:
    """Reopen undoes a dismissal and nothing else: an open gap needs no
    reopening, and reopening a resolved gap would silently contradict a
    merged source without retracting it."""
    del vault_config
    store = LocalFSStore(template_vault)
    gap_id = f"gap-{source_status}-reopen"
    _seed_gaps(store, template_vault, [_gap_record(gap_id=gap_id, status=source_status)])
    server = build_full_server()

    error = error_of(
        _review_gap(server, gap_id=gap_id, decision="reopen", reason="second thoughts")
    )

    assert_rejected_by_review_gap_itself(error)
    assert error["code"] == ErrorCode.INVALID_ARGUMENT.value
    assert error["fix"] != DEFAULT_FIX[ErrorCode.INVALID_ARGUMENT]
    assert _gaps_of(store)[gap_id].status == source_status


# ---------------------------------------------------------------------------
# Zero new MCP registrations -- the "adds no MCP registration" clause
# ---------------------------------------------------------------------------


def test_review_gap_adds_no_mcp_registration(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    names = [tool.name for tool in list_tools(build_full_server())]

    assert len(names) == 21, (
        f"review_gap must reach the surface only via fill(action=...), never as its own "
        f"registration -- expected the unchanged 21-tool surface, got {len(names)}: "
        f"{sorted(names)}"
    )
    assert "review_gap" not in names, (
        "review_gap is a lane-only verb, exactly like gaps_read and gapfill_discover -- it "
        "must never be independently callable"
    )


# ---------------------------------------------------------------------------
# gaps_read reports both terminal buckets
# ---------------------------------------------------------------------------


def test_gaps_read_through_the_fill_lane_reports_both_terminal_buckets_after_review_gap_transitions(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    _seed_gaps(
        store,
        template_vault,
        [
            _gap_record(gap_id="gap-still-open"),
            _gap_record(gap_id="gap-to-dismiss"),
            _gap_record(gap_id="gap-already-resolved", status="resolved"),
        ],
    )
    server = build_full_server()
    assert_success(
        _review_gap(server, gap_id="gap-to-dismiss", decision="dismiss", reason="duplicate report")
    )

    body = assert_success(
        call_tool(server, "fill", {"action": "gaps_read", "topic": TOPIC, "status": "all"})
    )

    assert body["status_counts"] == {"open": 1, "resolved": 1, "dismissed": 1}, (
        "both terminal buckets the human close and the machine close each own must stop "
        "reading as permanently zero once a gap has actually reached them"
    )


# ---------------------------------------------------------------------------
# The RED heart: the dismiss reason must survive a re-read
# ---------------------------------------------------------------------------


def test_dismissing_a_gap_through_the_fill_lane_durably_persists_the_reason_on_the_gap_record(
    vault_config: Path, template_vault: Path
) -> None:
    """``apply_gap_decision`` accepts and echoes ``reason`` today but persists
    it nowhere -- not on the ``GapRecord``, and not in the commit subject or
    ``log.md`` (see ``GapDecisionResult``'s own docstring). A dismissal "with a
    reason" is not met until a dismissed gap can be re-read and still carry
    the reason a human gave for it -- exactly the durability
    ``apply_decision``'s sibling ``decided_reason`` already guarantees for a
    rejected suggestion.
    """
    del vault_config
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-with-reason")])
    server = build_full_server()
    given_reason = "answered by an existing page; not worth sourcing"

    assert_success(
        _review_gap(server, gap_id="gap-with-reason", decision="dismiss", reason=given_reason)
    )

    persisted = _gaps_of(store)["gap-with-reason"]
    assert persisted.status == "dismissed"
    assert persisted.decided_reason == given_reason, (
        "a reason accepted at the dispatcher boundary must survive a re-read of the gap "
        "record -- echoing it back in the response and dropping it on write is exactly the "
        "gap this step exists to close"
    )
