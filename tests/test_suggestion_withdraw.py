"""``approved`` needs an exit that does not claim an ingest happened.

Before ``withdraw``, the only legal transition out of ``approved`` was
``mark_ingested``. An operator who approved a suggestion and then decided
against it -- or who wanted to release one whose candidate the gate had refused
-- could only move it by asserting an ingest that never occurred, writing a
false record into the queue that drives future gap-fill.

``withdraw`` returns it to ``pending`` and asserts nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core import gapfill
from knotica.core.errors import KnoticaError
from knotica.core.records import parse_suggestions_jsonl
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _gap_record():
    from knotica.core.records import GapEvidence, GapRecord

    return GapRecord(
        gap_id="gap-withdraw",
        topic=TOPIC,
        qa_id="qa-withdraw",
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-18T23:01:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question="What is the retrieval augmentation story?",
        reference_pages=("agent-workflow-memory",),
        reference_pages_exist=False,
        evidence=GapEvidence(
            quality_delta=-0.5,
            qa_accuracy_delta=-0.5,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=4,
        ),
        manifest_ref=f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    )


def _candidate():
    from knotica.discovery.records import SourceCandidate

    return SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )


@pytest.fixture
def approved(template_vault: Path) -> tuple[LocalFSStore, Path, str]:
    from knotica.core.transaction import VaultTransaction

    store = LocalFSStore(template_vault)
    records = gapfill.build_suggestion_records(
        _gap_record(), [_candidate()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed") as txn:
        txn.write(
            gapfill.suggestions_path(TOPIC), "".join(r.to_json_line() + "\n" for r in records)
        )
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")
    return store, template_vault, suggestion_id


def _status(store: LocalFSStore, suggestion_id: str) -> str:
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    return next(r for r in records if r.suggestion_id == suggestion_id).status


def _record(store: LocalFSStore, suggestion_id: str):
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    return next(r for r in records if r.suggestion_id == suggestion_id)


def test_withdraw_returns_an_approved_suggestion_to_pending(
    approved: tuple[LocalFSStore, Path, str],
) -> None:
    store, vault, suggestion_id = approved

    result = gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="withdraw")

    assert result.from_status == "approved"
    assert result.to_status == "pending"
    assert _status(store, suggestion_id) == "pending"


def test_withdraw_never_stamps_an_ingest(approved: tuple[LocalFSStore, Path, str]) -> None:
    """The whole point: releasing a suggestion must not record a false ingest."""
    store, vault, suggestion_id = approved

    gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="withdraw")

    assert _record(store, suggestion_id).ingested_at is None


def test_a_withdrawn_suggestion_can_be_approved_again(
    approved: tuple[LocalFSStore, Path, str],
) -> None:
    """Back in the queue means back in the queue -- not a dead end elsewhere."""
    store, vault, suggestion_id = approved
    gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="withdraw")

    gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="approve")

    assert _status(store, suggestion_id) == "approved"


def test_withdraw_records_an_optional_reason(
    approved: tuple[LocalFSStore, Path, str],
) -> None:
    store, vault, suggestion_id = approved

    result = gapfill.apply_decision(
        store, vault, TOPIC, suggestion_id, decision="withdraw", reason="superseded by rank 2"
    )

    assert result.decided_reason == "superseded by rank 2"


def test_withdraw_is_illegal_from_pending(template_vault: Path) -> None:
    """``withdraw`` exits ``approved`` only; it is not a general reset."""
    from knotica.core.transaction import VaultTransaction

    store = LocalFSStore(template_vault)
    records = gapfill.build_suggestion_records(
        _gap_record(), [_candidate()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    with VaultTransaction(store, template_vault, "test_seed", TOPIC, "seed") as txn:
        txn.write(
            gapfill.suggestions_path(TOPIC), "".join(r.to_json_line() + "\n" for r in records)
        )

    with pytest.raises(KnoticaError) as excinfo:
        gapfill.apply_decision(
            store, template_vault, TOPIC, records[0].suggestion_id, decision="withdraw"
        )

    assert "withdraw" in str(excinfo.value)
