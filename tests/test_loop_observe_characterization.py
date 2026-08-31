"""Characterization tests pinning `observe_default()`'s observable message set.

Written before the fourth-pass extraction of the observe leg out of
``core/loop.py`` into ``core/loop_observe.py``. The scheduling *decisions* are
already densely pinned elsewhere (`test_loop_runner`, `test_loop_cadence*`,
`test_td011_eval_rearm`, `test_loop_blocked_failure_backoff`); what no test
pinned is the pair the move can most easily corrupt without failing anything:

* the **decline envelope** -- every no-op path returns the *same* shape
  (``acted=False``, the default branch and its live HEAD, ``decision=none``,
  ``scalar=None``) and differs only in ``message``. Callers read that shape:
  ``session_status`` and the two-phase confirm surfaces report ``billed:
  false`` from it.
* the **exact decline and outcome strings**, which are what an operator sees
  and what several dispatchers substring-match on.

Each string is asserted in full rather than by fragment: a fragment survives a
reworded message, which is exactly the drift a behavior-preserving move is
supposed to be unable to introduce.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from knotica.core.loop import LoopDecision, LoopRunner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"


def _fake_evaluate(scalar: float, *, harness_version: str = "fake-observe-char"):
    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-observe-char-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        marker = clone.root / TOPIC / ".knotica" / "loop-eval-marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"scalar={scalar}\n", encoding="utf-8")
        run_git(clone.root, "add", "-A")
        run_git(clone.root, "commit", "-m", f"eval: record scalar {scalar}")
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-08-31T00:00:00Z",
            generation=1,
            harness_version=harness_version,
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


def _commit_content_change(vault: Path, note: str) -> None:
    vcs = VaultVcs(vault)
    vcs.checkout_branch(vcs.default_branch())
    page = vault / TOPIC / "observe-char-note.md"
    page.write_text(f"# note\n\n{note}\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: {note}")


def _assert_declined(result, vault: Path, message: str) -> None:
    """Every decline returns one shape; only ``message`` varies."""
    vcs = VaultVcs(vault)
    assert result.acted is False
    assert result.branch == vcs.default_branch()
    assert result.sha == vcs.head_sha()
    assert result.decision is LoopDecision.none
    assert result.scalar is None
    assert result.message == message


def test_an_immediate_repeat_observation_declines_as_bookkeeping_not_as_unchanged(
    template_vault: Path,
) -> None:
    """The loop never re-observes its own metrics merge -- and says which guard caught it.

    Worth pinning precisely because the *other* guard is the intuitive answer:
    ``cursor == head`` is checked first, but the cursor is recorded from the
    pre-write HEAD and the state write that records it commits, so HEAD is
    always one commit ahead by the time the next tick asks. The bookkeeping
    classifier is what actually holds the line, on every tick.
    """
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    _commit_content_change(template_vault, "first content")
    runner.observe_default()

    _assert_declined(
        runner.observe_default(),
        template_vault,
        "only loop bookkeeping changed since last observation",
    )


def test_a_bookkeeping_only_change_declines_without_moving_the_cursor(
    template_vault: Path,
) -> None:
    from knotica.core.loop_state import read_loop_state

    store = LocalFSStore(template_vault)
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    _commit_content_change(template_vault, "observed content")
    runner.observe_default()

    before = read_loop_state(store, TOPIC)
    assert before is not None
    cursor_before = dict(before.cursors)

    # `log.md` is bookkeeping by definition: appending to it must not wake the loop.
    log = template_vault / "log.md"
    log.write_text(log.read_text(encoding="utf-8") + "\n- hand-appended line\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: bookkeeping-only commit")

    _assert_declined(
        runner.observe_default(),
        template_vault,
        "only loop bookkeeping changed since last observation",
    )

    after = read_loop_state(store, TOPIC)
    assert after is not None
    assert dict(after.cursors) == cursor_before, (
        "a bookkeeping-only decline writes no state: the cursor waits for real content"
    )


def test_a_live_ingest_declines_with_the_ingest_message_and_clears_the_quiet_window(
    template_vault: Path,
) -> None:
    from knotica.core.ingest_activity import append_ingest_event

    store = LocalFSStore(template_vault)
    now = {"t": 100.0}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.50),
        arena_enabled=False,
        observe_quiet_seconds=20.0,
        clock=lambda: now["t"],
    )
    _commit_content_change(template_vault, "content mid-ingest")

    # Arm the quiet window first, so the ingest branch has something to clear.
    runner.observe_default()
    assert runner._pending_head is not None

    append_ingest_event(store, template_vault, topic=TOPIC, stage="write_page", title="mid-ingest")
    _assert_declined(
        runner.observe_default(),
        template_vault,
        "observation held: ingest in progress",
    )
    assert runner._pending_head is None, (
        "the ingest hold resets the settling window rather than letting it expire mid-ingest"
    )


def test_the_quiet_window_declines_with_its_configured_duration_in_the_message(
    template_vault: Path,
) -> None:
    now = {"t": 100.0}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.50),
        arena_enabled=False,
        observe_quiet_seconds=20.0,
        clock=lambda: now["t"],
    )
    _commit_content_change(template_vault, "burst commit")

    _assert_declined(
        runner.observe_default(),
        template_vault,
        "observation settling (20s quiet window)",
    )

    # The debounce lives on the runner, not on the call: the SAME runner carried
    # `_pending_head`/`_pending_since` across ticks and observes once they expire.
    now["t"] += 21.0
    assert runner.observe_default().acted is True


def test_an_observation_at_or_above_baseline_reports_holding_it(template_vault: Path) -> None:
    LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    ).set_baseline(0.50, harness_version="fake-observe-char")

    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.75), arena_enabled=False)
    _commit_content_change(template_vault, "improving content")
    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.pass_
    assert result.message == "observation 0.7500 holds baseline"


def test_a_regression_with_the_arena_disabled_reports_both_scalars(template_vault: Path) -> None:
    LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False
    ).set_baseline(0.50, harness_version="fake-observe-char")

    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.30), arena_enabled=False)
    _commit_content_change(template_vault, "degrading content")
    result = runner.observe_default()

    assert result.acted is True
    assert result.decision is LoopDecision.fail
    assert result.message == "observation 0.3000 regressed below baseline 0.5000"


def test_a_first_observation_reports_the_auto_frozen_baseline(template_vault: Path) -> None:
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.42), arena_enabled=False)
    _commit_content_change(template_vault, "first ever content")
    result = runner.observe_default()

    assert result.message == "first observation auto-froze baseline at 0.4200"


def test_an_instrument_change_reports_the_re_freeze_with_both_readings(
    template_vault: Path,
) -> None:
    LoopRunner(
        template_vault, TOPIC, evaluate=_fake_evaluate(0.90), arena_enabled=False
    ).set_baseline(0.90, harness_version="harness-old")

    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.60, harness_version="harness-new"),
        arena_enabled=False,
    )
    _commit_content_change(template_vault, "content under the new instrument")
    result = runner.observe_default()

    assert result.decision is LoopDecision.pass_
    assert result.message == (
        "instrument changed; baseline re-frozen at 0.6000 (was 0.9000 under a previous harness)"
    )


def test_a_cadence_hold_declines_with_the_elapsed_and_configured_interval(
    template_vault: Path,
) -> None:
    clock = {"now": datetime(2026, 8, 31, 12, 0, 0)}
    runner = LoopRunner(
        template_vault,
        TOPIC,
        evaluate=_fake_evaluate(0.50),
        arena_enabled=False,
        eval_min_interval_hours=6.0,
        now_fn=lambda: clock["now"],
    )
    _commit_content_change(template_vault, "first content")
    assert runner.observe_default().acted is True

    clock["now"] = datetime(2026, 8, 31, 13, 30, 0)
    _commit_content_change(template_vault, "second content, inside the interval")
    _assert_declined(
        runner.observe_default(),
        template_vault,
        "cadence held: 1.50h since last eval start < 6h interval",
    )
