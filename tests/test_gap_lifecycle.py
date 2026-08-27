"""Behavioral contract tests for the gap lifecycle -- the writers that make
``resolved`` and ``dismissed`` reachable.

``GAP_STATUSES`` has declared three statuses since P1 and ``gaps_read`` has
filtered on all three, but only ``open`` was ever written: the queue had a
filer and a reader and no closer. Two writers close it.

* **The machine close.** When a source candidate merges through the gate,
  ``apply_gate_outcome`` already stamps the suggestion's ``gate_outcome`` and
  advances it ``approved -> ingested``. The gap that suggestion was discovered
  *for* must reach ``resolved`` in the same breath -- same transaction, same
  commit -- because a gate reporting a source merged while its motivating gap
  still reads ``open`` is a queue that never drains.
* **The human close.** ``apply_gap_decision`` is the gap lifecycle's sibling of
  ``apply_decision``: a person dismisses a gap they judge not worth sourcing,
  and can reopen it later. ``dismiss`` is legal only from ``open``; ``reopen``
  only from ``dismissed``; every other source status is refused with a typed
  ``INVALID_ARGUMENT`` carrying an actionable fix.

**Atomicity is the subject of this file, not a footnote to it.** The gate close
is a second git-committing write inside an existing mutation span, and a
conflicted gate merge has already stranded this vault once (dec-086) by
unwinding out of a git mutation with no rollback behind it. A success-only suite
passes cleanly against an implementation that stamps the suggestion, fails to
close the gap, and commits the first half anyway. So the two writes are pinned
from three directions: the success case commits **once** naming both files;
a store failure on the gaps path leaves the suggestion **unstamped**; a store
failure on the suggestions path leaves the gap **open**. The second alone would
accept a gap-closing transaction that ran *before* the stamp and the third alone
one that ran *after* -- together they admit only a single span. Each injection is
self-proving too: it can only raise if the path it targets is genuinely written
during the call, so an implementation that never touches the gaps file fails
rather than skating past.

RED-first: neither ``apply_gap_decision`` nor ``GapDecisionResult`` existed when
this file was written -- the paired implementation landed concurrently -- so both
are resolved lazily inside the test bodies. Collection therefore succeeds against
a tree that has neither, and the failure is an ``ImportError`` in the tests that
need them rather than a collection error hiding the whole file. Written without
reading the implementation; every assertion is derived from the stated behaviour
and from the sibling ``apply_decision`` contract this transition mirrors.

Zero network, zero billing: nothing here constructs a discovery service, a
provider, or an LLM client -- both writers under test are pure vault I/O.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill import apply_gate_outcome, suggestions_path
from knotica.core.transaction import VaultTransaction
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_status_porcelain, run_git

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# Lazy resolution of the not-yet-existing symbols (RED handshake)
# ---------------------------------------------------------------------------


def _apply_gap_decision():
    """The human gap transition, imported at call time.

    Deferred so this module still *collects* before the paired implementer step
    lands: the first run must fail with ``ImportError`` inside the tests that
    need it, not a collection error that hides every other test in the file.
    """
    from knotica.core.gapfill import apply_gap_decision

    return apply_gap_decision


def _gap_decision_result_type():
    """The declared return type of the human gap transition, imported at call time."""
    from knotica.core.gapfill import GapDecisionResult

    return GapDecisionResult


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def _gap_evidence(**overrides: object):
    from knotica.core.records import GapEvidence

    payload: dict[str, object] = {
        "quality_delta": -0.12,
        "qa_accuracy_delta": -0.12,
        "citation_validity_delta": 0.0,
        "retrieval_trace": (),
        "pages_added": (),
        "pages_removed": (),
        "prior_generation": 4,
    }
    payload.update(overrides)
    return GapEvidence(**payload)


def _gap_record(*, gap_id: str, status: str = "open", **overrides: object):
    """A well-formed P1 gap record, built directly.

    Direct construction rather than driving the classifier: the classify-and-file
    path is ``tests/test_gap_classifier.py``'s subject, and seeding a non-``open``
    status is impossible through ``write_gap_records`` (which forces ``open``)
    yet is exactly what the illegal-transition cases need.
    """
    from knotica.core.records import GapRecord

    payload: dict[str, object] = {
        "gap_id": gap_id,
        "topic": TOPIC,
        "qa_id": f"golden-{gap_id}",
        "fault_class": "genuine_gap",
        "status": status,
        "classifier_version": 1,
        "detected_generation": 5,
        "detected_at": "2026-07-18T23:01:00Z",
        "scalar_at_detection": 0.9493,
        "baseline_scalar": 0.96,
        "question": f"What does {gap_id} leave unanswered?",
        "reference_pages": ("speculative-decoding",),
        "reference_pages_exist": False,
        "evidence": _gap_evidence(),
        "manifest_ref": f"{TOPIC}/.knotica/eval-runs/gen-5/manifest.json",
    }
    payload.update(overrides)
    return GapRecord(**payload)


def _suggestion_record(*, suggestion_id: str, gap_id: str, status: str = "approved", **overrides):
    """An approved suggestion joined to ``gap_id`` -- the gate's input shape."""
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


