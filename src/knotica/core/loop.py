"""Self-improving loop spine — watch → eval → gate → merge / revert.

This is the Phase-3a keep/discard harness without DSPy: a candidate branch
under ``loop/`` is evaluated on a clone, compared to a frozen baseline scalar,
then either fast-forwarded onto the default branch (pass) or discarded (fail).
Runner state is persisted only via :mod:`knotica.core.loop_state` so
``wiki_status`` remains the sole dashboard data path.

The evaluate callable is injectable so tests can drive the spine with a fake
scalar and zero network; production wires :func:`knotica.evals.harness.run_eval`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from knotica.core.loop_state import (
    LoopDecision,
    LoopStage,
    LoopState,
    empty_loop_state,
    read_loop_state,
    write_loop_state,
)
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore, VaultStore

__all__ = [
    "DEFAULT_BRANCH_PREFIX",
    "RESULT_BRANCH_PREFIX",
    "EvalOutcome",
    "EvaluateFn",
    "LoopCycleResult",
    "LoopRunner",
    "harness_evaluate",
    "wrap_harness_result",
]

DEFAULT_BRANCH_PREFIX = "loop/c/"
RESULT_BRANCH_PREFIX = "loop/r/"


class EvalOutcome(Protocol):
    """Minimal surface the runner needs from an eval result."""

    @property
    def scalar(self) -> float: ...

    @property
    def generation(self) -> int: ...

    @property
    def harness_version(self) -> str: ...

    @property
    def corpus_ref(self) -> str: ...

    @property
    def clone_root(self) -> Path: ...


@dataclass(frozen=True, slots=True)
class _SimpleOutcome:
    """Test-friendly eval outcome (also wraps harness records)."""

    scalar: float
    generation: int
    harness_version: str
    corpus_ref: str
    clone_root: Path


EvaluateFn = Callable[[str, Path, str | None], EvalOutcome]


@dataclass(frozen=True, slots=True)
class LoopCycleResult:
    """One processed candidate tip (or a no-op poll)."""

    acted: bool
    branch: str | None
    sha: str | None
    decision: LoopDecision
    scalar: float | None
    message: str


class LoopRunner:
    """Orchestrate one topic's keep/discard loop against a vault root."""

    def __init__(
        self,
        vault_root: str | Path,
        topic: str,
        *,
        evaluate: EvaluateFn,
        branch_prefix: str = DEFAULT_BRANCH_PREFIX,
        push_remote: str | None = None,
        store: VaultStore | None = None,
    ) -> None:
        self._root = Path(vault_root).resolve()
        self._topic = topic.strip().strip("/")
        self._evaluate = evaluate
        self._prefix = branch_prefix
        self._push_remote = push_remote
        self._store = store if store is not None else LocalFSStore(self._root)
        self._vcs = VaultVcs(self._root)

    def set_baseline(
        self,
        scalar: float,
        *,
        harness_version: str | None = None,
        corpus_ref: str | None = None,
    ) -> LoopState:
        """Freeze the gate baseline into loop-state (does not run eval)."""
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        state = state.model_copy(
            update={
                "baseline_scalar": float(scalar),
                "baseline_harness_version": harness_version,
                "baseline_corpus_ref": corpus_ref,
                "stage": LoopStage.idle,
            }
        )
        return write_loop_state(
            self._store, self._root, state, title=f"freeze baseline {scalar:.4f}"
        )

    def poll_once(self) -> LoopCycleResult:
        """Process at most one unhandled ``loop/*`` tip; no-op when idle."""
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        if state.baseline_scalar is None:
            return LoopCycleResult(
                acted=False,
                branch=None,
                sha=None,
                decision=LoopDecision.none,
                scalar=None,
                message="no baseline frozen; call set_baseline first",
            )

        pending = self._next_candidate(state)
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
        return self._process_candidate(state, branch, sha)

    def _next_candidate(self, state: LoopState) -> tuple[str, str] | None:
        """First ``prefix*`` tip whose SHA is not in ``state.cursors``."""
        default = self._vcs.default_branch()
        for branch, sha in self._vcs.list_branch_tips(self._prefix):
            if branch == default:
                continue
            if state.cursors.get(branch) == sha:
                continue
            return branch, sha
        return None

    def _process_candidate(self, state: LoopState, branch: str, sha: str) -> LoopCycleResult:
        """Evaluate → gate → merge or revert one candidate tip."""
        state = write_loop_state(
            self._store,
            self._root,
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
            outcome = self._evaluate(self._topic, self._root, sha)
        except Exception as exc:  # noqa: BLE001 — surface into loop-state, keep runner alive
            write_loop_state(
                self._store,
                self._root,
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

        passed = float(outcome.scalar) >= float(state.baseline_scalar or 0.0)
        if passed:
            return self._keep(state, branch, sha, outcome)
        return self._discard(state, branch, sha, outcome)

    def _keep(
        self, state: LoopState, branch: str, sha: str, outcome: EvalOutcome
    ) -> LoopCycleResult:
        """Fetch eval tip → FF-merge onto default branch → mark passed."""
        state = write_loop_state(
            self._store,
            self._root,
            state.model_copy(update={"stage": LoopStage.merging}),
            title=f"merging {branch}",
        )
        default = self._vcs.default_branch()
        result_branch = f"{RESULT_BRANCH_PREFIX}{sha[:12]}"
        # Pull the clone tip (includes the eval metrics commit) onto the source.
        self._vcs.fetch_ref_from(outcome.clone_root, "HEAD", result_branch)
        self._vcs.checkout_branch(default)
        self._vcs.merge_branch(result_branch, ff_only=False)
        # Candidate is consumed; drop it so the watch does not re-fire.
        self._safe_delete_branch(branch)
        if self._push_remote:
            self._vcs.push(self._push_remote, default)
            self._vcs.push(self._push_remote, result_branch)

        state = write_loop_state(
            self._store,
            self._root,
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

    def _discard(
        self, state: LoopState, branch: str, sha: str, outcome: EvalOutcome
    ) -> LoopCycleResult:
        """Delete the candidate branch; leave default branch untouched."""
        state = write_loop_state(
            self._store,
            self._root,
            state.model_copy(update={"stage": LoopStage.reverting}),
            title=f"reverting {branch}",
        )
        default = self._vcs.default_branch()
        if self._vcs.current_branch() == branch:
            self._vcs.checkout_branch(default)
        self._safe_delete_branch(branch)

        write_loop_state(
            self._store,
            self._root,
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

    def _safe_delete_branch(self, branch: str) -> None:
        """Delete ``branch`` if it still exists."""
        if self._vcs.branch_exists(branch):
            self._vcs.delete_branch(branch, force=True)


def wrap_harness_result(result: object) -> EvalOutcome:
    """Adapt a :class:`~knotica.evals.harness.EvalRunResult` into :class:`EvalOutcome`."""
    record = getattr(result, "record")
    return _SimpleOutcome(
        scalar=float(record.scalar),
        generation=int(record.generation),
        harness_version=str(record.harness_version),
        corpus_ref=str(record.corpus_ref),
        clone_root=Path(getattr(result, "clone_root")),
    )


def harness_evaluate(
    topic: str,
    source_root: Path,
    ref: str | None,
    **overrides: object,
) -> EvalOutcome:
    """Production evaluate callable — imports evals lazily (keeps MCP cold path clean)."""
    from knotica.evals.harness import run_eval

    result = run_eval(topic, source_root=source_root, ref=ref, **overrides)
    return wrap_harness_result(result)
