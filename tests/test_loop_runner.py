"""Wound → red → revert → green cycle for :class:`~knotica.core.loop.LoopRunner`.

Zero network: evaluate is injected. Real git branches on ``template_vault``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.loop_state import read_loop_state
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.status import gather_wiki_status
from knotica.core.vcs import VaultVcs
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

    good = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.55), arena_enabled=False
    )
    _commit_content_change(template_vault, "good ingest")
    held = good.observe_default()
    assert held.acted is True
    assert held.decision is LoopDecision.pass_

    bad = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.30), arena_enabled=False
    )
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
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    )
    _commit_content_change(template_vault, "unobserved history")

    state = runner.mark_observed()
    assert state.stage.value == "idle"

    noop = runner.observe_default()
    assert noop.acted is False, "adopted HEAD must not re-trigger an observation"


def test_observation_holds_while_ingest_is_active(template_vault: Path) -> None:
    from knotica.core.ingest_activity import append_ingest_event

    store = LocalFSStore(template_vault)
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    )
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
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.70), arena_enabled=False
    )
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
    better = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.80), arena_enabled=False
    )
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
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    )

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

    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    )
    state = runner.rebaseline("best")
    assert state.baseline_scalar == 0.80, "only current-instrument records are comparable"


def test_merged_result_branches_are_pruned_beyond_keep(template_vault: Path) -> None:
    runner = LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    )
    runner.observe_default()
    for index in range(6):
        _commit_content_change(template_vault, f"content wave {index}")
        runner.observe_default()

    vcs = VaultVcs(template_vault)
    result_branches = [b for b, _ in vcs.list_branch_tips("loop/r/")]
    assert len(result_branches) <= 5, (
        f"merged loop/r/* pointers must be pruned to the newest few, saw {result_branches}"
    )