def _merged_outcome() -> dict[str, object]:
    return {
        "verdict": "merged",
        "scalar": 0.9712,
        "baseline_scalar": 0.9655,
        "ref": "loop/x/agentic-systems/source-a1b2c3d4",
        "reason": None,
        "regressed_questions": None,
    }


def _refused_outcome() -> dict[str, object]:
    return {
        "verdict": "refused",
        "scalar": 0.9201,
        "baseline_scalar": 0.9655,
        "ref": "loop/x/agentic-systems/source-a1b2c3d4",
        "reason": "regressed 3 previously-passing golden questions",
        "regressed_questions": ["q-0001", "q-0007", "q-0012"],
    }


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
    """The persisted gap queue, keyed by ``gap_id``."""
    from knotica.core.records import parse_gaps_jsonl

    return {record.gap_id: record for record in parse_gaps_jsonl(store.read_text(gaps_path(TOPIC)))}


def _gap_lines(store) -> list[str]:
    """The raw persisted gap lines -- for counting records, not reading them."""
    return store.read_text(gaps_path(TOPIC)).strip().splitlines()


def _suggestions_of(store) -> dict[str, Any]:
    """The persisted suggestion queue, keyed by ``suggestion_id``."""
    from knotica.core.records import parse_suggestions_jsonl

    return {
        record.suggestion_id: record
        for record in parse_suggestions_jsonl(store.read_text(suggestions_path(TOPIC)))
    }


def _head_paths(vault: Path) -> list[str]:
    """The vault-relative paths named in the most recent commit."""
    return run_git(vault, "show", "--name-only", "--format=", "HEAD").split()


def _fail_writes_to(store, monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    """Make exactly one vault path un-writable, at the filesystem boundary.

    The store is the mutation primitive every vault write funnels through, so
    failing it for one path simulates a mid-span disk failure without knowing
    anything about how the operation under test is written. Every other path
    still writes normally, so the transaction reaches the failure with real work
    already buffered behind it.
    """
    real_write = store.write_text_atomic

    def failing_write(path, content: str) -> None:  # noqa: ANN001
        if str(path) == target:
            raise OSError(f"injected disk failure writing {target}")
        real_write(path, content)

    monkeypatch.setattr(store, "write_text_atomic", failing_write)


# ---------------------------------------------------------------------------
# MCP call seam for gaps_read (mirrors tests/test_mcp_gaps_read.py -- each tool
# test file duplicates this small harness per the project's convention)
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    """The verb surface: the published server plus the verbs the lanes absorbed.

    See ``support.dispatch.build_verb_server`` -- this is not the published
    surface, and the tests in this module assert verb *behaviour*, not
    registration.
    """
    from support.dispatch import build_verb_server

    return build_verb_server()


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
# The machine close: a merged gate verdict resolves the originating gap
# ---------------------------------------------------------------------------


def test_a_merged_gate_verdict_resolves_the_gap_the_candidate_was_found_for(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-merged")])
    _seed_suggestions(
        store, template_vault, [_suggestion_record(suggestion_id="sug-merged", gap_id="gap-merged")]
    )

    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "sug-merged",
        verdict="merged",
        gate_outcome=_merged_outcome(),
    )

    assert _gaps_of(store)["gap-merged"].status == "resolved", (
        "a source candidate that merged through the gate closes the knowledge hole it "
        "was discovered for -- the gap cannot stay open once its source has landed"
    )
    assert _suggestions_of(store)["sug-merged"].status == "ingested", (
        "the pre-existing suggestion advance must survive the added gap close"
    )


def test_closing_the_gap_and_stamping_the_suggestion_land_in_one_commit(
    template_vault: Path,
) -> None:
    """Both files move in a single commit -- the audit invariant, and the atomicity
    claim's success-side half. A second transaction would show up here as a second
    commit, and as a HEAD that names only one of the two queues."""
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-one-commit")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-one-commit", gap_id="gap-one-commit")],
    )
    before = git_commit_count(template_vault)

    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "sug-one-commit",
        verdict="merged",
        gate_outcome=_merged_outcome(),
    )

    assert git_commit_count(template_vault) == before + 1, (
        "the gap close rides the gate's existing mutation span and adds no commit of its own"
    )
    touched = _head_paths(template_vault)
    assert gaps_path(TOPIC) in touched
    assert suggestions_path(TOPIC) in touched, (
        "one commit must carry both queues; two commits each carrying one is the "
        "half-written state this pairing exists to make impossible"
    )


