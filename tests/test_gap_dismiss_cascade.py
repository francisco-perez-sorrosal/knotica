"""The dismiss cascade: a dismissed gap's still-open suggestions close with it.

A field report proved the strand this file exists to forbid: dismissing two
gaps left nine suggestions ``approved`` against questions nobody wanted
answered any more -- and since ``approved``'s only human exit is ``withdraw``
(one record at a time, if the caller ever finds it), the queue held them
permanently. So ``apply_gap_decision(decision="dismiss")`` now closes the gap's
``pending``/``approved``/``deferred`` suggestions as ``rejected`` -- inside the
same :class:`VaultTransaction`, the same one-commit discipline
``tests/test_gap_lifecycle.py`` pins for the gate's merge closing its
originating gap. ``ingested`` records are history and stay; other gaps'
suggestions are not this dismissal's to touch; ``reopen`` resurrects nothing
(a rejected record does not dedup discovery, so re-draining re-proposes).

Split from ``test_gap_lifecycle.py`` -- whose subject is the two gap-queue
writers themselves -- to keep both files under the size ratchet; the small
record-builder harness is duplicated per the project's per-file convention.

Zero network, zero billing: the writer under test is pure vault I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill import apply_gap_decision, suggestions_path
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_status_porcelain, run_git

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# Record builders (per-file duplicate of the small gap-lifecycle harness)
# ---------------------------------------------------------------------------


def _gap_record(*, gap_id: str, status: str = "open"):
    from knotica.core.records import GapEvidence, GapRecord

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
        evidence=GapEvidence(
            quality_delta=-0.12,
            qa_accuracy_delta=-0.12,
            citation_validity_delta=0.0,
            retrieval_trace=(),
            pages_added=(),
            pages_removed=(),
            prior_generation=4,
        ),
        manifest_ref=f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    )


def _suggestion_record(*, suggestion_id: str, gap_id: str, status: str = "approved", **overrides):
    from knotica.core.records import SuggestionRecord

    payload: dict[str, object] = {
        "suggestion_id": suggestion_id,
        "topic": TOPIC,
        "gap_id": gap_id,
        "qa_id": f"golden-{gap_id}",
        "fault_class": "genuine_gap",
        "question": f"What does {gap_id} leave unanswered?",
        "reference_pages": ("speculative-decoding",),
        "rank": 1,
        "query_text": f"What does {gap_id} leave unanswered?",
        "candidate": {
            "url": "https://arxiv.org/abs/2302.01318",
            "title": "Accelerating LLM Inference with Speculative Decoding",
        },
        "status": status,
        "proposed_at": "2026-07-19T00:00:00Z",
        "decided_at": "2026-07-19T01:00:00Z",
        "decided_reason": None,
        "ingested_at": None,
        "detected_generation": 5,
        "gap_origin": "measured",
    }
    payload.update(overrides)
    return SuggestionRecord(**payload)


# ---------------------------------------------------------------------------
# Seeding and reading the two queues
# ---------------------------------------------------------------------------


def _seed_gaps(store, root: Path, records) -> None:
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, root, "test_seed", TOPIC, "seed gaps for test") as txn:
        txn.write(gaps_path(TOPIC), body)


def _seed_suggestions(store, root: Path, records) -> None:
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, root, "test_seed", TOPIC, "seed suggestions for test") as txn:
        txn.write(suggestions_path(TOPIC), body)


def _gaps_of(store) -> dict[str, Any]:
    from knotica.core.records import parse_gaps_jsonl

    return {record.gap_id: record for record in parse_gaps_jsonl(store.read_text(gaps_path(TOPIC)))}


def _suggestions_of(store) -> dict[str, Any]:
    from knotica.core.records import parse_suggestions_jsonl

    return {
        record.suggestion_id: record
        for record in parse_suggestions_jsonl(store.read_text(suggestions_path(TOPIC)))
    }


def _head_paths(vault: Path) -> list[str]:
    """The vault-relative paths named in the most recent commit."""
    return run_git(vault, "show", "--name-only", "--format=", "HEAD").split()


def _fail_writes_to(store, monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    """Make exactly one vault path un-writable, at the filesystem boundary."""
    real_write = store.write_text_atomic

    def failing_write(path, content: str) -> None:  # noqa: ANN001
        if str(path) == target:
            raise OSError(f"injected disk failure writing {target}")
        real_write(path, content)

    monkeypatch.setattr(store, "write_text_atomic", failing_write)


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


def test_dismissing_a_gap_rejects_its_still_open_suggestions_in_the_same_commit(
    template_vault: Path,
) -> None:
    """Every suggestion still waiting on a human -- pending, approved, or
    deferred -- closes as rejected in the same transaction. ``approved`` is the
    load-bearing case: its only human exit is ``withdraw``, so an uncascaded
    dismissal strands it in the queue permanently."""
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-cascade")])
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(suggestion_id="sug-pending", gap_id="gap-cascade", status="pending"),
            _suggestion_record(
                suggestion_id="sug-approved", gap_id="gap-cascade", status="approved"
            ),
            _suggestion_record(
                suggestion_id="sug-deferred", gap_id="gap-cascade", status="deferred"
            ),
        ],
    )
    before = git_commit_count(template_vault)

    result = apply_gap_decision(
        store, template_vault, TOPIC, "gap-cascade", decision="dismiss", reason="page covers it"
    )

    suggestions = _suggestions_of(store)
    assert {s.status for s in suggestions.values()} == {"rejected"}
    assert suggestions["sug-approved"].decided_reason == "gap dismissed: page covers it", (
        "a cascade-closed record must say which dismissal closed it, not read like "
        "a human rejection with no reason"
    )
    assert sorted(result.cascaded_suggestion_ids) == ["sug-approved", "sug-deferred", "sug-pending"]
    assert git_commit_count(template_vault) == before + 1, (
        "the gap transition and the cascade are one operation, not two commits"
    )
    assert sorted(_head_paths(template_vault)) == sorted(
        [gaps_path(TOPIC), suggestions_path(TOPIC), "log.md"]
    )


def test_the_dismiss_cascade_spares_history_and_other_gaps_suggestions(
    template_vault: Path,
) -> None:
    """``ingested`` is a record of work that happened and stays; a suggestion
    joined to a different gap is not this dismissal's to touch."""
    store = LocalFSStore(template_vault)
    _seed_gaps(
        store,
        template_vault,
        [_gap_record(gap_id="gap-closing"), _gap_record(gap_id="gap-staying")],
    )
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(
                suggestion_id="sug-history",
                gap_id="gap-closing",
                status="ingested",
                ingested_at="2026-07-19T02:00:00Z",
            ),
            _suggestion_record(
                suggestion_id="sug-other-gap", gap_id="gap-staying", status="approved"
            ),
        ],
    )

    result = apply_gap_decision(
        store, template_vault, TOPIC, "gap-closing", decision="dismiss", reason="superseded"
    )

    suggestions = _suggestions_of(store)
    assert suggestions["sug-history"].status == "ingested"
    assert suggestions["sug-other-gap"].status == "approved"
    assert result.cascaded_suggestion_ids == ()
    assert _head_paths(template_vault) == [gaps_path(TOPIC), "log.md"], (
        "a dismissal that cascades nothing leaves the suggestion queue out of the commit entirely"
    )


