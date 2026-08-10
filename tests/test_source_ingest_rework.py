"""A refused source candidate must be reworkable, and its verdict must expire.

The reported session: a candidate was refused, and every route back through
the gate was closed. Re-opening the ingest returned a *fresh, empty* context --
the six stored source chunks and four written pages were gone from
``loop/wip/``, having been renamed into ``loop/x/`` by the refusal, so an
``open`` that reads only branch existence saw nothing and started over. Roughly
20,000 words of verbatim source had to be re-transmitted. And once the operator
had rebuilt it, every submit -- in either mode -- replayed the stored verdict,
still quoting a baseline of 0.9548 while the live gate baseline read 0.6562.

The two defects compose into one dead end, so they are tested together: the
resume makes rework cheap, and the input-keyed verdict lets the reworked
candidate actually be evaluated. Neither alone reopens the path.

The refusal itself is correct and is never weakened here -- every scenario
drives a real refusal through ``LoopRunner.poll_once`` first, and asserts the
quarantine ref survives everything that follows.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from knotica.core import gapfill, gate_inputs, source_ingest
from knotica.core.branch_namespaces import quarantine_branch_name
from knotica.core.loop import LoopRunner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord, parse_suggestions_jsonl
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"
HARNESS = "fake-source-gate"
BASELINE = 0.80
PAGE = "agent-workflow-memory"
SOURCE_KEY = "wang2024awm"


# ---------------------------------------------------------------------------
# Fixture builders -- a real approved suggestion driven through the real
# decision state machine, and a real candidate built through the shipped
# source_ingest session lifecycle.
# ---------------------------------------------------------------------------


def _gap_record(*, gap_id: str, qa_id: str):
    from knotica.core.records import GapEvidence, GapRecord

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
        reference_pages=(PAGE,),
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


def _candidate_source():
    from knotica.discovery.records import SourceCandidate

    return SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )


def _approved_suggestion(vault: Path, store: LocalFSStore) -> str:
    from knotica.core.transaction import VaultTransaction

    gap = _gap_record(gap_id="gap-rework", qa_id="qa-rework")
    records = gapfill.build_suggestion_records(
        gap, [_candidate_source()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    with VaultTransaction(store, vault, "test_seed", TOPIC, "seed suggestions") as txn:
        txn.write(
            gapfill.suggestions_path(TOPIC), "".join(r.to_json_line() + "\n" for r in records)
        )
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, vault, TOPIC, suggestion_id, decision="approve")
    return suggestion_id


def _write_candidate_content(vault: Path, handle, *, body: str) -> None:
    """Commit one source chunk and one page onto the open ingest's worktree."""
    vcs = VaultVcs(vault)
    entry = next(e for e in vcs.list_worktrees() if e.get("branch") == handle.candidate)
    worktree = Path(entry["path"])

    source = worktree / "sources" / TOPIC / f"{SOURCE_KEY}.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"# {SOURCE_KEY}\n\nverbatim stored source text\n", encoding="utf-8")

    page = worktree / TOPIC / f"{PAGE}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"# Agent Workflow Memory\n\n{body}\n", encoding="utf-8")

    VaultVcs(worktree).commit_paths(
        [f"sources/{TOPIC}/{SOURCE_KEY}.md", f"{TOPIC}/{PAGE}.md"],
        f"knotica(write_page): {TOPIC} — ingest {PAGE}",
    )


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-rework-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=1,
            harness_version=HARNESS,
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate


def _refuse_a_candidate(vault: Path, store: LocalFSStore) -> str:
    """Drive one real ingest all the way to a real gate refusal.

    Returns the suggestion id. Asserts the refusal actually happened, so a
    later assertion can never be vacuously true against a candidate that was
    quietly merged instead.
    """
    suggestion_id = _approved_suggestion(vault, store)
    handle = source_ingest.open_ingest(store, vault, TOPIC, suggestion_id)
    _write_candidate_content(vault, handle, body="first draft, dilutive")
    source_ingest.publish_ingest(handle)

    runner = LoopRunner(
        vault,
        TOPIC,
        evaluate=_fake_evaluate(0.50),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(BASELINE, harness_version=HARNESS)
    result = runner.poll_once()

    assert result.acted is True, "the fixture must produce a real gate cycle"
    record = _record(store, suggestion_id)
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "refused", "the fixture must produce a real refusal"
    return suggestion_id


def _record(store: LocalFSStore, suggestion_id: str):
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    return next(r for r in records if r.suggestion_id == suggestion_id)


# ---------------------------------------------------------------------------
# Resume from quarantine
# ---------------------------------------------------------------------------


def test_reopening_a_refused_ingest_restores_its_source_and_pages(
    template_vault: Path,
) -> None:
    """The blocker: re-open used to hand back an empty context to rebuild from."""
    store = LocalFSStore(template_vault)
    suggestion_id = _refuse_a_candidate(template_vault, store)

    handle = source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)

    assert handle.state == "resumed", "a refused candidate's work is recoverable, not gone"
    assert handle.resume.source_present is True
    assert PAGE in handle.resume.pages_present
    assert handle.resume.restored_from == quarantine_branch_name(TOPIC, suggestion_id), (
        "the caller must be able to tell a rework-resume from an ordinary resume"
    )