def test_a_refused_gate_verdict_leaves_the_gap_open(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-refused")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-refused", gap_id="gap-refused")],
    )

    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "sug-refused",
        verdict="refused",
        gate_outcome=_refused_outcome(),
    )

    assert _gaps_of(store)["gap-refused"].status == "open", (
        "a refused candidate sourced nothing, so the knowledge hole is still there"
    )
    assert _suggestions_of(store)["sug-refused"].status == "approved", (
        "a refused verdict leaves the suggestion re-workable"
    )


def test_a_merged_verdict_resolves_only_the_gap_its_suggestion_names(
    template_vault: Path,
) -> None:
    """One merged source closes one gap. Every other open gap in the queue is a
    different knowledge hole and stays open."""
    store = LocalFSStore(template_vault)
    _seed_gaps(
        store,
        template_vault,
        [_gap_record(gap_id="gap-targeted"), _gap_record(gap_id="gap-bystander")],
    )
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-targeted", gap_id="gap-targeted")],
    )

    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "sug-targeted",
        verdict="merged",
        gate_outcome=_merged_outcome(),
    )

    gaps = _gaps_of(store)
    assert gaps["gap-targeted"].status == "resolved"
    assert gaps["gap-bystander"].status == "open", (
        "resolving every open gap on one merge would silently report holes closed "
        "that nothing was sourced for"
    )


def test_resolving_a_gap_rewrites_it_in_place_rather_than_appending_a_second_record(
    template_vault: Path,
) -> None:
    """The queue's identity is the record, not the line. Appending a ``resolved``
    copy beside the ``open`` original leaves two records claiming one gap_id --
    and the ``(qa_id, fault_class)`` open-dedup that keeps a persistent regression
    from spamming the queue would then read the stale ``open`` line forever."""
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-in-place")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-in-place", gap_id="gap-in-place")],
    )

    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "sug-in-place",
        verdict="merged",
        gate_outcome=_merged_outcome(),
    )

    assert len(_gap_lines(store)) == 1, "the queue must still hold exactly one record per gap"
    assert _gaps_of(store)["gap-in-place"].status == "resolved"


def test_gaps_read_reports_a_resolved_bucket_once_a_candidate_has_merged(
    vault_config: Path, template_vault: Path
) -> None:
    """End-to-end from the gate to the read surface: the ``resolved`` bucket becomes
    non-zero because a source actually merged, not because a record was seeded that
    way. Until the gate closes gaps, this bucket is unreachable in a live vault."""
    del vault_config
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-counted")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-counted", gap_id="gap-counted")],
    )
    apply_gate_outcome(
        store,
        template_vault,
        TOPIC,
        "sug-counted",
        verdict="merged",
        gate_outcome=_merged_outcome(),
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC, "status": "all"}))

    assert body["status_counts"] == {"open": 0, "resolved": 1, "dismissed": 0}


# ---------------------------------------------------------------------------
# Atomicity: a failure anywhere in the span commits neither half
# ---------------------------------------------------------------------------


