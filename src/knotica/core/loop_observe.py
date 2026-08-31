"""The observe leg -- eval the default branch when its content moved.

Extracted from :mod:`knotica.core.loop`'s ``LoopRunner`` (``observe_default``
and the three helper clusters only it reaches: the pacing guards, and the
post-regression arena heal) to keep the already-oversized ``loop.py`` from
carrying the project's longest method as well. Fourth in the series that
produced :mod:`knotica.core.loop_factory`, :mod:`knotica.core.arena_resolve`
and :mod:`knotica.core.candidate_gate`; same shape as all three -- free
functions taking the driving :class:`~knotica.core.loop.LoopRunner` as an
explicit first parameter.

Two seams are load-bearing and must not be "simplified":

* ``observe_default`` stays reachable as a thin ``LoopRunner`` method. It is
  the runner's public API and the billing boundary every caller patches.
* the cadence guard is consulted through ``runner._cadence_hold``, the
  *method*, never through :func:`cadence_hold` directly. The candidate-gate
  path's contract is that it never consults cadence at all, and the test that
  proves it spies on the method.

The observation debounce (``_pending_head``/``_pending_since``) stays runner
state, mutated here through ``runner``: the CLI watcher keeps one runner alive
across ticks precisely so a burst of commits coalesces into one eval, and a
per-call debounce would settle forever.
"""

from __future__ import annotations

from datetime import datetime
from datetime import time as _time_of_day
from typing import TYPE_CHECKING

from knotica.core.arena import ArenaState
from knotica.core.arena_resolve import run_arena_and_resolve
from knotica.core.branch_namespaces import RESULT_BRANCH_PREFIX
from knotica.core.loop_attempt import (
    is_same_content_retry,
    note_attempt,
    record_failed_attempt,
    retry_hold,
)
from knotica.core.loop_state import (
    LoopDecision,
    LoopStage,
    LoopState,
    empty_loop_state,
    read_loop_state,
    write_loop_state,
)
from knotica.core.vcs import discarded_clone

if TYPE_CHECKING:
    from knotica.core.loop import LoopCycleResult, LoopRunner

__all__ = [
    "cadence_hold",
    "heal_prompts_after_regression",
    "observation_hold",
    "observe_default",
]