def test_reopening_a_dismissed_gap_resurrects_no_suggestion(template_vault: Path) -> None:
    """Reopen un-does the gap's dismissal only. Its cascade-rejected suggestions
    stay rejected -- a rejected record does not dedup discovery, so re-draining
    the reopened gap re-proposes its sources with fresh ranking."""
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-round-trip")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-stays-closed", gap_id="gap-round-trip")],
    )
    apply_gap_decision(
        store, template_vault, TOPIC, "gap-round-trip", decision="dismiss", reason="mistake"
    )

    result = apply_gap_decision(store, template_vault, TOPIC, "gap-round-trip", decision="reopen")

    assert _gaps_of(store)["gap-round-trip"].status == "open"
    assert _suggestions_of(store)["sug-stays-closed"].status == "rejected"
    assert result.cascaded_suggestion_ids == ()


def test_a_failing_cascade_write_leaves_the_gap_open_too(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cascade inherits the gap lifecycle's atomicity discipline: a gap
    recorded dismissed beside suggestions still approved is exactly the strand
    the cascade exists to forbid, so a failed suggestion write rolls back the
    gap transition with it.

    Self-proving: the injection can only fire if the suggestions file is
    genuinely written during the dismissal."""
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-half-dismissed")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-survives", gap_id="gap-half-dismissed")],
    )
    before = git_commit_count(template_vault)
    _fail_writes_to(store, monkeypatch, suggestions_path(TOPIC))

    with pytest.raises(OSError):
        apply_gap_decision(
            store, template_vault, TOPIC, "gap-half-dismissed", decision="dismiss", reason="stale"
        )

    monkeypatch.undo()
    assert _gaps_of(store)["gap-half-dismissed"].status == "open"
    assert _suggestions_of(store)["sug-survives"].status == "approved"
    assert git_commit_count(template_vault) == before
    assert git_status_porcelain(template_vault) == ""
