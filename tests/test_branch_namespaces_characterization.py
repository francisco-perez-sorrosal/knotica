"""Characterization safety net for the loop's branch-namespace scatter (P-A
consolidation, pre-extraction baseline).

The five branch-prefix families — ``loop/c/`` (candidate), ``loop/r/``
(result/audit), ``loop/x/`` (quarantine), ``loop/wip/`` (private ingest
session), ``compile/<topic>/`` (compile-review) — are today declared
independently across four files (``core/loop.py``, ``core/source_ingest.py``,
``core/source_gate.py``, ``core/compile_promote.py``), plus the
``classify_candidate``/``suggestion_id_from_branch``/``publish_ingest``
parse/round-trip helpers built on top of them. This file pins every one of
those emitted strings to *today's* exact value and every parse/classify
function to *today's* exact behavior, so the coming ``core/branch_namespaces.py``
extraction (P-A Step 5) can be verified by re-running this file unmodified and
seeing it stay GREEN — any drift in a literal or a parse result is a real
regression, not an acceptable refactor side effect.

Derived from ``RESEARCH_FINDINGS_loop-internals.md`` §3/§4 (the branch-prefix
scatter finding) and a direct read of the four producer modules — not from any
planned extraction shape. Exercised through each module's public surface
(exported constants, public naming functions, ``classify_candidate``,
``suggestion_id_from_branch``, and the real ``open_ingest``/``publish_ingest``
session lifecycle) rather than private parse internals directly, so the tests
survive the extraction without needing to know where the logic physically
lives afterward.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core import compile_promote, gapfill, loop, source_gate, source_ingest
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"

# ---------------------------------------------------------------------------
# Prefix constants -- byte-identical to today's values, across all four files.
# ---------------------------------------------------------------------------


def test_loop_owns_the_candidate_and_result_prefixes() -> None:
    assert loop.DEFAULT_BRANCH_PREFIX == "loop/c/"
    assert loop.RESULT_BRANCH_PREFIX == "loop/r/"


def test_source_ingest_owns_the_wip_and_candidate_prefixes() -> None:
    assert source_ingest.WIP_BRANCH_PREFIX == "loop/wip/"
    assert source_ingest.CANDIDATE_BRANCH_PREFIX == "loop/c/"


def test_source_ingests_candidate_prefix_matches_loops_own_prefix() -> None:
    """Today's scatter fact worth pinning: two files independently declare the
    SAME ``loop/c/`` literal rather than sharing one source of truth -- exactly
    what the coming extraction unifies. If this ever drifts pre-extraction, a
    source candidate publish and the loop's own candidate scan would disagree
    about what a candidate branch looks like."""
    assert source_ingest.CANDIDATE_BRANCH_PREFIX == loop.DEFAULT_BRANCH_PREFIX


def test_source_gate_owns_the_quarantine_prefix() -> None:
    assert source_gate.QUARANTINE_BRANCH_PREFIX == "loop/x/"


def test_compile_promote_derives_the_compile_prefix_from_a_topic() -> None:
    assert compile_promote.compile_branch_prefix(TOPIC) == "compile/agentic-systems/"


def test_compile_promote_rejects_an_invalid_topic_for_the_prefix() -> None:
    from knotica.core.errors import KnoticaError

    with pytest.raises(KnoticaError):
        compile_promote.compile_branch_prefix("nested/topic")


# ---------------------------------------------------------------------------
# Public naming functions -- exact formatted output for a fixed input.
# ---------------------------------------------------------------------------


def test_wip_branch_name_formats_the_private_session_branch() -> None:
    assert (
        source_ingest.wip_branch_name(TOPIC, "0123456789abcdef")
        == "loop/wip/agentic-systems/source-01234567"
    )


def test_candidate_branch_name_formats_the_public_candidate_branch() -> None:
    assert (
        source_ingest.candidate_branch_name(TOPIC, "0123456789abcdef")
        == "loop/c/agentic-systems/source-01234567"
    )


# ---------------------------------------------------------------------------
# classify_candidate -- kind is a pure function of the branch name alone.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        (f"loop/c/{TOPIC}/source-1a2b3c4d", "source"),
        (f"loop/c/{TOPIC}/prompt-1a2b3c4d", "prompt"),
        ("loop/c/wound", "prompt"),
        ("loop/c/1a2b3c4d5e6f", "prompt"),
        (f"loop/wip/{TOPIC}/source-1a2b3c4d", None),
        (f"loop/x/{TOPIC}/source-1a2b3c4d", None),
        ("main", None),
    ],
)
def test_classify_candidate_matches_todays_classification_for_every_branch_shape(
    branch: str, expected: str | None
) -> None:
    assert source_gate.classify_candidate(branch) == expected


def test_suggestion_id_from_branch_recovers_the_id8_infix() -> None:
    branch = f"loop/c/{TOPIC}/source-1a2b3c4d"
    assert source_gate.suggestion_id_from_branch(branch) == "1a2b3c4d"


# ---------------------------------------------------------------------------
# End-to-end round trip: open_ingest -> publish_ingest exercises the private
# WIP-branch parse (``_parse_wip_branch``) without depending on its location.
# ---------------------------------------------------------------------------


def _gap_record(*, gap_id: str, qa_id: str):
    from knotica.core.records import GapEvidence, GapRecord

    evidence = GapEvidence(
        quality_delta=-0.5,
        qa_accuracy_delta=-0.5,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
        prior_generation=4,
    )
    return GapRecord(
        gap_id=gap_id,
        topic=TOPIC,
        qa_id=qa_id,
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question=f"What is the retrieval augmentation story for {qa_id}?",
        reference_pages=("agent-workflow-memory",),
        reference_pages_exist=False,
        evidence=evidence,
        manifest_ref="agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    )


def _approved_suggestion(template_vault: Path, store) -> str:
    from knotica.core.transaction import VaultTransaction
    from knotica.discovery.records import SourceCandidate

    gap = _gap_record(gap_id="gap-namespace-char", qa_id="golden-namespace-char")
    candidate = SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )
    records = gapfill.build_suggestion_records(
        gap, [candidate], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    path = gapfill.suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(
        store, Path(template_vault), "test_seed", TOPIC, "seed suggestion for test"
    ) as txn:
        txn.write(path, body)
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")
    return suggestion_id


def test_publish_ingest_derives_the_public_candidate_name_from_the_wip_branch(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(template_vault, store)
    handle = source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)
    assert handle.candidate == source_ingest.wip_branch_name(TOPIC, suggestion_id)

    published = source_ingest.publish_ingest(handle)

    assert published == source_ingest.candidate_branch_name(TOPIC, suggestion_id)
    assert source_gate.classify_candidate(published) == "source"
    assert source_gate.suggestion_id_from_branch(published) == suggestion_id[:8]
    vcs = VaultVcs(template_vault)
    assert not vcs.branch_exists(handle.candidate), (
        "publish must rename the WIP branch away, not leave both names live"
    )
    assert vcs.branch_exists(published)
