"""Behavioral contract tests for the gap-fill drain + decide module (``core/gapfill.py``).

Derived from ``SYSTEMS_PLAN.md`` §Architecture > Data Flow / Decisions and
``INTERFACE_DESIGN.md`` §D2 (lifecycle state machine) — never from the
implementation. ``core.gapfill`` joins P1's committed ``genuine_gap`` records to
a P2 ``DiscoveryService``'s ranked candidates as committed ``pending``
``SuggestionRecord``s, and separately mediates the human approve/reject/defer
decision. Discovery is injected (a fake, zero network) so the drain's own
logic -- filtering, dedup, one-commit-per-drain, failure propagation -- is
under test, not the real search/enrich/score pipeline (already covered by
``tests/discovery/test_service.py``).

RED-first: ``knotica.core.gapfill`` does not exist yet when this file is
written (paired implementer step lands concurrently) — every production
symbol is resolved lazily inside a helper or the test body so collection
succeeds and the first run fails with ``ModuleNotFoundError``, not a
collection error. This file was written without reading the implementer's
code.

Zero network throughout: ``_FakeDiscoveryService`` replays canned
``SourceCandidate`` lists and records every ``SearchQuery`` it was driven
with, mirroring ``knotica.discovery.provider.FakeSearchProvider``.
"""

from pathlib import Path

import pytest

from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_head_sha

TOPIC = "agentic-systems"


def _gapfill_module():
    import knotica.core.gapfill

    return knotica.core.gapfill


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeDiscoveryService:
    """A zero-network stand-in for ``DiscoveryService`` -- replays canned candidates.

    Every ``discover`` call is recorded on ``calls`` so a test can assert the
    exact number of discovery invocations the drain issued (one per surviving
    open ``genuine_gap``, never one for a ``dilution`` or non-``open`` gap).
    """

    def __init__(self, candidates=None) -> None:
        self.calls: list = []
        self._candidates = list(candidates or [])

    def discover(self, query):
        self.calls.append(query)
        return list(self._candidates)