def test_a_failing_gap_write_leaves_the_gate_stamp_uncommitted_too(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half-write this pairing exists to forbid, injected on the gap side.

    A gate merge that stamps the suggestion and then fails to close the gap must
    commit neither -- the stamp says a source landed, and a vault that records
    that while the gap it answered stays open has published a lie it cannot
    detect. dec-086 is the same failure shape on the same path: a git mutation
    that failed and left the live vault holding its wreckage.

    Self-proving: the injection can only fire if the gaps file is genuinely
    written during this call, so an implementation that never touches it fails
    here on the missing raise rather than passing vacuously.
    """
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-doomed")])
    _seed_suggestions(
        store, template_vault, [_suggestion_record(suggestion_id="sug-doomed", gap_id="gap-doomed")]
    )
    before = git_commit_count(template_vault)
    _fail_writes_to(store, monkeypatch, gaps_path(TOPIC))

    with pytest.raises(OSError):
        apply_gate_outcome(
            store,
            template_vault,
            TOPIC,
            "sug-doomed",
            verdict="merged",
            gate_outcome=_merged_outcome(),
        )

    monkeypatch.undo()
    suggestion = _suggestions_of(store)["sug-doomed"]
    assert suggestion.gate_outcome is None, (
        "the gate stamp must roll back with the gap close -- a stamped suggestion "
        "beside an open gap is the inconsistent vault this test exists to forbid"
    )
    assert suggestion.status == "approved", "the approved -> ingested advance must roll back too"
    assert _gaps_of(store)["gap-doomed"].status == "open"
    assert git_commit_count(template_vault) == before, "a failed operation commits nothing"
    assert git_status_porcelain(template_vault) == "", (
        "the vault must be left clean, not holding the failed operation's wreckage"
    )


def test_a_failing_gate_stamp_leaves_the_gap_unresolved_too(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same forbidden half-write, injected from the other side.

    Failing only the gap write cannot distinguish one span from a gap-closing
    transaction that runs *before* the stamp; failing only the stamp cannot
    distinguish one span from a gap-closing transaction that runs *after*. Both
    directions together admit only a single span.
    """
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-orphaned")])
    _seed_suggestions(
        store,
        template_vault,
        [_suggestion_record(suggestion_id="sug-orphaned", gap_id="gap-orphaned")],
    )
    before = git_commit_count(template_vault)
    _fail_writes_to(store, monkeypatch, suggestions_path(TOPIC))

    with pytest.raises(OSError):
        apply_gate_outcome(
            store,
            template_vault,
            TOPIC,
            "sug-orphaned",
            verdict="merged",
            gate_outcome=_merged_outcome(),
        )

    monkeypatch.undo()
    assert _gaps_of(store)["gap-orphaned"].status == "open", (
        "a gap closed by a gate operation that then failed reports a hole filled by "
        "nothing -- the close must roll back with the stamp"
    )
    assert _suggestions_of(store)["sug-orphaned"].gate_outcome is None
    assert git_commit_count(template_vault) == before
    assert git_status_porcelain(template_vault) == ""


# ---------------------------------------------------------------------------
# The human close: dismiss and reopen
# ---------------------------------------------------------------------------


def test_the_human_gap_transition_is_part_of_the_modules_public_api(
    template_vault: Path,
) -> None:
    """The gap lifecycle's human path is exported alongside the suggestion
    lifecycle's, not reachable only as a private helper."""
    del template_vault
    import knotica.core.gapfill as gapfill

    assert "apply_gap_decision" in gapfill.__all__
    assert "GapDecisionResult" in gapfill.__all__


def test_dismissing_an_open_gap_moves_it_to_dismissed(template_vault: Path) -> None:
    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(
        store,
        template_vault,
        [_gap_record(gap_id="gap-dismissed"), _gap_record(gap_id="gap-untouched")],
    )

    apply_gap_decision(
        store,
        template_vault,
        TOPIC,
        "gap-dismissed",
        decision="dismiss",
        reason="answered by an existing page; not worth sourcing",
    )

    gaps = _gaps_of(store)
    assert gaps["gap-dismissed"].status == "dismissed"
    assert gaps["gap-untouched"].status == "open", (
        "a decision names one gap; every other record in the queue is untouched"
    )


def test_dismissing_a_gap_lands_in_exactly_one_commit(template_vault: Path) -> None:
    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-one-write")])
    before = git_commit_count(template_vault)

    apply_gap_decision(
        store, template_vault, TOPIC, "gap-one-write", decision="dismiss", reason="out of scope"
    )

    assert git_commit_count(template_vault) == before + 1
    assert _head_paths(template_vault) == [gaps_path(TOPIC), "log.md"], (
        "the transition rewrites the gap queue and nothing else"
    )


def test_reopening_a_dismissed_gap_makes_it_open_again(template_vault: Path) -> None:
    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-revived", status="dismissed")])

    apply_gap_decision(
        store,
        template_vault,
        TOPIC,
        "gap-revived",
        decision="reopen",
        reason="the page turned out not to answer it after all",
    )

    assert _gaps_of(store)["gap-revived"].status == "open", (
        "a dismissal is reversible -- a human who changes their mind puts the gap "
        "back in the drain's queue"
    )


def test_the_human_transition_returns_the_declared_result_type(template_vault: Path) -> None:
    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-result")])

    result = apply_gap_decision(
        store, template_vault, TOPIC, "gap-result", decision="dismiss", reason="duplicate"
    )

    assert isinstance(result, _gap_decision_result_type())


@pytest.mark.parametrize("source_status", ["resolved", "dismissed"])
def test_dismissing_a_gap_that_is_not_open_is_refused_with_an_actionable_error(
    template_vault: Path, source_status: str
) -> None:
    """Only an open gap can be dismissed. A resolved gap was answered by a merged
    source and a dismissed one is already dismissed -- neither is a human's to
    dismiss, and refusing loudly beats silently overwriting the earlier outcome."""
    from knotica.core.errors import DEFAULT_FIX, ErrorCode, KnoticaError

    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    gap_id = f"gap-{source_status}-dismiss"
    _seed_gaps(store, template_vault, [_gap_record(gap_id=gap_id, status=source_status)])
    before = git_commit_count(template_vault)

    with pytest.raises(KnoticaError) as caught:
        apply_gap_decision(store, template_vault, TOPIC, gap_id, decision="dismiss")

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert caught.value.fix != DEFAULT_FIX[ErrorCode.INVALID_ARGUMENT], (
        "a refused transition must say what to do about this gap, not fall back to "
        "the generic 'correct the named argument' text"
    )
    assert _gaps_of(store)[gap_id].status == source_status, "a refused decision mutates nothing"
    assert git_commit_count(template_vault) == before, "a refused decision commits nothing"


@pytest.mark.parametrize("source_status", ["open", "resolved"])
def test_reopening_a_gap_that_is_not_dismissed_is_refused_with_an_actionable_error(
    template_vault: Path, source_status: str
) -> None:
    """Reopen is the inverse of dismiss and nothing else: it un-does a human's
    dismissal. An open gap needs no reopening, and reopening a resolved gap would
    silently contradict a merged source without retracting it."""
    from knotica.core.errors import DEFAULT_FIX, ErrorCode, KnoticaError

    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    gap_id = f"gap-{source_status}-reopen"
    _seed_gaps(store, template_vault, [_gap_record(gap_id=gap_id, status=source_status)])
    before = git_commit_count(template_vault)

    with pytest.raises(KnoticaError) as caught:
        apply_gap_decision(store, template_vault, TOPIC, gap_id, decision="reopen")

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert caught.value.fix != DEFAULT_FIX[ErrorCode.INVALID_ARGUMENT]
    assert _gaps_of(store)[gap_id].status == source_status
    assert git_commit_count(template_vault) == before


def test_an_unrecognized_decision_verb_is_refused_with_an_actionable_error(
    template_vault: Path,
) -> None:
    from knotica.core.errors import DEFAULT_FIX, ErrorCode, KnoticaError

    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-bad-verb")])
    before = git_commit_count(template_vault)

    with pytest.raises(KnoticaError) as caught:
        apply_gap_decision(store, template_vault, TOPIC, "gap-bad-verb", decision="resolve")

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert caught.value.fix != DEFAULT_FIX[ErrorCode.INVALID_ARGUMENT], (
        "an unknown verb must be told which verbs exist"
    )
    assert _gaps_of(store)["gap-bad-verb"].status == "open"
    assert git_commit_count(template_vault) == before


def test_a_decision_on_a_gap_the_queue_does_not_hold_is_refused_and_names_it(
    template_vault: Path,
) -> None:
    """A lookup miss is reported, never a silent no-op. The exception *class* is
    left open here on purpose -- the sibling suggestion path raises ``ValueError``
    for the same miss, and the gap transition may reasonably route it through the
    typed envelope instead; what is not negotiable is that the caller learns which
    id was not found."""
    from knotica.core.errors import KnoticaError

    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(store, template_vault, [_gap_record(gap_id="gap-present")])
    before = git_commit_count(template_vault)

    with pytest.raises((ValueError, KnoticaError)) as caught:
        apply_gap_decision(store, template_vault, TOPIC, "gap-absent", decision="dismiss")

    assert "gap-absent" in str(caught.value), "the error must name the id that was not found"
    assert git_commit_count(template_vault) == before


def test_gaps_read_reports_a_dismissed_bucket_once_a_human_has_dismissed_a_gap(
    vault_config: Path, template_vault: Path
) -> None:
    """The third bucket, reached the only way a live vault can reach it."""
    del vault_config
    apply_gap_decision = _apply_gap_decision()
    store = LocalFSStore(template_vault)
    _seed_gaps(
        store,
        template_vault,
        [_gap_record(gap_id="gap-shown"), _gap_record(gap_id="gap-hidden")],
    )
    apply_gap_decision(
        store, template_vault, TOPIC, "gap-hidden", decision="dismiss", reason="not worth sourcing"
    )

    body = assert_success(call_tool("gaps_read", {"topic": TOPIC, "status": "all"}))

    assert body["status_counts"] == {"open": 1, "resolved": 0, "dismissed": 1}
