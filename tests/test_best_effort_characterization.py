"""Characterization safety net for the loop's five hand-written "best-effort,
never block the load-bearing action" isolation boundaries (P-A consolidation,
pre-extraction baseline).

Six call sites across ``core/loop.py`` and ``core/source_gate.py`` each swallow
a failure by hand today, with no shared primitive:

* ``_maybe_redirect_to_gaps`` (``loop.py:504-516``) -- a gap-classification
  failure is isolated AND recorded (a distinct loop-state commit names the
  failure) before falling through to the unchanged arena heal.
* ``_maybe_discover_for_gaps`` (``loop.py:610-624``) -- a discovery-drain
  failure is isolated SILENTLY (no commit names it at all) -- the loop-side
  drain is best-effort bookkeeping, never surfaced.
* ``_prune_result_branches`` / ``_prune_quarantine_branches``
  (``loop.py:1012-1031``, ``source_gate.py:522-536``) -- housekeeping failures
  are silently swallowed; the cycle that triggered pruning still completes.
* ``_grow_trainset_from_merge`` (``source_gate.py:247-294``) -- a post-merge
  trainset-grower failure (both its ``KnoticaError`` and generic ``Exception``
  branches) never undoes the merge or the suggestion's ``ingested`` status.
* ``_commit_quarantine_diff`` (``source_gate.py:444-495``) -- the artifact
  WRITE is best-effort (bare ``except: pass``), but the checkout RESTORE in
  its ``finally`` is deliberately un-swallowed (a different existing test,
  ``test_source_gate.py``'s failed-checkout-restore test, already pins that
  restore-failure half; this file pins the write-failure half instead).

This file pins each site's exact exception isolation and fallback -- so the
coming shared ``best_effort`` primitive (P-A Step 6) can be verified by
re-running this file unmodified and seeing it stay GREEN.

Derived from a direct read of both modules, not from any planned primitive
shape. Zero network throughout.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from knotica.core import gapfill, source_ingest
from knotica.core.arena import ArenaStage, ArenaState
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gap_classifier import gaps_path
from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.loop_state import read_loop_state
from knotica.core.records import MetricsComponents, MetricsRecord, parse_suggestions_jsonl
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import git_commit_subjects, run_git

TOPIC = "agentic-systems"


# ---------------------------------------------------------------------------
# Shared fixture builders (mirror tests/test_loop_runner.py, tests/
# test_loop_gapfill_hook.py, tests/test_source_gate.py's own conventions)
# ---------------------------------------------------------------------------


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "observed-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def _freeze_n_gaps(vault: Path, n: int) -> list[str]:
    from knotica.evals.golden import GoldenSetFloorWarning, freeze, load

    store = LocalFSStore(vault)
    entries = [
        {
            "question": f"Does the vault cover made-up-topic-{i}?",
            "reference_answer": "No, that concept is absent from this vault.",
            "pages_used": [f"nonexistent-page-{i}"],
        }
        for i in range(n)
    ]
    with pytest.warns(GoldenSetFloorWarning):
        freeze(store, vault, TOPIC, entries)
    by_query = {record.query: record.id for record in load(store, TOPIC)}
    return [by_query[entry["question"]] for entry in entries]


def _per_id_delta(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "quality_delta": -0.3,
        "qa_accuracy_delta": -0.3,
        "citation_validity_delta": 0.0,
        "pages_added": [],
        "pages_removed": [],
    }
    payload.update(overrides)
    return payload


def _manifest_with_deltas(*, generation: int, per_id: dict[str, dict]) -> dict:
    return {
        "manifest_schema_version": 2,
        "generation": generation,
        "per_example": [{"id": qa_id, "pages": []} for qa_id in per_id],
        "held_out_delta": {
            "ids_added": [],
            "ids_removed": [],
            "prior_generation": generation - 1,
            "scalar_delta": -0.1,
            "per_id": per_id,
        },
    }


def _regression_fake_evaluate(scalar: float, *, generation: int, manifest: dict):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-best-effort-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        manifest_dir = clone.root / topic / ".knotica" / "eval-runs" / f"gen-{generation}"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: write gen-{generation} manifest")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-21T00:00:00Z",
            generation=generation,
            harness_version="fake-best-effort",
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


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-best-effort-plain-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-21T00:00:00Z",
            generation=1,
            harness_version="fake-best-effort",
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


def _arena_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float]]:
    calls: list[tuple[str, float]] = []

    def _fake_race_variants(store, root, topic, variants, **kwargs):
        calls.append((topic, kwargs["baseline_scalar"]))
        return ArenaState(topic=topic, stage=ArenaStage.reverted, winner_id=None, message="spy")

    monkeypatch.setattr("knotica.core.arena_resolve.race_variants", _fake_race_variants)
    return calls


# ---------------------------------------------------------------------------
# Group A: gap-classification isolation -- swallowed AND recorded, arena heal
# still runs unchanged.
# ---------------------------------------------------------------------------


def test_gap_classification_failure_is_isolated_and_recorded_then_falls_through_to_arena(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    qa_id = _freeze_n_gaps(template_vault, 1)[0]
    manifest = _manifest_with_deltas(generation=2, per_id={qa_id: _per_id_delta()})
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest),
        arena_enabled=True,
        arena_score=lambda *_a, **_k: 0.0,
    )
    runner.set_baseline(0.90, harness_version="fake-best-effort")
    arena_calls = _arena_spy(monkeypatch)
    monkeypatch.setattr(
        "knotica.core.gap_classifier.classify_regression",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("classifier exploded")),
    )
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True, "a classifier exception must never crash the observe cycle"
    assert len(arena_calls) == 1, "the arena must still fire when classification raises"
    assert not store.exists(gaps_path(TOPIC)), "no gap record can be trusted from a failed run"
    subjects = git_commit_subjects(template_vault)
    assert any("gap classification failed" in subject for subject in subjects), (
        "unlike the discovery-drain isolation (Group B below), a classifier failure "
        "must leave a named trace in loop-state history before the arena heal proceeds"
    )


# ---------------------------------------------------------------------------
# Group B: discovery-drain isolation -- swallowed SILENTLY, no trace at all.
# ---------------------------------------------------------------------------


def test_discovery_drain_failure_is_isolated_silently_with_no_recorded_trace(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knotica.core import gapfill as gapfill_mod

    store = LocalFSStore(template_vault)

    class _RaisingDiscoveryService:
        def discover(self, query):
            raise RuntimeError("discovery unreachable")

    monkeypatch.setattr(
        gapfill_mod, "build_default_discovery_service", lambda **_kwargs: _RaisingDiscoveryService()
    )
    qa_id = _freeze_n_gaps(template_vault, 1)[0]
    manifest = _manifest_with_deltas(generation=2, per_id={qa_id: _per_id_delta()})
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest),
        discover_on_regression=True,
        max_gaps=5,
    )
    runner.set_baseline(0.90, harness_version="fake-best-effort")
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True, "a raising drain must never crash or block the observe cycle"
    assert store.exists(gaps_path(TOPIC)), "gap records persisted before the drain still survive"
    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.last_error is None, (
        "unlike the classifier isolation (Group A above), a discovery-drain failure "
        "must leave NO trace on loop-state -- it is silent, best-effort bookkeeping"
    )


# ---------------------------------------------------------------------------
# Group C: result-branch pruning isolation -- housekeeping failure never
# blocks the merge that triggered it.
# ---------------------------------------------------------------------------


def test_result_branch_pruning_failure_never_blocks_a_successful_keep(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = "loop/c/wound"
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)
    vcs.create_branch(candidate, default)
    vcs.checkout_branch(candidate)
    wound = template_vault / ".knotica" / "prompts" / "query.md"
    wound.parent.mkdir(parents=True, exist_ok=True)
    wound.write_text("# healed query\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: healed query")
    vcs.checkout_branch(default)

    def _raising_commit_timestamp(self: VaultVcs, sha: str) -> float:
        raise RuntimeError("git log exploded")

    monkeypatch.setattr(VaultVcs, "commit_timestamp", _raising_commit_timestamp)
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.60), branch_prefix="loop/c/"
    )
    runner.set_baseline(0.50, harness_version="fake-best-effort")

    result = runner.poll_once()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_
    assert not vcs.branch_exists(candidate), (
        "a pruning failure must never prevent the candidate from being consumed"
    )


# ---------------------------------------------------------------------------
# Group D: post-merge trainset-grower isolation -- both its KnoticaError and
# generic Exception branches never undo the merge or the ingested status.
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
        detected_at="2026-07-21T00:00:00Z",
        scalar_at_detection=0.9493,
        baseline_scalar=0.96,
        question=f"What is the retrieval augmentation story for {qa_id}?",
        reference_pages=("agent-workflow-memory",),
        reference_pages_exist=False,
        evidence=evidence,
        manifest_ref="agentic-systems/.knotica/eval-runs/gen-5/manifest.json",
    )


def _approved_suggestion(template_vault: Path, store, *, qa_id: str, gap_id: str) -> str:
    from knotica.core.transaction import VaultTransaction
    from knotica.discovery.records import SourceCandidate

    gap = _gap_record(gap_id=gap_id, qa_id=qa_id)
    candidate = SourceCandidate(
        url="https://arxiv.org/abs/2409.07429",
        title="Agent Workflow Memory",
        snippet="We propose inducing reusable workflows from past experience...",
        source_provider="fake",
        doi="10.48550/arXiv.2409.07429",
        citation_count=12,
    )
    records = gapfill.build_suggestion_records(
        gap, [candidate], proposer_version=1, clock=lambda: "2026-07-21T00:00:00Z"
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


def _open_and_publish_source_candidate(template_vault: Path, store, suggestion_id: str) -> str:
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
    return source_ingest.publish_ingest(handle)


def _source_pass_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-best-effort-source-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-21T00:00:00Z",
            generation=1,
            harness_version="fake-best-effort-source",
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


def test_trainset_grower_knotica_error_is_isolated_and_never_undoes_the_merge(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault,
        store,
        qa_id="golden-grower-knotica-error",
        gap_id="gap-grower-knotica-error",
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)

    def _raising_anthropic_client():
        raise KnoticaError(ErrorCode.NOT_CONFIGURED, "no Anthropic credentials configured")

    monkeypatch.setattr("knotica.evals.llm.AnthropicClient", _raising_anthropic_client)
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_source_pass_evaluate(0.95), branch_prefix="loop/c/"
    )
    runner.set_baseline(0.80, harness_version="fake-best-effort-source")

    result = runner.poll_once()

    assert result.acted is True
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    record = next(r for r in records if r.suggestion_id == suggestion_id)
    assert record.status == "ingested", (
        "a missing-credentials KnoticaError from the grower must never revert the merge"
    )
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "merged"


# ---------------------------------------------------------------------------
# Group E: quarantine-diff-artifact WRITE isolation -- the write is
# best-effort; the refusal completes even when it fails (contrast with the
# checkout-RESTORE half, which is NOT swallowed and already has its own test
# in tests/test_source_gate.py).
# ---------------------------------------------------------------------------


def test_quarantine_diff_write_failure_is_isolated_and_the_refusal_still_completes(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-diff-write-fail", gap_id="gap-diff-write-fail"
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    per_id = {f"golden-dilute-{index:02d}": _per_id_delta() for index in range(3)}

    class _RaisingVaultTransaction:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated quarantine-diff write failure")

    monkeypatch.setattr("knotica.core.source_gate.VaultTransaction", _RaisingVaultTransaction)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_regression_fake_evaluate(
            0.40, generation=1, manifest=_manifest_with_deltas(generation=1, per_id=per_id)
        ),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.80, harness_version="fake-best-effort")

    result = runner.poll_once()

    assert result.acted is True, "a quarantine-diff write failure must never crash the gate cycle"
    vcs = VaultVcs(template_vault)
    quarantine_branch = f"loop/x/{TOPIC}/source-{suggestion_id[:8]}"
    assert vcs.branch_exists(quarantine_branch), (
        "the rename to loop/x/* is unaffected by the diff-artifact write failing"
    )
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    record = next(r for r in records if r.suggestion_id == suggestion_id)
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "refused"
    assert record.gate_outcome.get("regressed_questions"), (
        "the refusal's own gate_outcome fields are computed independent of the "
        "diff-artifact write, so they survive its failure"
    )
    diff_path = f"{TOPIC}/.knotica/quarantine/source-{suggestion_id[:8]}.json"
    assert vcs.read_file_at(quarantine_branch, diff_path) is None, (
        "the diff artifact itself must be ABSENT -- the write genuinely failed and was "
        "swallowed, not silently retried or written elsewhere"
    )
    assert vcs.current_branch() == vcs.default_branch(), (
        "the checkout must still be restored to default despite the write failure"
    )


# ---------------------------------------------------------------------------
# Group F: quarantine-branch pruning isolation -- mirrors Group C for the
# source-refusal path.
# ---------------------------------------------------------------------------


def test_quarantine_branch_pruning_failure_never_blocks_a_completed_refusal(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault,
        store,
        qa_id="golden-quarantine-prune-fail",
        gap_id="gap-quarantine-prune-fail",
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    per_id = {f"golden-dilute-{index:02d}": _per_id_delta() for index in range(3)}

    def _raising_commit_timestamp(self: VaultVcs, sha: str) -> float:
        raise RuntimeError("git log exploded")

    monkeypatch.setattr(VaultVcs, "commit_timestamp", _raising_commit_timestamp)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_regression_fake_evaluate(
            0.40, generation=1, manifest=_manifest_with_deltas(generation=1, per_id=per_id)
        ),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.80, harness_version="fake-best-effort")

    result = runner.poll_once()

    assert result.acted is True, "a quarantine-prune failure must never crash the gate cycle"
    vcs = VaultVcs(template_vault)
    quarantine_branch = f"loop/x/{TOPIC}/source-{suggestion_id[:8]}"
    assert vcs.branch_exists(quarantine_branch)
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    record = next(r for r in records if r.suggestion_id == suggestion_id)
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "refused"