def observe_default(
    runner: "LoopRunner", *, auto_baseline: bool = True, force: bool = False
) -> "LoopCycleResult":
    """Eval the default branch when its HEAD moved since the last observation.

    This is the autonomous "observe" leg: content lands on the default branch
    (an ingest, a page edit), the watcher notices, evals on a clone, and
    merges the metrics commit back so the chart moves without any manual
    step. With ``auto_baseline`` the first observation freezes itself as the
    gate baseline — a fresh topic becomes fully gated with zero setup. A
    regression below baseline triggers the arena self-correction on the
    prompt substrate (content on the default branch is human-owned and is
    never reverted here).
    """
    from knotica.core import loop_gap_redirect
    from knotica.core.loop import LoopCycleResult

    state = read_loop_state(runner._store, runner._topic) or empty_loop_state(runner._topic)
    default = runner._vcs.default_branch()
    head = runner._vcs.head_sha()

    def _declined(message: str) -> LoopCycleResult:
        """A no-op cycle: observed, chose not to evaluate, wrote nothing."""
        return LoopCycleResult(
            acted=False,
            branch=default,
            sha=head,
            decision=LoopDecision.none,
            scalar=None,
            message=message,
        )

    cursor = state.cursors.get(default)
    if cursor == head:
        return _declined("default branch unchanged since last observation")
    if cursor is not None and not runner._content_changed_since(cursor, head):
        # Only bookkeeping moved since the cursor (loop-state / metrics / log
        # commits written by the loop itself). Deliberately no state write:
        # the cursor stays put until real content lands, so the loop never
        # commits (or evals) in response to its own writes.
        return _declined("only loop bookkeeping changed since last observation")

    hold = observation_hold(runner, head)
    if hold is not None:
        return _declined(hold)

    retrying = is_same_content_retry(state, head, content_changed=runner._content_changed_since)

    # ``force`` clears BOTH pacing holds, not cadence alone. Both pace the
    # *unattended* watcher; neither is a correctness gate. The retry floor in
    # particular cannot see its own precondition being fixed -- freezing a
    # golden set is a ``.knotica/`` write, which ``is_same_content_retry``
    # ignores by design -- so waiting it out was the only way past it. ``force``
    # reaches here solely from a two-phase, cost-quoted human confirm; every
    # autonomous caller leaves it false, which
    # ``test_loop_blocked_failure_backoff`` pins along with the incident this
    # floor exists to prevent.
    if not force:
        failure_hold = retry_hold(
            runner._root, runner._topic, state, same_content_retry=retrying, now=runner._now_fn()
        )
        if failure_hold is not None:
            return _declined(failure_hold)

        # Through the method, not :func:`cadence_hold`: the candidate-gate
        # path's never-consults-cadence contract is proven by a spy on it.
        cadence = runner._cadence_hold(state, runner._now_fn())
        if cadence is not None:
            return _declined(cadence)

    runner._ensure_union_log_merge()
    started_at = runner._now_fn()
    note_attempt(runner._root, runner._topic, at=started_at)
    # What the vault currently records, kept for the failure path to compare
    # against — ``state`` below becomes the in-flight attempt, which is not
    # the same thing and would compare equal to itself.
    stored = state
    attempt = state.model_copy(
        update={
            "stage": LoopStage.evaluating,
            "candidate_branch": default,
            "candidate_sha": head,
            "last_error": None,
            "last_eval_started_at": started_at,
        }
    )
    # ``evaluating`` → ``failed`` is ONE attempt, so a re-attempt of a failure
    # already on record must not pay a commit for its first half either (see
    # :mod:`knotica.core.loop_attempt`). Liveness meanwhile stays visible via
    # the gitignored heartbeat/progress files, never via commits.
    state = (
        attempt
        if retrying
        else write_loop_state(
            runner._store, runner._root, attempt, title=f"observing {default}@{head[:12]}"
        )
    )
    # Pin the eval clone AFTER the state commit above: the live side then has
    # no loop-authored commits the merge would have to reconcile — only
    # concurrent human activity, which the union log.md attribute absorbs.
    eval_ref = runner._vcs.head_sha()
    try:
        outcome = runner._evaluate(runner._topic, runner._root, eval_ref)
    except Exception as exc:  # noqa: BLE001 — surface into loop-state, keep runner alive
        # Do NOT mark_processed here: the cursor must stay unadvanced so the
        # next tick still sees content-changed against this same head and
        # re-attempts the eval, paced by the attempt clock ``note_attempt``
        # advanced above. Whether the failure is *written* at all is
        # loop_attempt's call — an attempt recording nothing new costs no commit.
        record_failed_attempt(
            runner._store,
            runner._root,
            stored=stored,
            attempt=state,
            branch=default,
            head=head,
            exc=exc,
            same_content=retrying,
        )
        return LoopCycleResult(
            acted=True,
            branch=default,
            sha=head,
            decision=LoopDecision.fail,
            scalar=None,
            message=f"observation eval failed: {exc}",
        )

    with discarded_clone(outcome.clone_root):
        # Bring the metrics commit home so the chart reflects the observation.
        # The merge, the post-merge head read, and the cursor-advancing state
        # write are ONE atomic span: a concurrent pass must not move the default
        # branch's HEAD between the merge and ``mark_processed`` (that would mark
        # someone else's commit observed and silently skip a real content change).
        with runner._mutation_span():
            result_branch = f"{RESULT_BRANCH_PREFIX}{eval_ref[:12]}"
            runner._vcs.fetch_ref_from(outcome.clone_root, "HEAD", result_branch)
            runner._vcs.checkout_branch(default)
            runner._vcs.merge_branch(result_branch, ff_only=False)
            if runner._push_remote:
                runner._vcs.push(runner._push_remote, default)
            runner._prune_result_branches()

            scalar = float(outcome.scalar)
            baseline = state.baseline_scalar
            updates: dict[str, object] = {
                "last_scalar": scalar,
                "last_generation": int(outcome.generation),
                "last_harness_version": outcome.harness_version,
                "candidate_branch": None,
                "candidate_sha": None,
                "last_error": None,
                "pending_retry": False,
            }
            # A baseline is only comparable under the instrument that produced it.
            # When the harness fingerprint rotates (judge prompt edit, model
            # rotation, dspy upgrade), the first observation on the new instrument
            # re-freezes the reference — the old scalar is not a valid bar anymore.
            instrument_changed = (
                baseline is not None
                and state.baseline_harness_version is not None
                and state.baseline_harness_version != outcome.harness_version
            )
            if baseline is None and auto_baseline:
                updates |= {
                    "baseline_scalar": scalar,
                    "baseline_harness_version": outcome.harness_version,
                    "baseline_corpus_ref": outcome.corpus_ref,
                    "baseline_golden_manifest_sha": runner._golden_manifest_sha(),
                    "stage": LoopStage.passed,
                    "last_decision": LoopDecision.pass_,
                }
                message = f"first observation auto-froze baseline at {scalar:.4f}"
            elif instrument_changed and auto_baseline:
                assert baseline is not None  # implied by ``instrument_changed``
                updates |= {
                    "baseline_scalar": scalar,
                    "baseline_harness_version": outcome.harness_version,
                    "baseline_corpus_ref": outcome.corpus_ref,
                    "baseline_golden_manifest_sha": runner._golden_manifest_sha(),
                    "stage": LoopStage.passed,
                    "last_decision": LoopDecision.pass_,
                }
                message = (
                    f"instrument changed; baseline re-frozen at {scalar:.4f} "
                    f"(was {float(baseline):.4f} under a previous harness)"
                )
            elif (
                baseline is not None
                and scalar > float(baseline)
                and state.baseline_policy == "best"
            ):
                # High-water-mark policy: a better reading raises the bar itself.
                updates |= {
                    "baseline_scalar": scalar,
                    "baseline_harness_version": outcome.harness_version,
                    "baseline_corpus_ref": outcome.corpus_ref,
                    "baseline_golden_manifest_sha": runner._golden_manifest_sha(),
                    "stage": LoopStage.passed,
                    "last_decision": LoopDecision.pass_,
                }
                message = f"new high-water baseline {scalar:.4f} (was {float(baseline):.4f})"
            elif baseline is None or scalar >= float(baseline):
                updates |= {"stage": LoopStage.passed, "last_decision": LoopDecision.pass_}
                message = f"observation {scalar:.4f} holds baseline"
            else:
                message = f"observation {scalar:.4f} regressed below baseline {float(baseline):.4f}"

            # Mark the POST-merge head processed so the metrics commit itself never
            # re-triggers an observation (the merge moved HEAD past ``head``).
            merged_head = runner._vcs.head_sha()
            state = write_loop_state(
                runner._store,
                runner._root,
                state.model_copy(update=updates).mark_processed(default, merged_head),
                title=message,
            )

        # A re-frozen (instrument-changed) baseline is by definition not a
        # regression: cross-instrument scalars are incomparable.
        regressed = (
            baseline is not None
            and scalar < float(baseline)
            and not (instrument_changed and auto_baseline)
        )
        if regressed:
            assert baseline is not None  # implied by ``regressed``
            redirect = loop_gap_redirect.maybe_redirect_to_gaps(
                runner, state, default, merged_head, scalar, float(baseline), outcome
            )
            if redirect is not None:
                return redirect
        if regressed and runner._arena_enabled and runner._arena_score is not None:
            return heal_prompts_after_regression(runner, state, default, merged_head, scalar)
        if regressed:
            write_loop_state(
                runner._store,
                runner._root,
                state.model_copy(
                    update={"stage": LoopStage.failed, "last_decision": LoopDecision.fail}
                ),
                title="observation regression (arena disabled)",
            )
            return LoopCycleResult(
                acted=True,
                branch=default,
                sha=head,
                decision=LoopDecision.fail,
                scalar=scalar,
                message=message,
            )
        return LoopCycleResult(
            acted=True,
            branch=default,
            sha=head,
            decision=LoopDecision.pass_,
            scalar=scalar,
            message=message,
        )