class _RaisingDiscoveryService:
    """A ``DiscoveryService`` stand-in that always raises -- the drain must not
    swallow it; failure isolation is the loop hook's job, not this module's."""

    def discover(self, query):
        raise RuntimeError("discovery unreachable")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _gap_evidence(**overrides):
    from knotica.core.records import GapEvidence

    payload = {
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


def _gap_record(*, gap_id: str, qa_id: str, **overrides):
    from knotica.core.records import GapRecord

    payload: dict[str, object] = {
        "gap_id": gap_id,
        "topic": TOPIC,
        "qa_id": qa_id,
        "fault_class": "genuine_gap",
        "status": "open",
        "classifier_version": 1,
        "detected_generation": 5,
        "detected_at": "2026-07-18T23:01:00Z",
        "scalar_at_detection": 0.9493,
        "baseline_scalar": 0.96,
        "question": f"What is the retrieval augmentation story for {qa_id}?",
        "reference_pages": ("speculative-decoding",),
        "reference_pages_exist": False,
        "evidence": _gap_evidence(),
        "manifest_ref": "agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    }
    payload.update(overrides)
    return GapRecord(**payload)


def _candidate(**overrides):
    from knotica.discovery.records import SourceCandidate

    payload: dict[str, object] = {
        "url": "https://arxiv.org/abs/2302.01318",
        "title": "Accelerating LLM Inference with Speculative Decoding",
        "snippet": "We propose a novel decoding scheme...",
        "source_provider": "fake",
        "doi": "10.48550/arXiv.2302.01318",
        "citation_count": 412,
    }
    payload.update(overrides)
    return SourceCandidate(**payload)


def _seed_gaps(store, root, topic: str, records) -> None:
    """Commit a fixed set of gap records once -- test-only seeding via a direct
    ``VaultTransaction``, so a test can plant a non-``open`` status or a
    ``dilution`` fault class directly (``write_gap_records`` always forces
    ``status='open'`` and its own dedup, which this fixture must bypass)."""
    from knotica.core.gap_classifier import gaps_path

    path = gaps_path(topic)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(store, Path(root), "test_seed", topic, "seed gaps for test") as txn:
        txn.write(path, body)


def _seed_suggestions(store, root, topic: str, records) -> None:
    """Commit a fixed set of suggestion records once -- test-only seeding for
    the decide-path tests, bypassing the drain so ``apply_decision`` is under
    test in isolation from ``refresh_suggestions_for_gaps``."""
    mod = _gapfill_module()

    path = mod.suggestions_path(topic)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(
        store, Path(root), "test_seed", topic, "seed suggestions for test"
    ) as txn:
        txn.write(path, body)


# ---------------------------------------------------------------------------
# formulate_query -- deterministic, one query per gap
# ---------------------------------------------------------------------------


def test_formulate_query_is_deterministic_for_the_same_gap():
    mod = _gapfill_module()
    gap = _gap_record(gap_id="gap-det", qa_id="golden-det")

    first = mod.formulate_query(gap)
    second = mod.formulate_query(gap)

    assert first == second, "the same gap must always formulate the identical query"
    assert first.text == gap.question, "the failed golden question IS the information need"


def test_formulate_query_differs_for_a_different_gaps_question():
    mod = _gapfill_module()
    gap_a = _gap_record(gap_id="gap-a", qa_id="golden-a", question="Question A?")
    gap_b = _gap_record(gap_id="gap-b", qa_id="golden-b", question="Question B?")

    query_a = mod.formulate_query(gap_a)
    query_b = mod.formulate_query(gap_b)

    assert query_a.text != query_b.text


# ---------------------------------------------------------------------------
# refresh_suggestions_for_gaps -- filtering: genuine_gap + open only
# ---------------------------------------------------------------------------


def test_drain_issues_one_discover_call_per_open_genuine_gap_and_stages_ranked_suggestions(
    template_vault: Path,
):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gaps = [
        _gap_record(gap_id="gap-one", qa_id="golden-one", question="Q1?"),
        _gap_record(gap_id="gap-two", qa_id="golden-two", question="Q2?"),
    ]
    _seed_gaps(store, template_vault, TOPIC, gaps)
    candidates = [
        _candidate(url="https://a.example", doi=None),
        _candidate(url="https://b.example", doi=None),
    ]
    service = _FakeDiscoveryService(candidates)

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    assert len(service.calls) == 2, "one discover call per open genuine_gap, never fewer or more"
    persisted = store.read_text(mod.suggestions_path(TOPIC)).strip().splitlines()
    assert len(persisted) == 4, "2 gaps x 2 ranked candidates each = 4 staged pending suggestions"


def test_drain_carries_each_gaps_origin_onto_its_suggestion_records(template_vault: Path):
    """A reported gap drains into suggestions exactly like a measured one --
    the resulting ``SuggestionRecord.gap_origin`` must reflect the motivating
    gap's own provenance, not a blanket default (dec-025, piece B)."""
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gaps = [
        _gap_record(gap_id="gap-measured", qa_id="golden-measured", origin="measured"),
        _gap_record(gap_id="gap-reported", qa_id="golden-reported", origin="reported"),
    ]
    _seed_gaps(store, template_vault, TOPIC, gaps)
    candidates = [_candidate(url="https://a.example", doi=None)]
    service = _FakeDiscoveryService(candidates)

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    from knotica.core.records import parse_suggestions_jsonl

    persisted = parse_suggestions_jsonl(store.read_text(mod.suggestions_path(TOPIC)))
    by_gap_id = {record.gap_id: record.gap_origin for record in persisted}
    assert by_gap_id == {"gap-measured": "measured", "gap-reported": "reported"}


def test_dilution_gap_never_produces_a_discover_call(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-dilution", qa_id="golden-dilution", fault_class="dilution")
    _seed_gaps(store, template_vault, TOPIC, [gap])
    service = _FakeDiscoveryService([_candidate()])

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    assert service.calls == [], "a dilution gap is P4-quarantine input, never a discovery query"
    assert not store.exists(mod.suggestions_path(TOPIC)), (
        "nothing survives the filter, so no suggestions file (and no commit) is created"
    )


def test_non_open_genuine_gap_is_not_drained(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-resolved", qa_id="golden-resolved", status="resolved")
    _seed_gaps(store, template_vault, TOPIC, [gap])
    service = _FakeDiscoveryService([_candidate()])

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    assert service.calls == [], "only status=='open' genuine_gap records are eligible for drain"


# ---------------------------------------------------------------------------
# Dedup on (gap_id, source_key) -- re-draining does not spam the queue
# ---------------------------------------------------------------------------


def test_draining_the_same_gap_twice_does_not_duplicate_suggestions(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-repeat", qa_id="golden-repeat")
    _seed_gaps(store, template_vault, TOPIC, [gap])
    service = _FakeDiscoveryService([_candidate(url="https://persistent-source.example", doi=None)])

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)
    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    persisted = store.read_text(mod.suggestions_path(TOPIC)).strip().splitlines()
    assert len(persisted) == 1, (
        "a second drain of an already-suggested (gap_id, source_key) pair must not append "
        "a duplicate -- a persistent regression must not spam the queue"
    )


# ---------------------------------------------------------------------------
# Own-transaction commit isolation (P1 own-commit test pattern, reused)
# ---------------------------------------------------------------------------


def test_suggestion_write_lands_in_exactly_one_commit_touching_only_the_suggestions_file(
    template_vault: Path,
):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-commit", qa_id="golden-commit")
    _seed_gaps(store, template_vault, TOPIC, [gap])
    before_count = git_commit_count(template_vault)
    before_sha = git_head_sha(template_vault)
    service = _FakeDiscoveryService([_candidate()])

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    after_count = git_commit_count(template_vault)
    assert after_count == before_count + 1, (
        "a suggestion-propose write must land in exactly one new commit"
    )
    vcs = VaultVcs(template_vault)
    changed = vcs.changed_paths(before_sha, vcs.head_sha())
    assert set(changed) == {mod.suggestions_path(TOPIC), "log.md"}, (
        "the suggestion-propose commit must touch only suggestions.jsonl (plus its own "
        "log.md entry) -- never bundled with the gap-record commit or anything else"
    )


# ---------------------------------------------------------------------------
# Observe-safety: a suggestion commit is bookkeeping, never re-triggers eval
# ---------------------------------------------------------------------------


def _unreachable_evaluate(*_args, **_kwargs):
    raise AssertionError(
        "evaluate must not be called -- this test only exercises the loop's bookkeeping "
        "classifier, never a real observation cycle"
    )


def test_suggestion_commit_is_classified_as_bookkeeping_not_content(template_vault: Path):
    """The suggestion write must never re-trigger a fresh loop observation --
    the exact same observe-safety guarantee ``.knotica/gaps/`` already carries."""
    from knotica.core.loop import LoopRunner

    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-observe", qa_id="golden-observe")
    _seed_gaps(store, template_vault, TOPIC, [gap])
    vcs = VaultVcs(template_vault)
    before_sha = vcs.head_sha()
    service = _FakeDiscoveryService([_candidate()])

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=service)

    after_sha = vcs.head_sha()
    runner = LoopRunner(template_vault, TOPIC, evaluate=_unreachable_evaluate)
    assert runner._content_changed_since(before_sha, after_sha) is False, (
        "a write under .knotica/suggestions/ must classify as bookkeeping, exactly like "
        ".knotica/gaps/ -- it must never re-trigger a fresh eval observation"
    )


# ---------------------------------------------------------------------------
# The drain never swallows a failure from the injected service (R3 note --
# failure isolation is the loop hook's concern, not this module's)
# ---------------------------------------------------------------------------


def test_a_failure_from_the_injected_service_propagates_uncaught(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-raise", qa_id="golden-raise")
    _seed_gaps(store, template_vault, TOPIC, [gap])

    with pytest.raises(RuntimeError, match="discovery unreachable"):
        mod.refresh_suggestions_for_gaps(
            store, template_vault, TOPIC, service=_RaisingDiscoveryService()
        )


# ---------------------------------------------------------------------------
# build_default_discovery_service -- degrades to None, never raises
# ---------------------------------------------------------------------------


def test_build_default_discovery_service_returns_none_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
):
    mod = _gapfill_module()
    monkeypatch.delenv("KNOTICA_YOUCOM_API_KEY", raising=False)

    assert mod.build_default_discovery_service() is None


def test_drain_with_no_configured_service_writes_nothing_and_does_not_raise(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    gap = _gap_record(gap_id="gap-nokey", qa_id="golden-nokey")
    _seed_gaps(store, template_vault, TOPIC, [gap])

    mod.refresh_suggestions_for_gaps(store, template_vault, TOPIC, service=None)

    assert not store.exists(mod.suggestions_path(TOPIC)), (
        "a None service must degrade to a clean no-op, never raise and never write"
    )


# ---------------------------------------------------------------------------
# apply_decision -- reject requires a non-empty reason
# ---------------------------------------------------------------------------


def _pending_suggestion(template_vault: Path, store, *, qa_id: str, gap_id: str):
    mod = _gapfill_module()
    gap = _gap_record(gap_id=gap_id, qa_id=qa_id)
    records = mod.build_suggestion_records(
        gap, [_candidate()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    _seed_suggestions(store, template_vault, TOPIC, records)
    return records[0].suggestion_id


def test_reject_without_a_reason_is_refused_with_a_typed_error(template_vault: Path):
    from knotica.core.errors import KnoticaError

    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _pending_suggestion(
        template_vault, store, qa_id="golden-reject-empty", gap_id="gap-reject-empty"
    )

    with pytest.raises(KnoticaError):
        mod.apply_decision(
            store, template_vault, TOPIC, suggestion_id, decision="reject", reason=""
        )

    persisted = _records_of(store, mod)
    assert persisted[0].status == "pending", "a refused reject must never mutate the record"


def test_reject_with_a_non_empty_reason_persists_the_reason(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _pending_suggestion(
        template_vault, store, qa_id="golden-reject-reason", gap_id="gap-reject-reason"
    )

    mod.apply_decision(
        store,
        template_vault,
        TOPIC,
        suggestion_id,
        decision="reject",
        reason="reputability too low for this topic",
    )

    persisted = _records_of(store, mod)
    assert persisted[0].status == "rejected"
    assert persisted[0].decided_reason == "reputability too low for this topic"


def _records_of(store, mod):
    from knotica.core.records import parse_suggestions_jsonl

    return parse_suggestions_jsonl(store.read_text(mod.suggestions_path(TOPIC)))


# ---------------------------------------------------------------------------
# apply_decision -- illegal transitions are refused (D2 lifecycle contract)
# ---------------------------------------------------------------------------


def test_deciding_on_an_already_terminal_suggestion_is_refused(template_vault: Path):
    from knotica.core.errors import KnoticaError

    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _pending_suggestion(
        template_vault, store, qa_id="golden-terminal", gap_id="gap-terminal"
    )
    mod.apply_decision(
        store, template_vault, TOPIC, suggestion_id, decision="reject", reason="not relevant"
    )

    with pytest.raises(KnoticaError):
        mod.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")


def test_approving_a_pending_suggestion_flips_status_and_records_decided_at(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _pending_suggestion(
        template_vault, store, qa_id="golden-approve", gap_id="gap-approve"
    )

    mod.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")

    persisted = _records_of(store, mod)
    assert persisted[0].status == "approved"
    assert persisted[0].decided_at is not None, (
        "an approve must stamp decided_at -- an auditable, cross-process-visible decision"
    )


def test_plan_decision_previews_the_exact_transition_apply_performs(template_vault: Path):
    """The dry-run plan and the applied result must agree on the transition --
    a two-phase tool shows the user precisely what apply will do, in isolation
    (pure function, no store, no I/O)."""
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    suggestion_id = _pending_suggestion(
        template_vault, store, qa_id="golden-plan", gap_id="gap-plan"
    )
    record = _records_of(store, mod)[0]

    plan = mod.plan_decision(record, decision="reject", reason="wrong venue")

    assert plan.from_status == "pending"
    assert plan.to_status == "rejected"
    assert plan.decided_reason == "wrong venue"
    mod.apply_decision(
        store, template_vault, TOPIC, suggestion_id, decision="reject", reason="wrong venue"
    )
    applied = _records_of(store, mod)[0]
    assert applied.status == plan.to_status
    assert applied.decided_reason == plan.decided_reason


# ---------------------------------------------------------------------------
# report_gap -- NL-reported gaps from Claude Desktop (piece B, dec-025)
#
# Import-only reuse of ``core.gap_classifier.write_gap_records``/``gaps_path``:
# these tests read ``gaps.jsonl`` back through that module's own path helper,
# never through a private gapfill accessor -- the write path is owned by
# piece A and is not edited here.
# ---------------------------------------------------------------------------


def _reported_gaps(store) -> list:
    from knotica.core.gap_classifier import gaps_path
    from knotica.core.records import parse_gaps_jsonl

    path = gaps_path(TOPIC)
    if not store.exists(path):
        return []
    return parse_gaps_jsonl(store.read_text(path))


def test_report_gap_writes_one_open_genuine_gap_with_reported_origin_and_honest_empty_evidence(
    template_vault: Path,
):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)

    mod.report_gap(
        store, template_vault, TOPIC, question="Why does ReAct outperform Reflexion here?"
    )

    gaps = _reported_gaps(store)
    assert len(gaps) == 1
    record = gaps[0]
    assert record.fault_class == "genuine_gap"
    assert record.status == "open"
    assert record.origin == "reported"
    assert record.question == "Why does ReAct outperform Reflexion here?", (
        "a reported gap carries the reporter's ACTUAL question, never a synthesized one"
    )
    assert record.evidence.quality_delta == 0.0
    assert record.evidence.qa_accuracy_delta == 0.0
    assert record.evidence.citation_validity_delta == 0.0
    assert record.evidence.retrieval_trace == ()
    assert record.evidence.pages_added == ()
    assert record.evidence.pages_removed == (), (
        "a reported gap has no eval manifest behind it -- its evidence must be honestly "
        "empty, never a fabricated delta"
    )


def test_report_gap_persists_the_reporters_reference_pages(template_vault: Path):
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)

    mod.report_gap(
        store,
        template_vault,
        TOPIC,
        question="How does context caching affect retry cost?",
        reference_pages=("prompt-caching",),
    )

    gaps = _reported_gaps(store)
    assert gaps[0].reference_pages == ("prompt-caching",)


def test_report_gap_qa_id_is_deterministic_for_the_same_question(
    template_vault: Path, vault_seed: Path, tmp_path: Path
) -> None:
    """The qa_id must be a pure function of the question text -- two independent
    vaults reporting the identical question must derive the identical id,
    mirroring the golden-set id derivation (``_golden_id``)."""
    import shutil

    mod = _gapfill_module()
    other_vault = tmp_path / "vault-other"
    shutil.copytree(vault_seed, other_vault)
    store_a = LocalFSStore(template_vault)
    store_b = LocalFSStore(other_vault)
    question = "Why does ReAct outperform Reflexion here?"

    mod.report_gap(store_a, template_vault, TOPIC, question=question)
    mod.report_gap(store_b, other_vault, TOPIC, question=question)

    assert _reported_gaps(store_a)[0].qa_id == _reported_gaps(store_b)[0].qa_id


def test_report_gap_qa_id_differs_for_a_different_question(template_vault: Path) -> None:
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)

    mod.report_gap(store, template_vault, TOPIC, question="Question A?")
    mod.report_gap(store, template_vault, TOPIC, question="Question B?")

    gaps = _reported_gaps(store)
    assert gaps[0].qa_id != gaps[1].qa_id


def test_reporting_the_same_question_twice_writes_exactly_one_gap_record(
    template_vault: Path,
) -> None:
    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    question = "What is the memory footprint of long-context Transformers?"

    mod.report_gap(store, template_vault, TOPIC, question=question)
    mod.report_gap(store, template_vault, TOPIC, question=question)

    gaps = _reported_gaps(store)
    assert len(gaps) == 1, "a duplicate report of the identical open gap must not spam the queue"


def test_report_gap_lands_in_exactly_one_commit_touching_only_the_gaps_file(
    template_vault: Path,
) -> None:
    from knotica.core.gap_classifier import gaps_path

    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)
    before_sha = git_head_sha(template_vault)

    mod.report_gap(store, template_vault, TOPIC, question="Does prompt caching change eval cost?")

    after_count = git_commit_count(template_vault)
    assert after_count == before_count + 1, "one reported gap must land in exactly one new commit"
    vcs = VaultVcs(template_vault)
    changed = vcs.changed_paths(before_sha, vcs.head_sha())
    assert set(changed) == {gaps_path(TOPIC), "log.md"}, (
        "the report_gap commit must touch only gaps.jsonl (plus its own log.md entry)"
    )


def test_report_gap_commit_is_classified_as_bookkeeping_not_content(
    template_vault: Path,
) -> None:
    """A reported-gap write must never re-trigger a fresh loop observation --
    the same observe-safety guarantee the classifier's own gap writes carry."""
    from knotica.core.loop import LoopRunner

    mod = _gapfill_module()
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    before_sha = vcs.head_sha()

    mod.report_gap(store, template_vault, TOPIC, question="Is retrieval-augmented eval biased?")

    after_sha = vcs.head_sha()
    runner = LoopRunner(template_vault, TOPIC, evaluate=_unreachable_evaluate)
    assert runner._content_changed_since(before_sha, after_sha) is False
