"""Cross-spine integration test for the self-improvement flywheel.

Derived from ``SYSTEMS_PLAN.md`` (gap-discovery + gap-fill + suggestion-review
architecture) and ``INTERFACE_DESIGN.md`` (``suggestions_review``/``wiki_status``
wire contracts) -- never from the implementation. Every module under test here
(``gap_classifier``, ``gapfill``, ``operations.guillotine``, the MCP suggestion
tools, ``wiki_status``) already has its own focused unit-level test file; this
file pins the *joins* between them -- the places a contract change in one
pipeline silently breaks another:

1. A two-generation eval manifest (one per-id regressor, one added-id failing
   the floor -- both reference-absent) drains into two ``measured`` genuine
   gaps via the real classifier cascade, not a hand-built ``GapRecord``.
2. A conversational ``gap_report`` and a knowledge-weakening guillotine verdict
   each file their own gap -- three provenance origins coexisting in one
   ``gaps.jsonl``.
3. The drain turns every open ``genuine_gap`` into ranked suggestions carrying
   the motivating gap's own ``gap_origin``; a second drain proves the dedup.
4. ``suggestions_review`` (approve/reject/defer) and ``wiki_status`` reconcile
   exactly against what this test built -- no hand-copied magic numbers.
5. Every gaps.jsonl/suggestions.jsonl-touching commit classifies as
   bookkeeping (never re-triggers a loop observation).
6. The vault's git history carries exactly one commit per mutating op, named.

Zero network, zero real LLM: the eval manifests are literal dicts, guillotine
runs its own deterministic classifier, and the discovery service is
``test_gapfill``'s ``_FakeDiscoveryService`` replaying canned candidates.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from knotica.core.gap_classifier import build_gap_records, classify_regression, write_gap_records
from knotica.core.gapfill import report_gap, suggestions_path
from knotica.core.loop import LoopRunner
from knotica.core.operations.guillotine import apply_guillotine
from knotica.guillotine.runner import run_guillotine
from knotica.core.records import (
    GAP_ORIGIN_MEASURED,
    GAP_ORIGIN_REPORTED,
    GAP_ORIGIN_RETRACTED,
    parse_gaps_jsonl,
    parse_suggestions_jsonl,
)
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_head_sha, parse_knotica_commit, run_git

# Reused fixture builders and doubles -- import, never copy-paste (per
# tests/test_gapfill.py, tests/test_guillotine.py, tests/test_mcp_suggestions.py).
from test_gap_classifier import (
    _added_id_floor,
    _per_id,
)
from test_gapfill import (
    _FakeDiscoveryService,
    _candidate,
)
from test_guillotine import (
    ASSERT_PAGE,
    DEMO_CLAIM,
    REFUTE_PAGE,
    SOURCE_PAGE,
)
from test_mcp_suggestions import (
    assert_success,
    call_tool,
)
from test_wiki_status_gaps import _gaps_block
from test_wiki_status_suggestions import _suggestions_block, _topic_row

TOPIC = "agentic-systems"

FIXED_PROPOSED_AT = "2026-07-19T10:00:00Z"


def _unreachable_evaluate(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(
        "evaluate must not be called -- every writer in this walk is bookkeeping, never "
        "a fresh loop observation"
    )


def _freeze_two_goldens(
    vault: Path,
    *,
    first: tuple[str, str, tuple[str, ...]],
    second: tuple[str, str, tuple[str, ...]],
) -> tuple[str, str]:
    """Freeze two held-out questions in one call and return their own ids.

    Adapted from ``test_gap_classifier._freeze_golden_question``: ``freeze()``
    writes the *whole* golden set from the ``accepted`` list it is given (it
    does not append across calls), so freezing two questions in the same vault
    requires one call with both -- unlike that helper's single-question shape.
    Each tuple is ``(query, answer, pages_used)``; ids are located by their own
    question text rather than assumed by list position.
    """
    from knotica.evals.golden import GoldenSetFloorWarning, freeze, load

    store = LocalFSStore(vault)
    with pytest.warns(GoldenSetFloorWarning):
        freeze(
            store,
            vault,
            TOPIC,
            [
                {"question": q, "reference_answer": a, "pages_used": list(p)}
                for q, a, p in (first, second)
            ],
        )
    records = load(store, TOPIC)
    first_id = next(record.id for record in records if record.query == first[0])
    second_id = next(record.id for record in records if record.query == second[0])
    return first_id, second_id


def _seed_guillotine_pages(vault: Path) -> None:
    """Plant the assertion/refutation/source trio the guillotine trial needs.

    Reuses ``test_guillotine``'s own fixture page bodies (imported constants)
    instead of duplicating their content.
    """
    topic_dir = vault / TOPIC
    topic_dir.mkdir(exist_ok=True)
    (topic_dir / "agent-safety.md").write_text(ASSERT_PAGE, encoding="utf-8")
    (topic_dir / "open-agent-ecosystem.md").write_text(REFUTE_PAGE, encoding="utf-8")
    sources_dir = vault / "sources" / TOPIC
    sources_dir.mkdir(parents=True, exist_ok=True)
    (sources_dir / "vendor-report-2026.md").write_text(SOURCE_PAGE, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: seed guillotine fixture pages")


def _gaps_of(store: LocalFSStore, vault: Path) -> list[Any]:
    from knotica.core.gap_classifier import gaps_path

    return parse_gaps_jsonl(store.read_text(gaps_path(TOPIC)))


def _suggestions_of(store: LocalFSStore) -> list[Any]:
    return parse_suggestions_jsonl(store.read_text(suggestions_path(TOPIC)))


def test_full_flywheel_reconciles_gap_report_guillotine_drain_review_and_status(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config  # points the MCP tools' default vault at template_vault
    store = LocalFSStore(template_vault)

    # -----------------------------------------------------------------
    # Setup (not under test): golden questions + guillotine pages. These
    # commits happen before the "before" baseline for the git-history count.
    # -----------------------------------------------------------------
    _seed_guillotine_pages(template_vault)
    qa_regressor, qa_added = _freeze_two_goldens(
        template_vault,
        first=(
            "Does the vault have a page on quantum retrieval augmentation?",
            "No, that concept does not appear in this vault.",
            ("nonexistent-page-regressor",),
        ),
        second=(
            "Does the vault explain retrieval-free speculative verification?",
            "No, that concept does not appear in this vault.",
            ("nonexistent-page-added",),
        ),
    )

    before_sha = git_head_sha(template_vault)
    before_count = git_commit_count(template_vault)

    # -----------------------------------------------------------------
    # 1. Two-generation manifest pair -> one classify -> two measured genuine
    #    gaps (a per-id regressor AND an added-id failing its own floor), both
    #    with an absent reference page.
    # -----------------------------------------------------------------
    floor = _added_id_floor()
    manifest = {
        "manifest_schema_version": 2,
        "generation": 5,
        "per_example": [
            {"id": qa_regressor, "pages": []},
            {
                "id": qa_added,
                "pages": [],
                "qa_accuracy": floor - 0.1,
                "quality": floor - 0.1,
            },
        ],
        "held_out_delta": {
            "ids_added": [qa_added],
            "ids_removed": [],
            "prior_generation": 4,
            "scalar_delta": -0.1,
            "per_id": {qa_regressor: _per_id()},
        },
    }
    from knotica.core.gap_classifier import regressed_ids_from_manifest

    regressed = regressed_ids_from_manifest(manifest)
    assert set(regressed) == {qa_regressor, qa_added}

    classification = classify_regression(
        store=store,
        topic=TOPIC,
        clone_root=template_vault,
        generation=5,
        manifest=manifest,
        regressed_ids=regressed,
    )
    verdicts_by_id = {v.qa_id: v for v in classification.verdicts}
    assert verdicts_by_id[qa_regressor].fault_class == "genuine_gap"
    assert verdicts_by_id[qa_added].fault_class == "genuine_gap"

    measured_records = build_gap_records(
        classification.verdicts,
        topic=TOPIC,
        generation=5,
        scalar_at_detection=0.5,
        baseline_scalar=0.9,
        prior_generation=4,
        clock=lambda: "2026-07-19T09:00:00Z",
    )
    assert len(measured_records) == 2, "both the regressor and the added id are genuine gaps"
    sha_after_classify_write = _write_and_capture(store, template_vault, measured_records)

    # -----------------------------------------------------------------
    # 2. A conversational report and a knowledge-weakening guillotine verdict
    #    each file their own gap -- three origins in one gaps.jsonl.
    # -----------------------------------------------------------------
    report_gap(
        store,
        template_vault,
        TOPIC,
        question="Why does ReAct outperform Reflexion in this vault's own sources?",
    )
    sha_after_report = git_head_sha(template_vault)

    guillotine_result, guillotine_diff = run_guillotine(
        store, template_vault, DEMO_CLAIM, topic=TOPIC, verdict="retract"
    )
    guillotine_envelope = apply_guillotine(
        store, template_vault, guillotine_result, guillotine_diff, summary="retract unsafe claim"
    )
    assert "error" not in guillotine_envelope
    sha_after_guillotine = git_head_sha(template_vault)
    # The apply lands two commits (page patch, then the retracted-gap file) --
    # isolate the gap-filing commit alone for the observe-safety sweep below.
    guillotine_new_shas = (
        run_git(template_vault, "log", "--reverse", f"{sha_after_report}..HEAD", "--format=%H")
        .strip()
        .splitlines()
    )
    assert len(guillotine_new_shas) == 2, "guillotine apply + its retracted-gap file, no more"
    sha_before_retracted_gap, sha_after_retracted_gap = guillotine_new_shas

    all_gaps = _gaps_of(store, template_vault)
    open_gaps = [gap for gap in all_gaps if gap.status == "open"]
    origin_counts = Counter(gap.origin for gap in open_gaps)
    assert origin_counts == {
        GAP_ORIGIN_MEASURED: 2,
        GAP_ORIGIN_REPORTED: 1,
        GAP_ORIGIN_RETRACTED: 1,
    }, "three origins must coexist in one gaps.jsonl, exactly as this test created them"

    # -----------------------------------------------------------------
    # 3. The drain turns ALL open genuine gaps into pending suggestions
    #    carrying the motivating gap's own origin; dedup across a re-drain.
    # -----------------------------------------------------------------
    from knotica.core.gapfill import refresh_suggestions_for_gaps

    candidates = [
        _candidate(),  # default fixture candidate: arXiv-shaped DOI
        _candidate(url="https://blog.example.com/alt-source", title="Alternate source", doi=None),
    ]
    service = _FakeDiscoveryService(candidates)

    refresh_suggestions_for_gaps(
        store, template_vault, TOPIC, service=service, clock=lambda: FIXED_PROPOSED_AT
    )
    sha_after_drain = git_head_sha(template_vault)
    assert len(service.calls) == len(open_gaps), "one discover call per open genuine gap"

    suggestions = _suggestions_of(store)
    assert len(suggestions) == len(open_gaps) * len(candidates), (
        "every open gap must drain into exactly one suggestion per candidate"
    )
    gap_origin_by_id = {gap.gap_id: gap.origin for gap in open_gaps}
    suggestion_origin_counts = Counter(record.gap_origin for record in suggestions)
    expected_suggestion_origin_counts = Counter(
        gap_origin_by_id[record.gap_id] for record in suggestions
    )
    assert suggestion_origin_counts == expected_suggestion_origin_counts
    assert suggestion_origin_counts[GAP_ORIGIN_MEASURED] == 2 * len(candidates)
    assert suggestion_origin_counts[GAP_ORIGIN_REPORTED] == 1 * len(candidates)
    assert suggestion_origin_counts[GAP_ORIGIN_RETRACTED] == 1 * len(candidates)

    # Re-drain with the identical service: nothing new, no second commit.
    refresh_suggestions_for_gaps(
        store, template_vault, TOPIC, service=service, clock=lambda: FIXED_PROPOSED_AT
    )
    sha_after_redrain = git_head_sha(template_vault)
    assert sha_after_redrain == sha_after_drain, "a fully-deduped re-drain must create no commit"
    assert len(_suggestions_of(store)) == len(suggestions), (
        "no duplicate suggestions after re-drain"
    )

    # -----------------------------------------------------------------
    # 4. suggestions_review: approve one, reject one (with reason), defer
    #    one -- then wiki_status reconciles exactly against what was built.
    # -----------------------------------------------------------------
    ordered_ids = sorted(record.suggestion_id for record in suggestions)
    approve_id, reject_id, defer_id = ordered_ids[0], ordered_ids[1], ordered_ids[2]

    approved = assert_success(
        call_tool(
            "suggestions_review",
            {"topic": TOPIC, "suggestion_id": approve_id, "action": "approve", "mode": "apply"},
        )
    )
    assert approved["to_status"] == "approved"
    sha_after_approve = git_head_sha(template_vault)

    rejected = assert_success(
        call_tool(
            "suggestions_review",
            {
                "topic": TOPIC,
                "suggestion_id": reject_id,
                "action": "reject",
                "mode": "apply",
                "reason": "reputability too low for this topic",
            },
        )
    )
    assert rejected["to_status"] == "rejected"
    sha_after_reject = git_head_sha(template_vault)

    deferred = assert_success(
        call_tool(
            "suggestions_review",
            {"topic": TOPIC, "suggestion_id": defer_id, "action": "defer", "mode": "apply"},
        )
    )
    assert deferred["to_status"] == "deferred"
    sha_after_defer = git_head_sha(template_vault)

    final_suggestions = _suggestions_of(store)
    expected_status_counts = Counter(record.status for record in final_suggestions)

    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    suggestions_block = _suggestions_block(body)
    assert suggestions_block["pending"] == expected_status_counts["pending"]
    assert suggestions_block["approved_awaiting_ingest"] == expected_status_counts["approved"]
    assert suggestions_block["deferred"] == expected_status_counts["deferred"]
    assert suggestions_block["rejected"] == expected_status_counts["rejected"]
    assert suggestions_block["ingested"] == expected_status_counts.get("ingested", 0)
    assert suggestions_block["newest_proposed_at"] == FIXED_PROPOSED_AT

    gaps_block = _gaps_block(body)
    final_open_gaps = [gap for gap in _gaps_of(store, template_vault) if gap.status == "open"]
    final_origin_counts = Counter(gap.origin for gap in final_open_gaps)
    assert gaps_block["measured"] == final_origin_counts[GAP_ORIGIN_MEASURED]
    assert gaps_block["reported"] == final_origin_counts[GAP_ORIGIN_REPORTED]
    assert gaps_block["retracted"] == final_origin_counts[GAP_ORIGIN_RETRACTED]
    row = _topic_row(body)
    assert "pages" in row and "curated" in row, (
        "existing wiki_status fields survive both new blocks"
    )

    # -----------------------------------------------------------------
    # 5. Observe-safety sweep: every gaps.jsonl/suggestions.jsonl commit from
    #    all three writers (loop classifier, report/guillotine, suggestion
    #    review) classifies as bookkeeping -- never a fresh loop observation.
    #    (The guillotine's own report/diff/json artifact commit is deliberately
    #    excluded from this sweep -- it never touches wiki page content, but
    #    the transition isn't asserted here either way.)
    # -----------------------------------------------------------------
    runner = LoopRunner(template_vault, TOPIC, evaluate=_unreachable_evaluate)
    assert runner._content_changed_since(before_sha, sha_after_classify_write) is False
    assert runner._content_changed_since(sha_after_classify_write, sha_after_report) is False
    assert runner._content_changed_since(sha_before_retracted_gap, sha_after_retracted_gap) is False
    assert runner._content_changed_since(sha_after_guillotine, sha_after_drain) is False
    assert runner._content_changed_since(sha_after_drain, sha_after_approve) is False
    assert runner._content_changed_since(sha_after_approve, sha_after_reject) is False
    assert runner._content_changed_since(sha_after_reject, sha_after_defer) is False

    # -----------------------------------------------------------------
    # 6. Exactly one commit per mutating op, named via the frozen commit grammar.
    # -----------------------------------------------------------------
    after_count = git_commit_count(template_vault)
    assert after_count == before_count + 8, "eight mutating ops must land eight distinct commits"
    subjects = (
        run_git(template_vault, "log", "--reverse", f"{before_sha}..HEAD", "--format=%s")
        .strip()
        .splitlines()
    )
    ops = [parsed["op"] for subject in subjects if (parsed := parse_knotica_commit(subject))]
    assert ops == [
        "gap_record",  # classify_regression -> build_gap_records -> write (2 measured gaps)
        "gap_record",  # report_gap (reported)
        "guillotine",  # guillotine report/diff/json artifacts (no page mutation)
        "gap_record",  # guillotine's filed retracted gap
        "suggestion_propose",  # the drain
        "suggestion_review",  # approve
        "suggestion_review",  # reject
        "suggestion_review",  # defer
    ]


def _write_and_capture(store: LocalFSStore, vault: Path, records: list[Any]) -> str:
    write_gap_records(store, vault, TOPIC, records)
    return git_head_sha(vault)
