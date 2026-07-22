"""Wound → red → revert → green cycle for :class:`~knotica.core.loop.LoopRunner`.

Zero network: evaluate is injected. Real git branches on ``template_vault``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from knotica.core.arena import ArenaStage, ArenaState
from knotica.core.gap_classifier import gaps_path
from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.loop_state import read_loop_state
from knotica.core.page import page_path
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
from knotica.evals.golden import GoldenSetFloorWarning, freeze, load
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
CANDIDATE = "loop/c/wound"


def _fake_evaluate(scalar: float):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-m2-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        # Drop a marker file so a keep-merge is observable on the default branch.
        marker = clone.root / TOPIC / ".knotica" / "loop-eval-marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"scalar={scalar}\n", encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: record scalar {scalar}")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-17T00:00:00Z",
            generation=1,
            harness_version="fake-m2",
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


def _open_candidate(vault: Path, body: str) -> str:
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
    sha = vcs.head_sha()
    vcs.checkout_branch(default)
    return sha


def test_wound_red_revert_then_green_keep(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.40),
        branch_prefix="loop/c/",
    )
    runner.set_baseline(0.5707, harness_version="fake-m2")

    # --- red path ---
    _open_candidate(template_vault, "# wounded query\n")
    red = runner.poll_once()
    assert red.acted is True
    assert red.decision is LoopDecision.fail
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE)

    status = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert status["gate"]["baseline"] == 0.5707
    assert status["gate"]["state"] == "fail"
    assert status["loop"]["last_decision"] == "fail"
    assert status["loop"]["stage"] == "failed"

    # Restart mid-cycle survival: cursors prevent reprocessing the same tip.
    noop = runner.poll_once()
    assert noop.acted is False

    # --- green path ---
    runner_ok = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.60),
        branch_prefix="loop/c/",
    )
    _open_candidate(template_vault, "# healed query\n")
    green = runner_ok.poll_once()
    assert green.acted is True
    assert green.decision is LoopDecision.pass_
    assert not VaultVcs(template_vault).branch_exists(CANDIDATE)

    marker = template_vault / TOPIC / ".knotica" / "loop-eval-marker.txt"
    assert marker.is_file(), "keep path must merge the eval clone tip onto default"
    assert "0.6" in marker.read_text(encoding="utf-8")

    status = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert status["gate"]["state"] == "pass"
    assert status["loop"]["stage"] == "passed"
    assert status["loop"]["last_decision"] == "pass"

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.5707


def _commit_content_change(vault: Path, note: str) -> None:
    """Land a content commit on the default branch (an 'ingest' stand-in)."""
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "observed-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def test_first_observation_auto_freezes_baseline(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.52),
        arena_enabled=False,
    )

    observed = runner.observe_default()
    assert observed.acted is True
    assert observed.decision is LoopDecision.pass_

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.52

    # The observation's own metrics merge must not re-trigger an observation.
    noop = runner.observe_default()
    assert noop.acted is False


def test_observation_holds_and_regresses_against_baseline(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    ).set_baseline(0.50)

    good = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.55), arena_enabled=False)
    _commit_content_change(template_vault, "good ingest")
    held = good.observe_default()
    assert held.acted is True
    assert held.decision is LoopDecision.pass_

    bad = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.30), arena_enabled=False)
    _commit_content_change(template_vault, "degrading ingest")
    regressed = bad.observe_default()
    assert regressed.acted is True
    assert regressed.decision is LoopDecision.fail

    status = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert status["gate"]["state"] == "fail"
    # Content on the default branch is human-owned: the page must survive.
    assert (template_vault / TOPIC / "observed-note.md").is_file()


def _log_appending_evaluate(scalar: float):
    """Fake evaluate that appends to the clone's log.md like the real harness."""
    inner = _fake_evaluate(scalar)

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        result = inner(topic, source_root, ref)
        clone_root = result.clone_root
        log = clone_root / "log.md"
        existing = log.read_text(encoding="utf-8") if log.is_file() else ""
        log.write_text(f"- eval generation appended\n{existing}", encoding="utf-8")
        run_git(clone_root, "add", "log.md")
        run_git(clone_root, "commit", "-m", "eval: append log entry")
        # Concurrent human activity on the LIVE vault while the eval runs:
        # another log.md append at the same location — the exact both-sides
        # append that conflicts without the union merge driver.
        live_log = source_root / "log.md"
        live_existing = live_log.read_text(encoding="utf-8") if live_log.is_file() else ""
        live_log.write_text(f"- concurrent curation appended\n{live_existing}", encoding="utf-8")
        run_git(source_root, "add", "log.md")
        run_git(source_root, "commit", "-m", "curate: concurrent log entry")
        return _reclone_result(result, clone_root)

    return _evaluate


