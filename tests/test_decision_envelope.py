"""Cross-cutting decision-envelope shape tests.

Derived from ``SYSTEMS_PLAN.md`` §Interfaces / ``INTERFACE_DESIGN.md`` §3
(human-decision ergonomics) -- not from the implementation. Every human-gate
dry-run/preview response is refined (additive fields only, no new mutation
semantics) toward the uniform decision-envelope shape
``{decision_id, summary, context, options, provenance, diff, reason_required}``
so a conversational client can render one consistent decision card regardless
of which gate produced the preview. Covers the three gates named in the
step: ``suggestions_review(mode=dry-run)``, ``source_ingest_submit`` refused
verdict, and ``golden_review_load`` / ``golden(action=load)``. Each test also
asserts the pre-existing fields are still present -- additive-compat, never a
silent rename.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.gapfill import apply_gate_outcome, suggestions_path
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# MCP call harness (mirrors test_mcp_status.py -- each tool test file
# duplicates this small seam per the project's established convention)
# ---------------------------------------------------------------------------


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
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    if isinstance(body, dict):
        assert "error" not in body, f"expected success, got error: {body!r}"
    assert getattr(result, "isError", False) is False
    return body


# ---------------------------------------------------------------------------
# suggestions_review(mode=dry-run) -- decision-envelope fields
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
            "url": "https://arxiv.org/abs/2211.17192",
            "title": "Accelerating LLM Inference with Speculative Decoding",
            "snippet": "We propose...",
            "source_provider": "fake",
            "doi": "10.48550/arXiv.2211.17192",
            "citation_count": 412,
            "reputability": {"tier": "preprint_known_lab", "score": 0.82, "signals": ["arxiv"]},
            "schema_version": 1,
        },
        "status": status,
        "proposed_at": "2026-07-19T07:30:00Z",
        "decided_at": None,
        "decided_reason": None,
        "ingested_at": None,
        "detected_generation": 42,
        "gap_origin": "measured",
    }
    payload.update(overrides)
    return SuggestionRecord(**payload)


def _seed_suggestions(vault: Path, records) -> None:
    store = LocalFSStore(vault)
    path = suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(path, body)


_ENVELOPE_FIELDS = frozenset(
    {"decision_id", "summary", "context", "options", "provenance", "reason_required"}
)


def test_suggestions_review_dry_run_carries_the_full_envelope_shape(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="envelope-approve")])

    body = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "envelope-approve",
                "action": "approve",
                "mode": "dry-run",
            },
        )
    )

    assert _ENVELOPE_FIELDS <= set(body), f"missing envelope fields: {_ENVELOPE_FIELDS - set(body)}"
    # Additive-compat: the pre-existing preview fields are untouched.
    assert body["from_status"] == "pending"
    assert body["to_status"] == "approved"
    assert body["preview"]
    assert body["candidate_title"]

    assert body["decision_id"] == "envelope-approve"
    assert isinstance(body["summary"], str) and body["summary"]
    context = body["context"]
    assert context["gap_question"] == (
        "How does speculative decoding interact with draft-model verification?"
    )
    assert context["why_wiki_fell_short"] == "genuine_gap"
    assert context["topic"] == TOPIC
    options = body["options"]
    assert isinstance(options, list) and len(options) == 1
    assert options[0]["action"] == "approve"
    provenance = body["provenance"]
    assert provenance["source_url"] == "https://arxiv.org/abs/2211.17192"
    assert provenance["reputability"] == 0.82
    assert provenance["origin"] == "measured"
    assert provenance["citation_hint"] == "10.48550/arXiv.2211.17192"


def test_suggestions_review_dry_run_defer_is_marked_reversible(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="envelope-defer")])

    body = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": "envelope-defer",
                "action": "defer",
                "mode": "dry-run",
            },
        )
    )

    assert body["options"][0]["reversible"] is True
    assert body["reason_required"] is False


def test_suggestions_review_dry_run_does_not_mutate_the_vault(
    vault_config: Path, template_vault: Path
) -> None:
    """The additive envelope fields are read-only enrichment -- a dry-run with
    the new fields present must still write nothing (unchanged mutation
    contract, per Step 50's Done-when)."""
    del vault_config
    from support.vault import run_git

    _seed_suggestions(template_vault, [_suggestion_record(suggestion_id="envelope-no-mutate")])
    before_sha = run_git(template_vault, "rev-parse", "HEAD").strip()

    call_tool(
        "suggestions_review",
        {
            "topic": TOPIC,
            "suggestion_id": "envelope-no-mutate",
            "action": "reject",
            "mode": "dry-run",
            "reason": "not relevant",
        },
    )

    after_sha = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert after_sha == before_sha


# ---------------------------------------------------------------------------
# source_ingest_submit refused verdict -- diff shape
# ---------------------------------------------------------------------------


def test_source_ingest_submit_refused_verdict_nests_the_diff_shape(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_suggestions(
        template_vault, [_suggestion_record(suggestion_id="envelope-refused", status="approved")]
    )
    refused_outcome: dict[str, object] = {
        "verdict": "refused",
        "scalar": 0.71,
        "baseline_scalar": 0.80,
        "ref": "loop/x/agentic-systems/source-deadbeef",
        "reason": "regressed 2 previously-passing golden questions",
        "regressed_questions": ["q-0001", "q-0002"],
    }
    apply_gate_outcome(
        LocalFSStore(template_vault),
        template_vault,
        TOPIC,
        "envelope-refused",
        verdict="refused",
        gate_outcome=refused_outcome,
    )

    body = assert_success(
        call_tool(
            "source_ingest_submit",
            {"topic": TOPIC, "suggestion_id": "envelope-refused", "mode": "dry-run"},
        )
    )

    assert body["verdict"] == "refused"
    # Additive-compat: the pre-existing top-level fields are untouched.
    assert body["diff_summary"] == refused_outcome["reason"]
    assert body["regressed_questions"] == refused_outcome["regressed_questions"]
    assert body["diff"] == {
        "diff_summary": refused_outcome["reason"],
        "regressed_questions": refused_outcome["regressed_questions"],
    }


# ---------------------------------------------------------------------------
# golden_review_load / golden(action=load) -- added-vs-displaced diff
# ---------------------------------------------------------------------------


def _write_candidates(vault: Path, relative: str, questions: list[str]) -> None:
    path = vault / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "question": question,
            "reference_answer": f"{question} answer.",
            "citations": [],
            "pages_used": [],
        }
        for question in questions
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_golden_review_load_reports_added_and_displaced_since_last_freeze(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    staging = f"{TOPIC}/.knotica/datasets/golden.staging.jsonl"
    reviewed = f"{TOPIC}/.knotica/datasets/golden.staging.reviewed.jsonl"
    _write_candidates(template_vault, staging, ["What is concept A?", "What is concept B?"])
    _write_candidates(template_vault, reviewed, ["What is concept A?", "What is concept C?"])

    body = assert_success(call_tool("golden_review_load", {"topic": TOPIC}))

    diff = body["diff"]
    assert diff["added"] == ["What is concept B?"]
    assert diff["displaced"] == ["What is concept C?"]
    assert diff["diff_summary"] == "1 added, 1 displaced since last freeze"


def test_golden_review_load_diff_is_all_added_on_a_first_bootstrap(
    vault_config: Path, template_vault: Path
) -> None:
    """No reviewed file exists yet -- nothing has been frozen, so nothing is
    displaced; every staging candidate reads as newly added."""
    del vault_config
    staging = f"{TOPIC}/.knotica/datasets/golden.staging.jsonl"
    _write_candidates(template_vault, staging, ["What is concept A?"])

    body = assert_success(call_tool("golden_review_load", {"topic": TOPIC}))

    assert body["diff"] == {
        "added": ["What is concept A?"],
        "displaced": [],
        "diff_summary": "1 added, 0 displaced since last freeze",
    }
