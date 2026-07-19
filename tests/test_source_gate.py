"""Behavioral contract tests for ``knotica.core.source_gate`` -- the
candidate-kind classifier and pass/refuse dispatch that lets the loop's
existing clone-eval-gate cycle handle a *source* candidate (an ingested
gap-fill suggestion) differently from an ordinary prompt candidate.

The contract under test:

* A candidate branch is classified as a source candidate purely from its
  name (``loop/c/<topic>/source-<id8>``) -- never from any persisted state.
  Every other branch shape under the same ``loop/c/`` prefix is *not* a
  source candidate.
* A source candidate that holds or raises the gate's scalar is merged onto
  the default branch; the suggestion that drove it flips from approved to
  ingested automatically, and its record gains a "merged" gate outcome
  pointing at the merge's audit ref.
* A source candidate that regresses the scalar is never raced through the
  arena (the arena heals prompt-recoverable regressions; a diluted or
  displaced source is not prompt-fixable). It is quarantined -- renamed
  away, never deleted, so the diff and the reasoning survive -- and the
  suggestion gains a "refused" gate outcome with a bounded per-question
  diff. The suggestion's own status is left alone, so a human decides what
  happens to the source next.
* Quarantined branches accumulate across refusals; only the newest few per
  topic are kept, mirroring the loop's existing merged-result pruning.

RED-first: ``knotica.core.source_gate`` does not exist yet when this file is
written (the paired implementer step lands the module concurrently) -- the
module is resolved lazily inside a helper so collection succeeds and the
first run fails with ``ModuleNotFoundError``, not a collection error. This
file was written without reading the implementer's code; the diagnostic
manifest shape used below mirrors ``gap_classifier.py``'s already-shipped
v2 ``held_out_delta``/``per_example`` schema -- the same substrate the plan
text points at for the per-question refusal diff -- not a guess.

Every scenario drives the change through the loop's public
``LoopRunner.poll_once`` surface end to end (not the private ``source_gate``
dispatch functions directly), except for the two branch-naming-convention
tests, which exercise ``classify_candidate``/``suggestion_id_from_branch``
directly since parsing a branch name is itself the behavior under test.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from knotica.core import gapfill, source_ingest
from knotica.core.arena import ArenaStage, ArenaState
from knotica.core.loop import LoopRunner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord, parse_suggestions_jsonl
from knotica.core.vcs import GitError, VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"


def _source_gate_module():
    import knotica.core.source_gate

    return knotica.core.source_gate


# ---------------------------------------------------------------------------
# Fixture builders -- a real approved suggestion, driven through the real
# gapfill decision state machine (never hand-forged into a status the
# machine itself would refuse to reach), and a real source-candidate branch
# built through the already-shipped source_ingest session lifecycle.
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


def _seed_suggestions(store, root: Path, records) -> None:
    from knotica.core.transaction import VaultTransaction

    path = gapfill.suggestions_path(TOPIC)
    body = "\n".join(record.to_json_line() for record in records) + "\n"
    with VaultTransaction(
        store, Path(root), "test_seed", TOPIC, "seed suggestions for test"
    ) as txn:
        txn.write(path, body)


def _approved_suggestion(template_vault: Path, store, *, qa_id: str, gap_id: str) -> str:
    """Build one suggestion and approve it via the real decision state machine."""
    gap = _gap_record(gap_id=gap_id, qa_id=qa_id)
    records = gapfill.build_suggestion_records(
        gap, [_candidate_source()], proposer_version=1, clock=lambda: "2026-07-19T00:00:00Z"
    )
    _seed_suggestions(store, template_vault, records)
    suggestion_id = records[0].suggestion_id
    gapfill.apply_decision(store, template_vault, TOPIC, suggestion_id, decision="approve")
    return suggestion_id


def _open_and_publish_source_candidate(template_vault: Path, store, suggestion_id: str) -> str:
    """Open a real ingest session, commit one page, and publish it onto ``loop/c/*``."""
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


def _stub_headless_trainset_grower(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the real Anthropic-backed post-merge trainset grower.

    A source-pass cycle now also fires the post-merge grower, which
    constructs a real credentialed client and could otherwise make a live
    network call during a test. Any test that drives a source-pass cycle but
    is not itself testing the grower's behavior should call this so the
    cycle completes deterministically and network-free.
    """
    monkeypatch.setattr("knotica.evals.llm.AnthropicClient", lambda: object())
    monkeypatch.setattr("knotica.evals.train_bootstrap.bootstrap_trainset", lambda *_a, **_k: {})


def _suggestion_record(store, suggestion_id: str):
    records = parse_suggestions_jsonl(store.read_text(gapfill.suggestions_path(TOPIC)))
    return next(record for record in records if record.suggestion_id == suggestion_id)


# ---------------------------------------------------------------------------
# Eval fakes -- zero network, mirrors tests/test_loop_runner.py's harness
# stub conventions (``wrap_harness_result`` over a real cloned worktree).
# ---------------------------------------------------------------------------


def _fake_evaluate(scalar: float):
    """A plain pass/fail stub -- no diagnostic manifest, for the merge scenarios."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-source-gate-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=1,
            harness_version="fake-source-gate",
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


def _per_id_delta() -> dict[str, object]:
    return {
        "quality_delta": -0.3,
        "qa_accuracy_delta": -0.3,
        "citation_validity_delta": 0.0,
        "pages_added": [],
        "pages_removed": [],
    }


def _evaluate_with_manifest(scalar: float, *, generation: int, per_id: dict[str, dict]):
    """A regressing stub that also writes a v2 diagnostic manifest onto the clone.

    Mirrors the same ``held_out_delta``/``per_example`` schema
    ``gap_classifier.py`` already reads for the default-branch regression
    path -- the plan text points at this exact substrate for the
    per-question refusal diff.
    """

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-source-gate-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        manifest = {
            "manifest_schema_version": 2,
            "generation": generation,
            "per_example": [{"id": qa_id, "pages": []} for qa_id in per_id],
            "held_out_delta": {
                "ids_added": [],
                "ids_removed": [],
                "prior_generation": generation - 1,
                "scalar_delta": scalar - 0.8,
                "per_id": per_id,
            },
        }
        manifest_dir = clone.root / topic / ".knotica" / "eval-runs" / f"gen-{generation}"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: write gen-{generation} manifest")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-19T00:00:00Z",
            generation=generation,
            harness_version="fake-source-gate",
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
    """Replace the arena's entry point with a recording stub -- proves whether it fired."""
    calls: list[tuple[str, float]] = []

    def _fake_race_variants(
        store,
        root,
        topic,
        variants,
        *,
        baseline_scalar,
        score,
        candidate_branch,
        promote_on_win,
    ):
        calls.append((topic, baseline_scalar))
        return ArenaState(topic=topic, stage=ArenaStage.reverted, winner_id=None, message="spy")

    monkeypatch.setattr("knotica.core.loop.race_variants", _fake_race_variants)
    return calls


# ---------------------------------------------------------------------------
# classify_candidate / suggestion_id_from_branch -- kind is a pure function
# of the branch name, never persisted state.
# ---------------------------------------------------------------------------


def test_classify_candidate_recognizes_the_source_branch_naming_convention() -> None:
    mod = _source_gate_module()

    assert mod.classify_candidate(f"loop/c/{TOPIC}/source-1a2b3c4d") == "source"


@pytest.mark.parametrize(
    "branch",
    [
        "loop/c/1a2b3c4d5e6f",
        "loop/c/wound",
        f"loop/c/{TOPIC}/prompt-1a2b3c4d",
        f"loop/wip/{TOPIC}/source-1a2b3c4d",
    ],
)
def test_classify_candidate_never_reports_a_non_source_branch_as_source(branch: str) -> None:
    mod = _source_gate_module()

    assert mod.classify_candidate(branch) != "source", (
        f"{branch!r} carries no source- infix and must never classify as a source candidate"
    )


def test_suggestion_id_from_branch_recovers_the_id8_prefix() -> None:
    mod = _source_gate_module()
    branch = f"loop/c/{TOPIC}/source-1a2b3c4d"

    assert mod.suggestion_id_from_branch(branch) == "1a2b3c4d"


# ---------------------------------------------------------------------------
# Source-pass: merge onto default, auto-transition the suggestion, stamp a
# merged gate outcome -- never racing the arena.
# ---------------------------------------------------------------------------


def test_a_passing_source_candidate_merges_and_auto_transitions_the_suggestion_to_ingested(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-pass", gap_id="gap-pass"
    )
    published_branch = _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    arena_calls = _arena_spy(monkeypatch)
    _stub_headless_trainset_grower(monkeypatch)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.95),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=lambda *_a, **_k: 0.0,
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")

    result = runner.poll_once()

    assert result.acted is True
    vcs = VaultVcs(template_vault)
    live_candidates = {branch for branch, _ in vcs.list_branch_tips("loop/c/")}
    assert published_branch not in live_candidates, (
        "a merged source candidate must no longer show up in a loop/c/ scan"
    )
    record = _suggestion_record(store, suggestion_id)
    assert record.status == "ingested"
    assert record.ingested_at is not None
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "merged"
    assert record.gate_outcome["scalar"] == pytest.approx(0.95)
    assert record.gate_outcome["baseline_scalar"] == pytest.approx(0.80)
    ref = record.gate_outcome["ref"]
    assert isinstance(ref, str) and ref.startswith("loop/r/")
    assert vcs.branch_exists(ref), "the merge's audit ref must survive as a real branch"
    assert arena_calls == [], "a passing source candidate must never race the arena"


# ---------------------------------------------------------------------------
# Post-merge trainset grower: scoped to exactly what the merge changed, and
# never load-bearing for the merge or the suggestion's ingested status.
# ---------------------------------------------------------------------------


def test_a_passing_source_candidate_grows_the_trainset_for_exactly_the_pages_the_merge_changed(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right after a source candidate merges, the loop grows the trainset --
    but only for the entity pages this specific merge actually changed, never
    the whole topic's page set."""
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-grower", gap_id="gap-grower"
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    monkeypatch.setattr("knotica.evals.llm.AnthropicClient", lambda: object())
    grower_calls: list[dict[str, object]] = []

    def _fake_bootstrap_trainset(store_arg, root_arg, topic_arg, llm_client, snapshot, **kwargs):
        grower_calls.append({"topic": topic_arg, "pages": kwargs.get("pages")})
        return {}

    monkeypatch.setattr(
        "knotica.evals.train_bootstrap.bootstrap_trainset", _fake_bootstrap_trainset
    )
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.95),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")

    result = runner.poll_once()

    assert result.acted is True
    assert len(grower_calls) == 1, "the grower must run exactly once for a passing merge"
    call = grower_calls[0]
    assert call["topic"] == TOPIC
    assert call["pages"] == [f"{TOPIC}/agent-workflow-memory.md"], (
        "the grower must be scoped to exactly the pages this merge changed, not the whole "
        f"topic's page set, got {call['pages']!r}"
    )


def test_a_failing_trainset_grower_never_undoes_the_merge_or_the_suggestion_ingestion(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trainset-grower failure after a passing merge must never undo the
    merge, revert the suggestion's ingested status, or leave the live vault
    stranded off the default branch -- the merge has already committed by
    the time the grower runs, and grower trouble is the loop's own
    bookkeeping problem, not the source's."""
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-grower-fail", gap_id="gap-grower-fail"
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    monkeypatch.setattr("knotica.evals.llm.AnthropicClient", lambda: object())

    def _raising_bootstrap_trainset(*_args, **_kwargs):
        raise RuntimeError("simulated trainset grower failure")

    monkeypatch.setattr(
        "knotica.evals.train_bootstrap.bootstrap_trainset", _raising_bootstrap_trainset
    )
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.95),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")

    result = runner.poll_once()

    assert result.acted is True
    record = _suggestion_record(store, suggestion_id)
    assert record.status == "ingested", "a grower failure must never revert the ingested status"
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "merged"
    vcs = VaultVcs(template_vault)
    assert vcs.current_branch() == vcs.default_branch(), (
        "a grower failure must leave the live checkout on the default branch"
    )
    merged_page = (template_vault / TOPIC / "agent-workflow-memory.md").read_text(encoding="utf-8")
    assert "ingested body" in merged_page, (
        "the source candidate's merged content must still be present on the default branch"
    )


# ---------------------------------------------------------------------------
# Source-refuse: quarantine (rename, never delete), a bounded diff, an
# unmodified suggestion status, and -- the load-bearing guard -- no arena.
# ---------------------------------------------------------------------------


def test_a_regressing_source_candidate_is_quarantined_not_deleted(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-refuse", gap_id="gap-refuse"
    )
    published_branch = _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    _arena_spy(monkeypatch)
    per_id = {f"golden-dilute-{index:02d}": _per_id_delta() for index in range(12)}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_evaluate_with_manifest(0.40, generation=1, per_id=per_id),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=lambda *_a, **_k: 0.0,
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")

    result = runner.poll_once()

    assert result.acted is True
    vcs = VaultVcs(template_vault)
    quarantine_branch = f"loop/x/{TOPIC}/source-{suggestion_id[:8]}"
    assert vcs.branch_exists(quarantine_branch), (
        "a refused source candidate must be renamed onto loop/x/*, not deleted"
    )
    live_candidates = {branch for branch, _ in vcs.list_branch_tips("loop/c/")}
    assert published_branch not in live_candidates, (
        "the quarantined branch must be invisible to a subsequent loop/c/ scan"
    )
    assert quarantine_branch not in live_candidates


def test_a_regressing_source_candidate_records_a_bounded_refusal_diff_and_leaves_status_untouched(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-refuse2", gap_id="gap-refuse2"
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    arena_calls = _arena_spy(monkeypatch)
    per_id = {f"golden-dilute-{index:02d}": _per_id_delta() for index in range(12)}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_evaluate_with_manifest(0.40, generation=1, per_id=per_id),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=lambda *_a, **_k: 0.0,
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")

    result = runner.poll_once()

    assert result.acted is True
    record = _suggestion_record(store, suggestion_id)
    assert record.status == "approved", (
        "a refusal must leave the driving suggestion's status untouched -- a human decides next"
    )
    assert record.gate_outcome is not None
    assert record.gate_outcome["verdict"] == "refused"
    assert record.gate_outcome["scalar"] == pytest.approx(0.40)
    assert record.gate_outcome["baseline_scalar"] == pytest.approx(0.80)
    ref = record.gate_outcome["ref"]
    assert isinstance(ref, str) and ref.startswith("loop/x/")
    regressed = record.gate_outcome.get("regressed_questions")
    assert isinstance(regressed, list)
    assert 0 < len(regressed) <= 10, (
        f"the refusal diff must be bounded to at most 10 questions, got {len(regressed)}"
    )
    assert arena_calls == [], (
        "a regressing source candidate must never be raced through the arena -- the "
        "reward-hacking guard this phase exists to enforce"
    )


def test_a_failed_checkout_restore_after_quarantine_never_lets_the_suggestion_commit_land_on_the_quarantine_branch(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the live checkout cannot be switched back to the default branch after
    a source candidate is renamed onto the quarantine branch, the cycle must
    fail loudly rather than silently continuing -- because the very next step
    would otherwise commit the suggestion's gate-outcome record onto whichever
    branch happens to be checked out. Neither branch's committed history may
    ever end up carrying that stamped record: not the quarantine branch (it
    would misroute there), and not the default branch (the commit that would
    stamp it there never got a chance to run)."""
    store = LocalFSStore(template_vault)
    suggestion_id = _approved_suggestion(
        template_vault, store, qa_id="golden-restore-fail", gap_id="gap-restore-fail"
    )
    _open_and_publish_source_candidate(template_vault, store, suggestion_id)
    per_id = {f"golden-dilute-{index:02d}": _per_id_delta() for index in range(3)}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_evaluate_with_manifest(0.40, generation=1, per_id=per_id),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    real_checkout_branch = VaultVcs.checkout_branch

    def _fail_only_the_restore_to_default(self: VaultVcs, name: str) -> None:
        if name == default:
            raise GitError(f"simulated failure switching the live checkout back to {name!r}")
        real_checkout_branch(self, name)

    monkeypatch.setattr(VaultVcs, "checkout_branch", _fail_only_the_restore_to_default)

    with pytest.raises(GitError):
        runner.poll_once()

    quarantine_branch = f"loop/x/{TOPIC}/source-{suggestion_id[:8]}"
    assert vcs.branch_exists(quarantine_branch), (
        "the rename to loop/x/* happens before the restore step, so it must still have occurred"
    )
    suggestions_relpath = gapfill.suggestions_path(TOPIC)
    quarantine_record = next(
        record
        for record in parse_suggestions_jsonl(
            vcs.read_file_at(quarantine_branch, suggestions_relpath) or ""
        )
        if record.suggestion_id == suggestion_id
    )
    assert quarantine_record.gate_outcome is None, (
        "the suggestion's gate-outcome commit must never land on the quarantine branch, even "
        "when restoring the checkout back to default afterward fails"
    )
    default_record = next(
        record
        for record in parse_suggestions_jsonl(vcs.read_file_at(default, suggestions_relpath) or "")
        if record.suggestion_id == suggestion_id
    )
    assert default_record.status == "approved"
    assert default_record.gate_outcome is None, (
        "the default branch's committed history must carry no gate-outcome stamp either -- "
        "the commit that would have written it never ran"
    )


def test_arena_still_races_for_a_regressing_ordinary_candidate_under_the_same_runner_config(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity control: the exact runner configuration that lets a regressing
    SOURCE candidate skip the arena must still race the arena for an ordinary
    (non-source) candidate branch -- proving the exclusion is source-kind
    specific, not merely "the arena never fires in this fixture"."""
    arena_calls = _arena_spy(monkeypatch)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.10),
        branch_prefix="loop/c/",
        arena_enabled=True,
        arena_score=lambda *_a, **_k: 0.0,
    )
    runner.set_baseline(0.80, harness_version="fake-source-gate")
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)
    plain_candidate = "loop/c/plain-prompt-candidate"
    vcs.create_branch(plain_candidate, default)
    vcs.checkout_branch(plain_candidate)
    marker = template_vault / TOPIC / "prompt-candidate-marker.md"
    marker.write_text("# prompt candidate\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: prompt candidate marker")
    vcs.checkout_branch(default)

    result = runner.poll_once()

    assert result.acted is True
    assert len(arena_calls) == 1, "a regressing non-source candidate must still race the arena"


# ---------------------------------------------------------------------------
# Quarantine pruning -- only the newest few per topic are kept, mirroring
# the loop's existing merged-result-branch pruning.
# ---------------------------------------------------------------------------


def test_quarantine_branches_beyond_the_newest_five_per_topic_are_pruned(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    _arena_spy(monkeypatch)
    oldest_quarantine_branch = ""
    for index in range(6):
        suggestion_id = _approved_suggestion(
            template_vault, store, qa_id=f"golden-prune-{index}", gap_id=f"gap-prune-{index}"
        )
        # Distinct, strictly increasing commit dates per iteration so the
        # pruning order (newest-first) is deterministic rather than an
        # accident of same-second git timestamps.
        commit_date = f"2026-01-01T00:{index:02d}:00+00:00"
        monkeypatch.setenv("GIT_COMMITTER_DATE", commit_date)
        monkeypatch.setenv("GIT_AUTHOR_DATE", commit_date)
        _open_and_publish_source_candidate(template_vault, store, suggestion_id)
        runner = LoopRunner(
            template_vault,
            TOPIC,
            evaluate=_evaluate_with_manifest(
                0.40, generation=index + 1, per_id={f"q-prune-{index}": _per_id_delta()}
            ),
            branch_prefix="loop/c/",
        )
        runner.set_baseline(0.80, harness_version="fake-source-gate")

        result = runner.poll_once()

        assert result.acted is True
        if index == 0:
            oldest_quarantine_branch = f"loop/x/{TOPIC}/source-{suggestion_id[:8]}"

    vcs = VaultVcs(template_vault)
    remaining = {branch for branch, _ in vcs.list_branch_tips(f"loop/x/{TOPIC}/")}
    assert len(remaining) <= 5, (
        f"loop/x/* quarantine branches must be pruned to the newest few per topic, saw {remaining}"
    )
    assert oldest_quarantine_branch not in remaining, (
        "the oldest quarantine branch must be the one pruned first"
    )