def _reclone_result(result, clone_root: Path):
    from knotica.evals.harness import EvalRunResult

    record = MetricsRecord(
        topic=TOPIC,
        timestamp="2026-07-18T00:00:00Z",
        generation=1,
        harness_version="fake-m2",
        scalar=result.scalar,
        components=MetricsComponents(
            qa_accuracy=result.scalar,
            citation_validity=1.0,
            lint_violations=0.0,
            token_cost=0.0,
        ),
        n_examples=1,
        corpus_ref=result.corpus_ref,
        artifact_ref=None,
    )
    return wrap_harness_result(EvalRunResult(record=record, clone_root=clone_root))


def test_observation_merges_despite_concurrent_log_appends(template_vault: Path) -> None:
    """Both sides append log.md during an observation; the union driver must absorb it."""
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_log_appending_evaluate(0.50),
        arena_enabled=False,
    )

    observed = runner.observe_default()
    assert observed.acted is True
    assert observed.decision is LoopDecision.pass_

    attributes = (template_vault / ".gitattributes").read_text(encoding="utf-8")
    assert "log.md merge=union" in attributes
    log_body = (template_vault / "log.md").read_text(encoding="utf-8")
    assert "eval generation appended" in log_body, "the clone-side log line survived the merge"
    assert "concurrent curation appended" in log_body, "the live-side log line survived too"


def test_mark_observed_settles_cursor_without_eval(template_vault: Path) -> None:
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    _commit_content_change(template_vault, "unobserved history")

    state = runner.mark_observed()
    assert state.stage.value == "idle"

    noop = runner.observe_default()
    assert noop.acted is False, "adopted HEAD must not re-trigger an observation"


def test_observation_holds_while_ingest_is_active(template_vault: Path) -> None:
    from knotica.core.ingest_activity import append_ingest_event

    store = LocalFSStore(template_vault)
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    _commit_content_change(template_vault, "content during ingest")
    event = append_ingest_event(
        store, template_vault, topic=TOPIC, stage="write_page", title="mid-ingest"
    )

    held = runner.observe_default()
    assert held.acted is False
    assert "ingest in progress" in held.message

    append_ingest_event(
        store,
        template_vault,
        topic=TOPIC,
        stage="complete",
        title="done",
        status="ok",
        run_id=str(event["run_id"]),
    )
    observed = runner.observe_default()
    assert observed.acted is True, "terminal ingest event releases the hold"


def test_observation_waits_for_head_stability_window(template_vault: Path) -> None:
    fake_now = {"t": 100.0}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.50),
        arena_enabled=False,
        observe_quiet_seconds=20.0,
        clock=lambda: fake_now["t"],
    )
    _commit_content_change(template_vault, "burst commit one")

    settling = runner.observe_default()
    assert settling.acted is False
    assert "settling" in settling.message

    # A further commit inside the window restarts the clock.
    fake_now["t"] += 10.0
    _commit_content_change(template_vault, "burst commit two")
    restarted = runner.observe_default()
    assert restarted.acted is False

    fake_now["t"] += 10.0
    still_waiting = runner.observe_default()
    assert still_waiting.acted is False

    fake_now["t"] += 21.0
    observed = runner.observe_default()
    assert observed.acted is True, "stable HEAD past the window finally observes"