def observation_hold(runner: "LoopRunner", head: str) -> str | None:
    """Reason to defer this observation, or ``None`` to proceed.

    Two independent guards: a live ingest run (measure the ingest once, at
    its boundary — bounded by staleness so a crashed ingest cannot block
    forever), and a HEAD-stability window (a burst of commits coalesces
    into one eval; active only when ``observe_quiet_seconds`` > 0).
    """
    from knotica.core.ingest_activity import has_active_ingest

    if has_active_ingest(runner._store, stale_after_seconds=runner._ingest_hold_stale_seconds):
        runner._pending_head = None
        return "observation held: ingest in progress"
    if runner._observe_quiet_seconds <= 0.0:
        return None
    now = runner._clock()
    if head != runner._pending_head:
        runner._pending_head = head
        runner._pending_since = now
        return f"observation settling ({runner._observe_quiet_seconds:g}s quiet window)"
    if now - runner._pending_since < runner._observe_quiet_seconds:
        return f"observation settling ({runner._observe_quiet_seconds:g}s quiet window)"
    runner._pending_head = None
    return None


def cadence_hold(runner: "LoopRunner", state: LoopState, now: datetime) -> str | None:
    """Reason to defer this observation eval on cadence grounds, or ``None``.

    Reached only from :func:`observe_default` — never from ``poll_once`` or the
    candidate-gate path, whose evals stay eager always. All-defaults
    (``eval_min_interval_hours == 0`` and ``eval_window is None``) is the
    byte-identical fast path: this returns ``None`` before touching either
    knob, so scheduling is unchanged from pre-cadence behavior.
    """
    if runner._eval_min_interval_hours == 0 and runner._eval_window is None:
        return None
    if runner._eval_min_interval_hours > 0 and state.last_eval_started_at is not None:
        elapsed_hours = (now - state.last_eval_started_at).total_seconds() / 3600.0
        if elapsed_hours < runner._eval_min_interval_hours:
            return (
                f"cadence held: {elapsed_hours:.2f}h since last eval start "
                f"< {runner._eval_min_interval_hours:g}h interval"
            )
    if runner._eval_window is not None and not _within_window(runner, now.time()):
        return (
            f"cadence held: outside eval window {runner._eval_window[0]}-{runner._eval_window[1]}"
        )
    return None


