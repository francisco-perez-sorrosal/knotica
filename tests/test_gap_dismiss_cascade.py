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
directly, but a cascade-closed record does not dedup discovery, so re-draining
the reopened gap re-stages its sources -- asserted here end to end rather than
in prose, because the contract was false for as long as it was only prose.
An ``approved`` record whose candidate branch is already published is spared:
the gate merges that branch before stamping the record, so un-approving it here
is how a merged-but-unstamped source happens.

Split from ``test_gap_lifecycle.py`` -- whose subject is the two gap-queue
writers themselves -- to keep both files under the size ratchet; the small
record-builder harness is duplicated per the project's per-file convention.

Zero network, zero billing: the writers under test are pure vault I/O, and the
drain is driven by a canned fake service.
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


def _candidate(url: str):
    from knotica.discovery.records import SourceCandidate

    return SourceCandidate(
        url=url,
        title="Accelerating LLM Inference with Speculative Decoding",
        snippet="We propose a novel decoding scheme...",
        source_provider="fake",
    )


class _FakeDiscoveryService:
    """Replays one canned candidate list per ``discover`` call (zero network)."""

    def __init__(self, candidates) -> None:
        self._candidates = list(candidates)

    def discover(self, query):  # noqa: ANN001, ANN202
        return list(self._candidates)


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


def test_redraining_a_reopened_gap_restages_the_sources_its_dismissal_closed(
    template_vault: Path,
) -> None:
    """The reopen contract, end to end. A cascade rejection is the GAP speaking,
    not a judgement of the source, so it must not dedup a later drain -- without
    this the reopened gap is sourceless forever (only a merged source resolves a
    gap, and the gate needs an approved suggestion) and its only exit is another
    dismissal. A human's own ``reject`` still dedups; that respect is the point."""
    from knotica.core.gapfill import refresh_suggestions_for_gaps

    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-round-trip")])
    candidate = _candidate("https://arxiv.org/abs/2302.01318")
    service = _FakeDiscoveryService([candidate])
    refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)
    staged = next(iter(_suggestions_of(store).values()))
    apply_gap_decision(
        store, template_vault, TOPIC, "gap-round-trip", decision="dismiss", reason="mistake"
    )
    assert _suggestions_of(store)[staged.suggestion_id].status == "rejected"

    apply_gap_decision(store, template_vault, TOPIC, "gap-round-trip", decision="reopen")
    result = refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    revived = _suggestions_of(store)
    assert result.suggestions_written == 1
    assert revived[staged.suggestion_id].status == "pending"
    assert len(revived) == 1, (
        "the re-stage must REPLACE the cascade-closed line, not append a second record "
        "under the same deterministic suggestion id"
    )


def test_a_human_rejection_still_dedups_a_later_drain(template_vault: Path) -> None:
    """The narrow exclusion: only a cascade closure is re-proposable."""
    from knotica.core.gapfill import apply_decision, refresh_suggestions_for_gaps

    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-human-reject")])
    service = _FakeDiscoveryService([_candidate("https://arxiv.org/abs/2302.01318")])
    refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)
    staged = next(iter(_suggestions_of(store).values()))
    apply_decision(
        store,
        template_vault,
        TOPIC,
        staged.suggestion_id,
        decision="reject",
        reason="reputability too low",
    )

    result = refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    assert result.suggestions_written == 0
    assert _suggestions_of(store)[staged.suggestion_id].status == "rejected"


def test_a_decision_taken_during_a_slow_drain_survives_the_drains_write(
    template_vault: Path,
) -> None:
    """The drain rewrites the whole queue, so it must read the queue AFTER the
    (slow, networked) discovery phase, not before it. A snapshot taken ahead of
    a 40-second search silently reverts every approval an operator made
    meanwhile -- no error, no ``changed=False``, just a normal-looking refresh.

    Self-proving: the concurrent decision is injected from inside ``discover``,
    the exact window the old ordering left open."""
    from knotica.core.gapfill import apply_decision, refresh_suggestions_for_gaps

    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-race")])
    first = _FakeDiscoveryService([_candidate("https://arxiv.org/abs/2302.01318")])
    refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=first)
    staged = next(iter(_suggestions_of(store).values()))

    class _DecidingService:
        """Approves the staged record mid-discovery, as another session would."""

        def discover(self, query):  # noqa: ANN001, ANN202
            apply_decision(store, template_vault, TOPIC, staged.suggestion_id, decision="approve")
            return [_candidate("https://arxiv.org/abs/2401.00001")]

    refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=_DecidingService())

    assert _suggestions_of(store)[staged.suggestion_id].status == "approved", (
        "a decision committed during the drain's discovery phase must survive the "
        "drain's own full-file write"
    )


def test_the_cascade_spares_an_approved_record_with_a_published_candidate_branch(
    template_vault: Path,
) -> None:
    """The gate merges a source branch and only THEN stamps its record, so a
    dismissal that un-approves a published candidate behind the gate's back is
    how a merged-but-unstamped source happens. Such a record is left for the
    gate to disposition."""
    from knotica.core.branch_namespaces import candidate_branch_name

    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-in-flight")])
    _seed_suggestions(
        store,
        template_vault,
        [
            _suggestion_record(suggestion_id="sug-published", gap_id="gap-in-flight"),
            _suggestion_record(suggestion_id="sug-idle", gap_id="gap-in-flight", status="pending"),
        ],
    )
    run_git(template_vault, "branch", candidate_branch_name(TOPIC, "sug-published"))

    result = apply_gap_decision(
        store, template_vault, TOPIC, "gap-in-flight", decision="dismiss", reason="not needed"
    )

    suggestions = _suggestions_of(store)
    assert suggestions["sug-published"].status == "approved"
    assert suggestions["sug-idle"].status == "rejected"
    assert result.cascaded_suggestion_ids == ("sug-idle",)


def test_a_refused_gap_transition_names_the_legal_exit_from_the_records_status(
    template_vault: Path,
) -> None:
    """A refusal that names only the attempted decision's legal sources leaves
    the caller stuck -- the sibling suggestion lifecycle was corrected for this
    and the gap lifecycle has the same shape."""
    from knotica.core.errors import KnoticaError

    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-twice")])
    apply_gap_decision(
        store, template_vault, TOPIC, "gap-twice", decision="dismiss", reason="duplicate"
    )

    with pytest.raises(KnoticaError) as excinfo:
        apply_gap_decision(
            store, template_vault, TOPIC, "gap-twice", decision="dismiss", reason="again"
        )

    assert "reopen" in (excinfo.value.fix or ""), (
        "the fix text must name the exit the record's ACTUAL status has, not only the "
        "attempted decision's legal source"
    )


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