def test_instrument_change_refreezes_baseline_instead_of_false_regression(
    template_vault: Path,
) -> None:
    """A rotated harness fingerprint re-baselines from the first new-instrument reading."""
    store = LocalFSStore(template_vault)
    old_instrument = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.90), arena_enabled=False
    )
    old_instrument.set_baseline(0.90, harness_version="harness-old")

    # New instrument reads LOWER — without the re-freeze this would be a
    # (false) regression; with it, it becomes the new reference.
    new_instrument = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.60), arena_enabled=False
    )
    _commit_content_change(template_vault, "content under new instrument")
    observed = new_instrument.observe_default()

    assert observed.acted is True
    assert observed.decision is LoopDecision.pass_
    assert "re-frozen" in observed.message

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.60
    assert state.baseline_harness_version == "fake-m2"


def test_best_policy_ratchets_baseline_upward(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.70), arena_enabled=False)
    runner.set_baseline(0.50, harness_version="fake-m2")
    runner.set_baseline_policy("best")

    _commit_content_change(template_vault, "better content")
    observed = runner.observe_default()
    assert observed.decision is LoopDecision.pass_
    assert "high-water" in observed.message

    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.70, "best policy raises the bar to the new reading"

    # Under latest policy the same improvement would NOT move the baseline.
    runner.set_baseline_policy("latest")
    better = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.80), arena_enabled=False)
    _commit_content_change(template_vault, "even better content")
    held = better.observe_default()
    assert held.decision is LoopDecision.pass_
    state = read_loop_state(store, TOPIC)
    assert state is not None
    assert state.baseline_scalar == 0.70, "latest policy leaves the bar where it was"


def _seed_metrics_history(vault: Path, scalars: list[float], harness: str = "fake-m2") -> None:
    """Write a metrics.jsonl history directly (generation = list order)."""
    path = vault / TOPIC / ".knotica" / "metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for generation, scalar in enumerate(scalars, start=1):
        record = MetricsRecord(
            topic=TOPIC,
            timestamp=f"2026-07-18T00:0{generation}:00Z",
            generation=generation,
            harness_version=harness,
            scalar=scalar,
            components=MetricsComponents(
                qa_accuracy=scalar, citation_validity=1.0, lint_violations=0.0, token_cost=0.0
            ),
            n_examples=1,
            corpus_ref="git:seeded",
            artifact_ref=None,
        )
        lines.append(record.to_json_line())
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: seed metrics history")


def test_rebaseline_freezes_high_water_mark_from_history(template_vault: Path) -> None:
    _seed_metrics_history(template_vault, [0.60, 0.90, 0.70])
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    state = runner.rebaseline("best")
    assert state.baseline_scalar == 0.90, "rebaseline best freezes the high-water mark"

    state = runner.rebaseline("latest")
    assert state.baseline_scalar == 0.70, "rebaseline latest freezes the newest record"


def test_rebaseline_ignores_records_from_previous_instruments(template_vault: Path) -> None:
    # A stale 0.99 under an old instrument must never become the bar.
    path = template_vault / TOPIC / ".knotica" / "metrics.jsonl"
    _seed_metrics_history(template_vault, [0.99], harness="old-instrument")
    old_line = path.read_text(encoding="utf-8")
    _seed_metrics_history(template_vault, [0.60, 0.80])
    path.write_text(old_line + path.read_text(encoding="utf-8"), encoding="utf-8")

    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    state = runner.rebaseline("best")
    assert state.baseline_scalar == 0.80, "only current-instrument records are comparable"


def test_merged_result_branches_are_pruned_beyond_keep(template_vault: Path) -> None:
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    runner.observe_default()
    for index in range(6):
        _commit_content_change(template_vault, f"content wave {index}")
        runner.observe_default()

    vcs = VaultVcs(template_vault)
    result_branches = [b for b, _ in vcs.list_branch_tips("loop/r/")]
    assert len(result_branches) <= 5, (
        f"merged loop/r/* pointers must be pruned to the newest few, saw {result_branches}"
    )