def _within_window(runner: "LoopRunner", now_time: _time_of_day) -> bool:
    """``True`` if ``now_time`` falls inside ``runner._eval_window`` (supports midnight wrap)."""
    start, end = runner._eval_window  # type: ignore[misc]  # guarded by caller
    if start <= end:
        return start <= now_time <= end
    return now_time >= start or now_time <= end


def heal_prompts_after_regression(
    runner: "LoopRunner", state: LoopState, default: str, head: str, scalar: float
) -> "LoopCycleResult":
    """Race prompt variants after a default-branch regression (content stays)."""
    from knotica.core.loop import LoopCycleResult

    baseline = float(state.baseline_scalar or 0.0)
    state = write_loop_state(
        runner._store,
        runner._root,
        state.model_copy(update={"stage": LoopStage.racing}),
        title="arena racing after observation regression",
    )

    def _resolve(arena: ArenaState, *, won: bool) -> LoopCycleResult:
        # The winner's promotion commit moved HEAD; absorb it into the cursor.
        merged_head = runner._vcs.head_sha()
        write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(
                update={
                    "stage": LoopStage.passed if won else LoopStage.failed,
                    "last_decision": LoopDecision.pass_ if won else LoopDecision.fail,
                    "last_error": None if won else arena.message,
                }
            ).mark_processed(default, merged_head),
            title=(
                f"arena healed regression via {arena.winner_id}"
                if won
                else "arena no-winner after regression"
            ),
        )
        return LoopCycleResult(
            acted=True,
            branch=default,
            sha=head,
            decision=LoopDecision.pass_ if won else LoopDecision.fail,
            scalar=float(arena.winner_scalar or scalar) if won else scalar,
            message=(
                f"regression healed: arena winner {arena.winner_id}"
                if won
                else f"regression persists: {arena.message}"
            ),
        )

    return run_arena_and_resolve(
        store=runner._store,
        root=runner._root,
        topic=runner._topic,
        arena_score=runner._arena_score,
        arena_scorer_info=runner._arena_scorer_info,
        arena_variants=runner._arena_variants,
        arena_n=runner._arena_n,
        candidate_branch=None,
        baseline=baseline,
        on_win=lambda arena: _resolve(arena, won=True),
        on_lose=lambda arena: _resolve(arena, won=False),
    )