def test_a_resume_branches_from_the_quarantine_ref_without_consuming_it(
    template_vault: Path,
) -> None:
    """The quarantine ref is the only forensic record of a refused candidate."""
    store = LocalFSStore(template_vault)
    suggestion_id = _refuse_a_candidate(template_vault, store)
    quarantine = quarantine_branch_name(TOPIC, suggestion_id)
    vcs = VaultVcs(template_vault)
    before = vcs.ref_sha(quarantine)

    source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)

    assert vcs.branch_exists(quarantine), "the audit trail must survive the resume"
    assert vcs.ref_sha(quarantine) == before, "the resume must branch from it, never move it"


def test_a_first_open_is_still_a_created_session(template_vault: Path) -> None:
    """No regression: an ingest that was never gated starts empty, from HEAD."""
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(template_vault, store)

    handle = source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)

    assert handle.state == "created"
    assert handle.resume.source_present is False
    assert handle.resume.pages_present == ()
    assert handle.resume.restored_from is None


def test_an_interrupted_never_gated_ingest_resumes_exactly_as_before(
    template_vault: Path,
) -> None:
    """No regression: the ordinary resume path reports no quarantine origin."""
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(template_vault, store)
    first = source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)
    _write_candidate_content(template_vault, first, body="partial work")

    resumed = source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)

    assert resumed.state == "resumed"
    assert resumed.resume.source_present is True
    assert PAGE in resumed.resume.pages_present
    assert resumed.resume.restored_from is None, (
        "an interrupted ingest was never quarantined; claiming otherwise would "
        "misreport where its work came from"
    )


# ---------------------------------------------------------------------------
# The verdict cache expires when its inputs move
# ---------------------------------------------------------------------------


def test_the_gate_stamps_the_inputs_its_verdict_was_computed_from(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _refuse_a_candidate(template_vault, store)

    stamped = gate_inputs.from_record(_record(store, suggestion_id).gate_outcome)

    assert stamped is not None, "a verdict with no fingerprint can never be shown to still apply"
    assert stamped.candidate_tree_sha, "the verdict must record what was evaluated"
    assert stamped.baseline_scalar == BASELINE, "and the bar it was measured against"


def test_an_unfingerprinted_legacy_verdict_is_never_replayed() -> None:
    """The reported session's own record predates the fingerprint entirely."""
    legacy = {"verdict": "refused", "scalar": 0.93, "baseline_scalar": 0.95}

    assert gate_inputs.from_record(legacy) is None


def test_a_changed_baseline_invalidates_the_stored_verdict() -> None:
    stamped = gate_inputs.GateInputs(
        candidate_tree_sha="t1",
        golden_manifest_sha="g1",
        baseline_scalar=0.9548,
        harness_version="h1",
    )
    live = gate_inputs.GateInputs(
        candidate_tree_sha="t1",
        golden_manifest_sha="g1",
        baseline_scalar=0.6562,
        harness_version="h1",
    )

    assert stamped.diff(live) == ("baseline_scalar",)


def test_a_replaced_golden_set_invalidates_the_stored_verdict() -> None:
    """9 -> 21 questions is a different measurement, not the same one repeated."""
    stamped = gate_inputs.GateInputs(golden_manifest_sha="465ad26", baseline_scalar=0.8)
    live = gate_inputs.GateInputs(golden_manifest_sha="222e2eb", baseline_scalar=0.8)

    assert stamped.diff(live) == ("golden_manifest_sha",)


def test_a_rewritten_candidate_invalidates_the_stored_verdict() -> None:
    stamped = gate_inputs.GateInputs(candidate_tree_sha="tree-a", baseline_scalar=0.8)
    live = gate_inputs.GateInputs(candidate_tree_sha="tree-b", baseline_scalar=0.8)

    assert stamped.diff(live) == ("candidate_tree_sha",)


def test_a_changed_harness_invalidates_the_stored_verdict() -> None:
    """Cross-instrument scalars are incomparable, so the verdict is too."""
    stamped = gate_inputs.GateInputs(harness_version="h1")
    live = gate_inputs.GateInputs(harness_version="h2")

    assert stamped.diff(live) == ("harness_version",)


def test_an_untouched_candidate_still_compares_equal() -> None:
    """The idempotency guard the cache exists for must survive the fix."""
    same = gate_inputs.GateInputs(
        candidate_tree_sha="t1", golden_manifest_sha="g1", baseline_scalar=0.8, harness_version="h1"
    )

    assert same.diff(same) == ()


@pytest.mark.parametrize(
    ("stamped", "live"),
    [
        (gate_inputs.GateInputs(harness_version="h1"), gate_inputs.GateInputs()),
        (gate_inputs.GateInputs(), gate_inputs.GateInputs(harness_version="h1")),
    ],
    ids=["known-then-unknown", "unknown-then-known"],
)
def test_one_sided_knowledge_counts_as_changed(
    stamped: gate_inputs.GateInputs, live: gate_inputs.GateInputs
) -> None:
    """Absence of evidence is not evidence of sameness.

    Replaying a verdict reports a measurement that was never taken; a needless
    re-evaluation costs one eval. The asymmetry decides the tie.
    """
    assert stamped.diff(live) == ("harness_version",)


def test_both_sides_unknown_is_not_a_difference() -> None:
    """Otherwise a lean install without ``dspy`` could never replay anything."""
    assert gate_inputs.GateInputs().diff(gate_inputs.GateInputs()) == ()