# ---------------------------------------------------------------------------
# The heal-redirect contract at the loop boundary: a regression is classified
# by cause before the arena races prompt variants. Derived from the
# behavioral spec's heal-redirect decision (skip the arena only when every
# regressed id is knowledge-cause; heal on any ambiguity or failure), never
# from the loop hook's implementation.
#
# RED-first: the hook does not exist on ``core/loop.py`` yet when this section
# is written (paired implementer step lands concurrently) — every scenario
# below drives the change through the public ``LoopRunner.observe_default``
# surface plus a spy on ``race_variants`` (the arena entry point), so a test
# fails honestly against today's "always heal" behavior rather than against an
# import that does not yet exist.
# ---------------------------------------------------------------------------


def _write_live_page(vault: Path, page_name: str, body: str = "# stub\n") -> None:
    """Commit a page directly onto the live default branch (so the next eval clone sees it)."""
    path = vault / page_path(TOPIC, page_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: add page {page_name}")


def _freeze_golden(vault: Path, *, query: str, answer: str, pages_used: tuple[str, ...]) -> str:
    """Freeze one held-out golden question on the live vault; return its stable id."""
    store = LocalFSStore(vault)
    with pytest.warns(GoldenSetFloorWarning):
        freeze(
            store,
            vault,
            TOPIC,
            [{"question": query, "reference_answer": answer, "pages_used": list(pages_used)}],
        )
    matches = [record for record in load(store, TOPIC) if record.query == query]
    return matches[-1].id


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


def _manifest_with_deltas(
    *,
    generation: int,
    per_id: dict[str, dict] | None,
    traces: dict[str, list[str]],
) -> dict:
    """A v2 manifest carrying only what the classifier reads (mirrors the real harness shape)."""
    held_out_delta = (
        None
        if per_id is None
        else {
            "ids_added": [],
            "ids_removed": [],
            "prior_generation": generation - 1,
            "scalar_delta": -0.1,
            "per_id": per_id,
        }
    )
    return {
        "manifest_schema_version": 2,
        "generation": generation,
        "per_example": [{"id": qa_id, "pages": pages} for qa_id, pages in traces.items()],
        "held_out_delta": held_out_delta,
    }


def _regression_fake_evaluate(scalar: float, *, generation: int, manifest: dict):
    """Fake evaluate that also writes a v2 manifest onto the clone at the real harness path."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-gapfill-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        manifest_dir = clone.root / topic / ".knotica" / "eval-runs" / f"gen-{generation}"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: write gen-{generation} manifest")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-18T00:00:00Z",
            generation=generation,
            harness_version="fake-gapfill",
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

    monkeypatch.setattr("knotica.core.arena_resolve.race_variants", _fake_race_variants)
    return calls


def _gapfill_runner(vault: Path, *, evaluate) -> LoopRunner:
    return LoopRunner(
        vault,
        TOPIC,
        evaluate=evaluate,
        arena_enabled=True,
        arena_score=lambda *_args, **_kwargs: 0.0,
    )


def test_all_knowledge_cause_regression_skips_arena_and_persists_gap_records(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    qa_id = _freeze_golden(
        template_vault,
        query="Does the vault cover quantum retrieval augmentation?",
        answer="No, that concept is absent from this vault.",
        pages_used=("nonexistent-page",),
    )
    manifest = _manifest_with_deltas(
        generation=2, per_id={qa_id: _per_id_delta()}, traces={qa_id: []}
    )
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill")
    arena_calls = _arena_spy(monkeypatch)
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert arena_calls == [], "an all-knowledge-cause regression must never race the arena"
    assert store.exists(gaps_path(TOPIC)), "a genuine_gap verdict must be persisted for P3"
    assert qa_id in store.read_text(gaps_path(TOPIC))


def test_mixed_cause_regression_still_races_arena_and_persists_only_the_knowledge_gap(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    _write_live_page(template_vault, "react")
    # freeze() publishes the WHOLE reviewed set (replace, not append) — both
    # questions must land in one call or the first vanishes from the golden set.
    with pytest.warns(GoldenSetFloorWarning):
        freeze(
            store,
            template_vault,
            TOPIC,
            [
                {
                    "question": "Does the vault cover quantum retrieval augmentation?",
                    "reference_answer": "No, that concept is absent from this vault.",
                    "pages_used": ["nonexistent-page"],
                },
                {
                    "question": "What does the react page say about acting and reasoning?",
                    "reference_answer": "It interleaves reasoning traces with actions.",
                    "pages_used": ["react"],
                },
            ],
        )
    by_query = {record.query: record.id for record in load(store, TOPIC)}
    gap_id = by_query["Does the vault cover quantum retrieval augmentation?"]
    genfault_id = by_query["What does the react page say about acting and reasoning?"]
    manifest = _manifest_with_deltas(
        generation=2,
        per_id={gap_id: _per_id_delta(), genfault_id: _per_id_delta()},
        traces={gap_id: [], genfault_id: ["react"]},
    )
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill")
    arena_calls = _arena_spy(monkeypatch)
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert len(arena_calls) == 1, (
        "a mixed regression (any non-knowledge-cause id present) must still race the arena"
    )
    gap_body = store.read_text(gaps_path(TOPIC))
    assert gap_id in gap_body
    assert genfault_id not in gap_body, "a generation-fault verdict must never become a gap record"


def test_null_held_out_delta_races_arena_and_writes_no_gap_records(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    manifest = _manifest_with_deltas(generation=1, per_id=None, traces={})
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=1, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill")
    arena_calls = _arena_spy(monkeypatch)
    _commit_content_change(template_vault, "the cold-start regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert len(arena_calls) == 1, "a null held_out_delta cannot localize a cause; heal as before"
    assert not store.exists(gaps_path(TOPIC)), "a null delta must never produce a gap record"


def test_manifest_with_no_regressed_ids_is_a_defensive_no_op(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    manifest = _manifest_with_deltas(generation=2, per_id={}, traces={})
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill")
    arena_calls = _arena_spy(monkeypatch)
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True
    assert len(arena_calls) == 1, "an empty regressed-id set must still fall through to the heal"
    assert not store.exists(gaps_path(TOPIC))


def test_classifier_exception_still_races_arena_and_does_not_crash_the_cycle(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSStore(template_vault)
    qa_id = _freeze_golden(
        template_vault,
        query="Does the vault cover quantum retrieval augmentation?",
        answer="No, that concept is absent from this vault.",
        pages_used=("nonexistent-page",),
    )
    manifest = _manifest_with_deltas(
        generation=2, per_id={qa_id: _per_id_delta()}, traces={qa_id: []}
    )
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill")
    arena_calls = _arena_spy(monkeypatch)

    def _raising_classify_regression(*_args, **_kwargs):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(
        "knotica.core.gap_classifier.classify_regression", _raising_classify_regression
    )
    _commit_content_change(template_vault, "the regressing ingest")

    result = runner.observe_default()

    assert result.acted is True, "a classifier exception must never crash the observe cycle"
    assert len(arena_calls) == 1, "the arena must still fire when classification raises"
    assert not store.exists(gaps_path(TOPIC)), "no gap record can be trusted from a failed run"


def test_gap_record_commit_does_not_retrigger_a_fresh_observation(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: the redirect's own gap-record commit must read as bookkeeping, not content."""
    qa_id = _freeze_golden(
        template_vault,
        query="Does the vault cover quantum retrieval augmentation?",
        answer="No, that concept is absent from this vault.",
        pages_used=("nonexistent-page",),
    )
    manifest = _manifest_with_deltas(
        generation=2, per_id={qa_id: _per_id_delta()}, traces={qa_id: []}
    )
    runner = _gapfill_runner(
        template_vault, evaluate=_regression_fake_evaluate(0.40, generation=2, manifest=manifest)
    )
    runner.set_baseline(0.90, harness_version="fake-gapfill")
    _arena_spy(monkeypatch)
    _commit_content_change(template_vault, "the regressing ingest")
    first = runner.observe_default()
    assert first.acted is True

    second = runner.observe_default()

    assert second.acted is False, (
        "a gap-record commit is bookkeeping and must not trigger a fresh eval cycle"
    )
