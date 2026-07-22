"""Characterization safety net for the loop's candidate-gate cluster
(``poll_once`` / ``_next_candidate`` / ``_process_candidate`` / ``_keep`` /
``_discard``, ``core/loop.py:~903-1095``, pre-extraction baseline).

Black-box against :meth:`LoopRunner.poll_once` only -- no reference to where
the code physically lives, so the coming ``candidate_gate.py`` extraction can
be verified by re-running this file unmodified and seeing it stay GREEN. Pins
the four observable outcomes the plan names:

* **keep** -- a passing candidate branch fast-forward-merges onto default and
  the candidate branch is gone afterward.
* **discard** -- a failing candidate branch is deleted; default is untouched.
* **source-gate path** -- ``runner._keep`` is reachable from
  ``source_gate.gate_source_candidate`` (a *source*-kind candidate), not just
  from the plain prompt-candidate path inside ``_process_candidate`` itself.
  This is the exact reachability the SYSTEMS_PLAN Risk Assessment flags.
* **no-op** -- no pending candidate branch is a defensive no-op that neither
  merges nor deletes anything.

Derived from a direct read of ``core/loop.py`` and ``core/source_gate.py``,
not from any planned extraction shape. Zero network: every scenario injects a
deterministic ``evaluate`` stub over a real cloned worktree, mirroring
``tests/test_loop_runner.py`` and ``tests/test_source_gate.py``'s own
conventions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
CANDIDATE = "loop/c/wound"


def _fake_evaluate(scalar: float):
    """Deterministic, network-free evaluate stub over a real cloned worktree."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-candidate-gate-char-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-22T00:00:00Z",
            generation=1,
            harness_version="fake-candidate-gate-char",
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


def _open_candidate(vault: Path, body: str) -> None:
    """Land a plain (non-source) candidate branch -- the ``_process_candidate`` path."""
    vcs = VaultVcs(vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)
    if vcs.branch_exists(CANDIDATE):
        vcs.delete_branch(CANDIDATE, force=True)
    vcs.create_branch(CANDIDATE, default)
    vcs.checkout_branch(CANDIDATE)
    wound = vault / ".knotica" / "prompts" / "query.md"
    wound.parent.mkdir(parents=True, exist_ok=True)
    wound.write_text(body, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: wound query.md")
    vcs.checkout_branch(default)


def test_a_passing_candidate_is_kept_and_the_candidate_branch_disappears(
    template_vault: Path,
) -> None:
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.60),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.50, harness_version="fake-candidate-gate-char")
    _open_candidate(template_vault, "# healed query\n")

    result = runner.poll_once()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE), (
        "a kept candidate branch must no longer exist once poll_once merges it"
    )


def test_a_failing_candidate_is_discarded_without_merging_its_content(
    template_vault: Path,
) -> None:
    vcs = VaultVcs(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.30),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.50, harness_version="fake-candidate-gate-char")
    _open_candidate(template_vault, "# wounded query\n")

    result = runner.poll_once()

    assert result.acted is True
    assert result.decision is LoopDecision.fail
    assert not vcs.branch_exists(CANDIDATE), "a discarded candidate branch must be deleted"
    query = template_vault / ".knotica" / "prompts" / "query.md"
    assert not (
        query.is_file() and query.read_text(encoding="utf-8").strip() == "# wounded query"
    ), "a discarded candidate's content must never land on the default branch"


def test_poll_once_is_a_no_op_when_no_candidate_branch_is_pending(
    template_vault: Path,
) -> None:
    vcs = VaultVcs(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.60),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.50, harness_version="fake-candidate-gate-char")
    vcs.checkout_branch(vcs.default_branch())
    default_sha_before = vcs.head_sha()

    result = runner.poll_once()

    assert result.acted is False
    vcs.checkout_branch(vcs.default_branch())
    assert vcs.head_sha() == default_sha_before, (
        "a no-pending-candidate cycle must never touch the default branch"
    )


def test_source_gate_reaches_runner_keep_through_poll_once(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source-candidate path (``source_gate.gate_source_candidate``) calls
    back into ``runner._keep`` rather than duplicating the merge logic -- this
    is the exact reachability the SYSTEMS_PLAN Risk Assessment flags as a
    Low-likelihood/High-impact risk for the coming extraction: a delegator
    stub for ``_keep`` alone (without ``_next_candidate``/``_process_candidate``
    also being reachable) would silently break this call path."""
    from knotica.core import gapfill, source_ingest
    from knotica.core.records import GapEvidence, GapRecord, parse_suggestions_jsonl
    from knotica.core.transaction import VaultTransaction
    from knotica.discovery.records import SourceCandidate

    store = LocalFSStore(template_vault)

    def _stub_headless_trainset_grower() -> None:
        monkeypatch.setattr("knotica.evals.llm.AnthropicClient", lambda: object())
        monkeypatch.setattr(
            "knotica.evals.train_bootstrap.bootstrap_trainset", lambda *_a, **_k: {}
        )

    _stub_headless_trainset_grower()

    evidence = GapEvidence(
        quality_delta=-0.5,
        qa_accuracy_delta=-0.5,
        citation_validity_delta=0.0,
        retrieval_trace=(),
        pages_added=(),
        pages_removed=(),
        prior_generation=4,
    )
    gap = GapRecord(
        gap_id="gap-candidate-gate-char",
        topic=TOPIC,
        qa_id="golden-candidate-gate-char",
        fault_class="genuine_gap",
        status="open",
        classifier_version=1,
        detected_generation=5,
        detected_at="2026-07-22T00:00:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question="What is the retrieval story for candidate-gate-char?",
        reference_pages=("agent-workflow-memory",),
        reference_pages_exist=False,
        evidence=evidence,
        manifest_ref="agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    )
    candidate_source = SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )
    records = gapfill.build_suggestion_records(
        gap, [candidate_source], proposer_version=1, clock=lambda: "2026-07-22T00:00:00Z"
    )
    suggestions_path = gapfill.suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(
        store, template_vault, "test_seed", TOPIC, "seed suggestions for test"
    ) as txn:
        txn.write(suggestions_path, body)
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")

    handle = source_ingest.open_ingest(store, template_vault, TOPIC, suggestion_id)
    vcs = VaultVcs(template_vault)
    worktree_entry = next(
        entry for entry in vcs.list_worktrees() if entry.get("branch") == handle.candidate
    )
    worktree_path = Path(worktree_entry["path"])
    page = worktree_path / TOPIC / "agent-workflow-memory.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Agent Workflow Memory\n\ningested body\n", encoding="utf-8")
    VaultVcs(worktree_path).commit_paths(
        [f"{TOPIC}/agent-workflow-memory.md"],
        f"knotica(write_page): {TOPIC} — ingest agent-workflow-memory",
    )
    published_branch = source_ingest.publish_ingest(handle)

    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.95),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=lambda *_a, **_k: 0.0,
    )
    runner.set_baseline(0.80, harness_version="fake-candidate-gate-char")

    result = runner.poll_once()

    assert result.acted is True
    live_candidates = {branch for branch, _ in vcs.list_branch_tips("loop/c/")}
    assert published_branch not in live_candidates, (
        "the source-gate merge must remove the source candidate branch, proving "
        "runner._keep was actually invoked through source_gate.gate_source_candidate"
    )
    records_after = parse_suggestions_jsonl(store.read_text(suggestions_path))
    record_after = next(r for r in records_after if r.suggestion_id == suggestion_id)
    assert record_after.status == "ingested"
    assert record_after.gate_outcome is not None
    assert record_after.gate_outcome["verdict"] == "merged"
