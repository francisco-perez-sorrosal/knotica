"""Candidate-gate path -- poll, classify, evaluate, gate one ``loop/*`` tip.

Extracted from :mod:`knotica.core.loop`'s ``LoopRunner`` methods
(``poll_once``/``_next_candidate``/``_process_candidate``/``_keep``/``_discard``)
to keep the already-large ``loop.py`` (td-008) from growing further. Free
functions taking the driving :class:`~knotica.core.loop.LoopRunner` as an
explicit first parameter, mirroring the precedent set by
:mod:`knotica.core.source_gate`.

``poll_once`` and ``keep`` stay reachable from ``LoopRunner`` through thin
delegator methods on the class itself: ``poll_once`` is the runner's public
API, and ``_keep`` is called directly by
:func:`knotica.core.source_gate.handle_source_pass` as ``runner._keep(...)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from knotica.core.branch_namespaces import RESULT_BRANCH_PREFIX
from knotica.core.loop import LoopCycleResult
from knotica.core.loop_state import (
    LoopDecision,
    LoopStage,
    LoopState,
    empty_loop_state,
    read_loop_state,
    write_loop_state,
)

if TYPE_CHECKING:
    from knotica.core.loop import EvalOutcome, LoopRunner

__all__ = [
    "discard",
    "keep",
    "next_candidate",
    "poll_once",
    "process_candidate",
]


def poll_once(runner: "LoopRunner") -> LoopCycleResult:
    """Process at most one unhandled ``loop/*`` tip; no-op when idle."""
    state = read_loop_state(runner._store, runner._topic) or empty_loop_state(runner._topic)
    if state.baseline_scalar is None:
        return LoopCycleResult(
            acted=False,
            branch=None,
            sha=None,
            decision=LoopDecision.none,
            scalar=None,
            message="no baseline frozen; call set_baseline first",
        )

    pending = next_candidate(runner, state)
    if pending is None:
        return LoopCycleResult(
            acted=False,
            branch=None,
            sha=None,
            decision=LoopDecision.none,
            scalar=None,
            message="no pending loop branches",
        )

    branch, sha = pending
    return process_candidate(runner, state, branch, sha)


def next_candidate(runner: "LoopRunner", state: LoopState) -> tuple[str, str] | None:
    """First ``prefix*`` tip whose SHA is not in ``state.cursors``."""
    default = runner._vcs.default_branch()
    for branch, sha in runner._vcs.list_branch_tips(runner._prefix):
        if branch == default:
            continue
        if state.cursors.get(branch) == sha:
            continue
        return branch, sha
    return None


def process_candidate(
    runner: "LoopRunner", state: LoopState, branch: str, sha: str
) -> LoopCycleResult:
    """Evaluate → gate → merge or revert one candidate tip."""
    runner._ensure_union_log_merge()
    state = write_loop_state(
        runner._store,
        runner._root,
        state.model_copy(
            update={
                "stage": LoopStage.evaluating,
                "candidate_branch": branch,
                "candidate_sha": sha,
                "last_error": None,
            }
        ),
        title=f"evaluating {branch}",
    )

    try:
        outcome = runner._evaluate(runner._topic, runner._root, sha)
    except Exception as exc:  # noqa: BLE001 — surface into loop-state, keep runner alive
        write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(
                update={
                    "stage": LoopStage.failed,
                    "last_error": str(exc),
                    "last_decision": LoopDecision.fail,
                }
            ).mark_processed(branch, sha),
            title=f"eval error on {branch}",
        )
        return LoopCycleResult(
            acted=True,
            branch=branch,
            sha=sha,
            decision=LoopDecision.fail,
            scalar=None,
            message=f"eval failed: {exc}",
        )

    # A source candidate (an ingested gap-fill source, named
    # ``loop/c/<topic>/source-<id8>``) is gated separately and is NEVER raced
    # through the arena: content dilution is not prompt-fixable, and racing
    # could surface a prompt that masks it. The orchestration lives in
    # ``source_gate`` to keep it out of this file.
    from knotica.core import source_gate

    if source_gate.classify_candidate(branch) == "source":
        return source_gate.gate_source_candidate(runner, state, branch, sha, outcome)

    passed = float(outcome.scalar) >= float(state.baseline_scalar or 0.0)
    if passed:
        return runner._keep(state, branch, sha, outcome)
    if runner._arena_enabled and runner._arena_score is not None:
        return runner._race_then_resolve(state, branch, sha, outcome)
    return discard(runner, state, branch, sha, outcome)


def keep(
    runner: "LoopRunner", state: LoopState, branch: str, sha: str, outcome: "EvalOutcome"
) -> LoopCycleResult:
    """Fetch eval tip → FF-merge onto default branch → mark passed."""
    # One atomic span: the fetch/checkout/merge/delete sequence and the
    # pass-recording state write must not interleave with a concurrent pass's
    # own git steps on this working tree (reentrant when the source gate calls
    # this inside its own span).
    with runner._mutation_span():
        state = write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(update={"stage": LoopStage.merging}),
            title=f"merging {branch}",
        )
        default = runner._vcs.default_branch()
        result_branch = f"{RESULT_BRANCH_PREFIX}{sha[:12]}"
        # Pull the clone tip (includes the eval metrics commit) onto the source.
        runner._vcs.fetch_ref_from(outcome.clone_root, "HEAD", result_branch)
        runner._vcs.checkout_branch(default)
        runner._vcs.merge_branch(result_branch, ff_only=False)
        # Candidate is consumed; drop it so the watch does not re-fire.
        runner._safe_delete_branch(branch)
        if runner._push_remote:
            runner._vcs.push(runner._push_remote, default)
            runner._vcs.push(runner._push_remote, result_branch)
        runner._prune_result_branches()

        state = write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(
                update={
                    "stage": LoopStage.passed,
                    "last_scalar": float(outcome.scalar),
                    "last_generation": int(outcome.generation),
                    "last_harness_version": outcome.harness_version,
                    "last_decision": LoopDecision.pass_,
                    "candidate_branch": None,
                    "candidate_sha": None,
                    "last_error": None,
                }
            ).mark_processed(branch, sha),
            title=f"kept {branch} scalar={outcome.scalar:.4f}",
        )
    return LoopCycleResult(
        acted=True,
        branch=branch,
        sha=sha,
        decision=LoopDecision.pass_,
        scalar=float(outcome.scalar),
        message=f"passed gate; merged {result_branch} into {default}",
    )


def discard(
    runner: "LoopRunner", state: LoopState, branch: str, sha: str, outcome: "EvalOutcome"
) -> LoopCycleResult:
    """Delete the candidate branch; leave default branch untouched."""
    # One atomic span: the checkout/delete and the fail-recording state write
    # must not interleave with a concurrent pass's git steps on this tree.
    with runner._mutation_span():
        state = write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(update={"stage": LoopStage.reverting}),
            title=f"reverting {branch}",
        )
        default = runner._vcs.default_branch()
        if runner._vcs.current_branch() == branch:
            runner._vcs.checkout_branch(default)
        runner._safe_delete_branch(branch)

        write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(
                update={
                    "stage": LoopStage.failed,
                    "last_scalar": float(outcome.scalar),
                    "last_generation": int(outcome.generation),
                    "last_harness_version": outcome.harness_version,
                    "last_decision": LoopDecision.fail,
                    "candidate_branch": None,
                    "candidate_sha": None,
                    "last_error": None,
                }
            ).mark_processed(branch, sha),
            title=f"reverted {branch} scalar={outcome.scalar:.4f}",
        )
    return LoopCycleResult(
        acted=True,
        branch=branch,
        sha=sha,
        decision=LoopDecision.fail,
        scalar=float(outcome.scalar),
        message=f"failed gate; deleted {branch}",
    )
