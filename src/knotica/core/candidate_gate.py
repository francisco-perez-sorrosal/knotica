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

from knotica.core.branch_namespaces import (
    QUARANTINE_BRANCH_PREFIX,
    RESULT_BRANCH_PREFIX,
    WIP_BRANCH_PREFIX,
)
from knotica.core.best_effort import best_effort
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import LoopCycleResult
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
            message=_idle_reason(runner, state),
        )

    branch, sha = pending
    return process_candidate(runner, state, branch, sha)


def _merge_or_leave_clean(
    runner: "LoopRunner", state: LoopState, branch: str, result_branch: str
) -> None:
    """Merge the eval tip onto the default branch, or leave no wreckage behind.

    ``git merge`` on a conflict exits non-zero **and leaves the working tree
    mid-merge** -- ``MERGE_HEAD`` set, conflict markers written into tracked
    files. Before this, that state simply propagated: the exception unwound out
    of the cycle and the live vault sat conflicted until some later span called
    :meth:`~knotica.core.vcs.VaultVcs.heal_git_mutation_state`. For an
    unattended watcher that is the wrong shape of failure -- the next thing to
    touch the vault (Obsidian, an MCP tool, a human) sees a broken tree, and
    nothing says why.

    Observed on a real ingest: a candidate branched before the default branch
    gained a metrics generation evaluates to a **colliding** generation number,
    so ``metrics.jsonl`` and ``eval-runs/gen-N/manifest.json`` both conflict on
    merge. The candidate's *content* merged cleanly; only the loop's own
    bookkeeping collided.

    So the merge failure is caught, the merge aborted, the cycle recorded as
    failed, and a typed error raised naming the cause and the way out. The
    candidate branch is left intact and unhandled, so nothing is lost -- a
    refreshed candidate can be re-submitted.
    """
    try:
        runner._vcs.merge_branch(result_branch, ff_only=False)
        return
    except Exception as exc:
        conflicted = _abort_and_report(runner)
        write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(
                update={
                    "stage": LoopStage.failed,
                    "last_decision": LoopDecision.fail,
                    "last_error": f"merge of {result_branch} conflicted: {conflicted}",
                }
            ),
            title=f"merge conflict on {branch}",
        )
        raise KnoticaError(
            ErrorCode.GIT_ERROR,
            f"merging {result_branch!r} onto the default branch conflicted on "
            f"{conflicted or 'unknown paths'}; the merge was aborted and the vault "
            "left clean. The candidate branch is untouched and still pending.",
            fix=(
                "This happens when the candidate was branched before the default "
                "branch gained a metrics generation, so the candidate's eval writes "
                "a colliding generation number. Refresh the candidate against the "
                "default branch and re-submit."
            ),
        ) from exc


def _abort_and_report(runner: "LoopRunner") -> str:
    """Abort the in-flight merge; return the conflicted paths for the message.

    Best-effort by necessity: this runs on the failure path, and an abort that
    itself fails must not replace the merge error with its own.
    """
    conflicted: list[str] = []
    with best_effort():
        conflicted = runner._vcs.unmerged_paths()
    with best_effort():
        if runner._vcs.is_merge_in_progress():
            runner._vcs.abort_merge()
    return ", ".join(conflicted)


def _idle_reason(runner: "LoopRunner", state: LoopState) -> str:
    """Say *why* there is nothing to gate, not merely that there is nothing.

    "no pending loop branches" covered four different situations, and the
    operator-facing ones are the situations where work exists and the loop
    cannot see it. An unsubmitted ingest and a refused candidate awaiting
    rework both read, to that message, exactly like an idle topic -- so the
    reported session showed ``refused_awaiting_rework: 1`` beside a loop
    reporting nothing pending, with no surface explaining the difference.

    The invisibility itself is correct and is not changed here: ``loop/wip/`` is
    private until :func:`~knotica.core.source_ingest.publish_ingest` renames it,
    which is what guarantees the gate never evaluates a half-written candidate.
    Only the silence about it was wrong.
    """
    with best_effort():
        if any(True for _ in runner._vcs.list_branch_tips(WIP_BRANCH_PREFIX)):
            return (
                "no pending loop branches; an ingest session is open but not submitted "
                "(finish it with source_ingest_submit mode=apply -- a WIP candidate is "
                "deliberately invisible to the gate until then)"
            )
        if any(True for _ in runner._vcs.list_branch_tips(QUARANTINE_BRANCH_PREFIX)):
            return (
                "no pending loop branches; a refused candidate is quarantined and awaiting "
                "rework (re-open it with source_ingest_open to resume, then resubmit)"
            )
        if any(
            branch != runner._vcs.default_branch()
            for branch, _sha in runner._vcs.list_branch_tips(runner._prefix)
        ):
            return "no pending loop branches; every candidate has already been gated"
    return "no pending loop branches"


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

    with discarded_clone(outcome.clone_root):
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
        _merge_or_leave_clean(runner, state, branch, result_branch)
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
