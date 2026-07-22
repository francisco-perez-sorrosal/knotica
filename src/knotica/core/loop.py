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

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from datetime import time as _time_of_day
from pathlib import Path
from typing import Protocol

from knotica.core import branch_namespaces
from knotica.core.arena import ArenaState, ScoreFn, VariantSpec
from knotica.core.arena_resolve import run_arena_and_resolve
from knotica.core.best_effort import best_effort
from knotica.core.loop_state import (
    LoopDecision,
    LoopStage,
    LoopState,
    empty_loop_state,
    read_loop_state,
    write_loop_state,
)
from knotica.core.transaction import VaultTransaction, vault_mutation_span
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore, VaultStore

__all__ = [
    "DEFAULT_BRANCH_PREFIX",
    "RESULT_BRANCH_PREFIX",
    "EvalOutcome",
    "EvaluateFn",
    "LoopCycleResult",
    "LoopRunner",
    "build_loop_runner",
    "harness_evaluate",
    "wrap_harness_result",
]

# Re-exported from the branch-namespace single source of truth so the loop's
# historical public names (``loop.DEFAULT_BRANCH_PREFIX`` / ``RESULT_BRANCH_PREFIX``,
# imported by cli/loop, branch_scoreboard, loop_promote, status) keep resolving.
DEFAULT_BRANCH_PREFIX = branch_namespaces.DEFAULT_BRANCH_PREFIX
RESULT_BRANCH_PREFIX = branch_namespaces.RESULT_BRANCH_PREFIX

#: ``log.md`` is an append-only journal: concurrent branches legitimately add
#: different lines at the same location, so it must merge with git's union
#: driver (keep both sides) instead of conflicting. The loop self-heals this
#: attribute into any vault before its first merge.
_GITATTRIBUTES_PATH = ".gitattributes"
_LOG_UNION_RULE = "log.md merge=union"

#: Always-on floor between retries of a *failing* observation eval, independent
#: of ``eval_min_interval_hours``/``eval_window`` (which gate the eager,
#: config-off-by-default path). Without this, a persistently-failing eval at
#: the default config retries every loop tick (5-30s) indefinitely — a real
#: spend/log-noise risk. Applies only to retries of already-``pending_retry``
#: state; a brand-new content change is never held by this floor.
_FAILURE_RETRY_FLOOR_SECONDS = 60


def _local_now() -> datetime:
    """Default ``now_fn``: naive local-clock timestamp (matches ``eval_window`` inputs)."""
    return datetime.now()


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
        arena_enabled: bool = True,
        arena_score: ScoreFn | None = None,
        arena_variants: list[VariantSpec] | None = None,
        arena_n: int = 4,
        discover_on_regression: bool = False,
        max_gaps: int = 5,
        observe_quiet_seconds: float = 0.0,
        ingest_hold_stale_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        eval_min_interval_hours: float = 0.0,
        eval_window: tuple[_time_of_day, _time_of_day] | None = None,
        now_fn: Callable[[], datetime] = _local_now,
    ) -> None:
        self._root = Path(vault_root).resolve()
        self._topic = topic.strip().strip("/")
        self._evaluate = evaluate
        self._prefix = branch_prefix
        self._push_remote = push_remote
        self._store = store if store is not None else LocalFSStore(self._root)
        self._vcs = VaultVcs(self._root)
        self._arena_enabled = arena_enabled
        self._arena_score = arena_score
        self._arena_variants = arena_variants
        self._arena_n = arena_n
        # Opt-in P3 gap-fill batch (default off = byte-identical to pre-P3): when
        # enabled, a regression that persists genuine_gaps also drains them into
        # staged suggestions in its own transaction, capped by ``max_gaps``.
        self._discover_on_regression = discover_on_regression
        self._gapfill_max_gaps = max_gaps
        # Observation debounce (watch mode): a burst of commits — a multi-page
        # ingest, a batch of edits — coalesces into ONE eval at its natural
        # boundary. 0.0 = observe immediately (explicit one-shot invocations).
        self._observe_quiet_seconds = max(0.0, observe_quiet_seconds)
        self._ingest_hold_stale_seconds = ingest_hold_stale_seconds
        self._clock = clock
        self._pending_head: str | None = None
        self._pending_since: float = 0.0
        # Cadence throttle (observe_default only — never the candidate-gate
        # path). All-defaults (0.0 / None) is the byte-identical fast path:
        # ``_cadence_hold`` returns ``None`` unconditionally before touching
        # either knob.
        self._eval_min_interval_hours = eval_min_interval_hours
        self._eval_window = eval_window
        self._now_fn = now_fn

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

    def observe_default(
        self, *, auto_baseline: bool = True, force: bool = False
    ) -> LoopCycleResult:
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
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        default = self._vcs.default_branch()
        head = self._vcs.head_sha()
        cursor = state.cursors.get(default)
        if cursor == head:
            return LoopCycleResult(
                acted=False,
                branch=default,
                sha=head,
                decision=LoopDecision.none,
                scalar=None,
                message="default branch unchanged since last observation",
            )
        if cursor is not None and not self._content_changed_since(cursor, head):
            # Only bookkeeping moved since the cursor (loop-state / metrics / log
            # commits written by the loop itself). Deliberately no state write:
            # the cursor stays put until real content lands, so the loop never
            # commits (or evals) in response to its own writes.
            return LoopCycleResult(
                acted=False,
                branch=default,
                sha=head,
                decision=LoopDecision.none,
                scalar=None,
                message="only loop bookkeeping changed since last observation",
            )

        hold = self._observation_hold(head)
        if hold is not None:
            return LoopCycleResult(
                acted=False,
                branch=default,
                sha=head,
                decision=LoopDecision.none,
                scalar=None,
                message=hold,
            )

        failure_hold = self._failure_retry_hold(state, head, self._now_fn())
        if failure_hold is not None:
            return LoopCycleResult(
                acted=False,
                branch=default,
                sha=head,
                decision=LoopDecision.none,
                scalar=None,
                message=failure_hold,
            )

        if not force:
            cadence_hold = self._cadence_hold(state, self._now_fn())
            if cadence_hold is not None:
                return LoopCycleResult(
                    acted=False,
                    branch=default,
                    sha=head,
                    decision=LoopDecision.none,
                    scalar=None,
                    message=cadence_hold,
                )

        self._ensure_union_log_merge()
        state = write_loop_state(
            self._store,
            self._root,
            state.model_copy(
                update={
                    "stage": LoopStage.evaluating,
                    "candidate_branch": default,
                    "candidate_sha": head,
                    "last_error": None,
                    "last_eval_started_at": self._now_fn(),
                }
            ),
            title=f"observing {default}@{head[:12]}",
        )
        # Pin the eval clone AFTER the state commit above: the live side then has
        # no loop-authored commits the merge would have to reconcile — only
        # concurrent human activity, which the union log.md attribute absorbs.
        eval_ref = self._vcs.head_sha()
        try:
            outcome = self._evaluate(self._topic, self._root, eval_ref)
        except Exception as exc:  # noqa: BLE001 — surface into loop-state, keep runner alive
            # Do NOT mark_processed here: the cursor must stay unadvanced so the
            # next tick still sees content-changed against this same head and
            # re-attempts the eval. last_eval_started_at (set above, before this
            # try block) throttles that retry via _cadence_hold — it does not
            # fire on every tick with no delay. candidate_sha is deliberately
            # KEPT as the failed head (not nulled) so a later tick can tell a
            # same-content retry apart from a genuinely new content change.
            write_loop_state(
                self._store,
                self._root,
                state.model_copy(
                    update={
                        "stage": LoopStage.failed,
                        "last_error": str(exc),
                        "candidate_branch": default,
                        "candidate_sha": head,
                        "pending_retry": True,
                    }
                ),
                title=f"observation eval error on {default}",
            )
            return LoopCycleResult(
                acted=True,
                branch=default,
                sha=head,
                decision=LoopDecision.fail,
                scalar=None,
                message=f"observation eval failed: {exc}",
            )

        # Bring the metrics commit home so the chart reflects the observation.
        # The merge, the post-merge head read, and the cursor-advancing state
        # write are ONE atomic span: a concurrent pass must not move the default
        # branch's HEAD between the merge and ``mark_processed`` (that would mark
        # someone else's commit observed and silently skip a real content change).
        with self._mutation_span():
            result_branch = f"{RESULT_BRANCH_PREFIX}{eval_ref[:12]}"
            self._vcs.fetch_ref_from(outcome.clone_root, "HEAD", result_branch)
            self._vcs.checkout_branch(default)
            self._vcs.merge_branch(result_branch, ff_only=False)
            if self._push_remote:
                self._vcs.push(self._push_remote, default)
            self._prune_result_branches()

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
                    "stage": LoopStage.passed,
                    "last_decision": LoopDecision.pass_,
                }
                message = f"first observation auto-froze baseline at {scalar:.4f}"
            elif instrument_changed and auto_baseline:
                updates |= {
                    "baseline_scalar": scalar,
                    "baseline_harness_version": outcome.harness_version,
                    "baseline_corpus_ref": outcome.corpus_ref,
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
            merged_head = self._vcs.head_sha()
            state = write_loop_state(
                self._store,
                self._root,
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
            redirect = self._maybe_redirect_to_gaps(
                state, default, merged_head, scalar, float(baseline), outcome
            )
            if redirect is not None:
                return redirect
        if regressed and self._arena_enabled and self._arena_score is not None:
            return self._heal_prompts_after_regression(state, default, merged_head, scalar)
        if regressed:
            write_loop_state(
                self._store,
                self._root,
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

    def _observation_hold(self, head: str) -> str | None:
        """Reason to defer this observation, or ``None`` to proceed.

        Two independent guards: a live ingest run (measure the ingest once, at
        its boundary — bounded by staleness so a crashed ingest cannot block
        forever), and a HEAD-stability window (a burst of commits coalesces
        into one eval; active only when ``observe_quiet_seconds`` > 0).
        """
        from knotica.core.ingest_activity import has_active_ingest

        if has_active_ingest(self._store, stale_after_seconds=self._ingest_hold_stale_seconds):
            self._pending_head = None
            return "observation held: ingest in progress"
        if self._observe_quiet_seconds <= 0.0:
            return None
        now = self._clock()
        if head != self._pending_head:
            self._pending_head = head
            self._pending_since = now
            return f"observation settling ({self._observe_quiet_seconds:g}s quiet window)"
        if now - self._pending_since < self._observe_quiet_seconds:
            return f"observation settling ({self._observe_quiet_seconds:g}s quiet window)"
        self._pending_head = None
        return None

    def _failure_retry_hold(self, state: LoopState, head: str, now: datetime) -> str | None:
        """Reason to defer a retry of a *failing* observation eval, or ``None``.

        Always-on and independent of ``eval_min_interval_hours``/``eval_window``
        — the only things it consults are ``state.pending_retry`` (set when the
        previous observation eval raised), ``state.candidate_sha`` (the head
        that failed, kept rather than nulled on the failure path), and elapsed
        time since that eval started. A brand-new content change never has
        ``pending_retry`` set, and different CONTENT than what failed is never
        held here — only a same-content retry is. Content equality (not exact
        sha equality) is the right comparison: the loop's own bookkeeping
        commits (loop-state / metrics / log) move ``head`` between ticks even
        when nothing a human wrote has changed.
        """
        if not state.pending_retry or state.last_eval_started_at is None:
            return None
        if state.candidate_sha is None:
            return None
        if self._content_changed_since(state.candidate_sha, head):
            return None
        elapsed_seconds = (now - state.last_eval_started_at).total_seconds()
        if elapsed_seconds < _FAILURE_RETRY_FLOOR_SECONDS:
            return (
                f"failure retry held: {elapsed_seconds:.0f}s since last eval attempt "
                f"< {_FAILURE_RETRY_FLOOR_SECONDS}s floor"
            )
        return None

    def _cadence_hold(self, state: LoopState, now: datetime) -> str | None:
        """Reason to defer this observation eval on cadence grounds, or ``None``.

        Called only from :meth:`observe_default` — never from :meth:`poll_once`
        or :meth:`_process_candidate`, whose candidate-gate evals stay eager
        always. All-defaults (``eval_min_interval_hours == 0`` and
        ``eval_window is None``) is the byte-identical fast path: this method
        returns ``None`` before touching either knob, so scheduling is
        unchanged from pre-cadence behavior.
        """
        if self._eval_min_interval_hours == 0 and self._eval_window is None:
            return None
        if self._eval_min_interval_hours > 0 and state.last_eval_started_at is not None:
            elapsed_hours = (now - state.last_eval_started_at).total_seconds() / 3600.0
            if elapsed_hours < self._eval_min_interval_hours:
                return (
                    f"cadence held: {elapsed_hours:.2f}h since last eval start "
                    f"< {self._eval_min_interval_hours:g}h interval"
                )
        if self._eval_window is not None and not self._within_window(now.time()):
            return (
                f"cadence held: outside eval window {self._eval_window[0]}-{self._eval_window[1]}"
            )
        return None

    def _within_window(self, now_time: _time_of_day) -> bool:
        """``True`` if ``now_time`` falls inside ``self._eval_window`` (supports midnight wrap)."""
        start, end = self._eval_window  # type: ignore[misc]  # guarded by caller
        if start <= end:
            return start <= now_time <= end
        return now_time >= start or now_time <= end

    def _ensure_union_log_merge(self) -> None:
        """Self-heal the ``log.md merge=union`` attribute into the vault (idempotent)."""
        existing = (
            self._store.read_text(_GITATTRIBUTES_PATH)
            if self._store.exists(_GITATTRIBUTES_PATH)
            else ""
        )
        for line in existing.splitlines():
            parts = line.split()
            if parts and parts[0] == "log.md" and "merge=union" in parts[1:]:
                return
        body = (existing.rstrip() + "\n" if existing.strip() else "") + _LOG_UNION_RULE + "\n"
        with VaultTransaction(
            self._store, self._root, "loop", self._topic, "union-merge attribute for log.md"
        ) as txn:
            txn.write(_GITATTRIBUTES_PATH, body)

    def mark_observed(self) -> LoopState:
        """Adopt the current default-branch HEAD as observed (recovery escape hatch).

        For a vault whose observation was interrupted (crash, killed merge): the
        human reconciles git themselves, then this settles loop-state — cursor
        at HEAD, stage idle — so the watcher does not re-eval history it has
        effectively already measured.
        """
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        default = self._vcs.default_branch()
        head = self._vcs.head_sha()
        return write_loop_state(
            self._store,
            self._root,
            state.model_copy(
                update={
                    "stage": LoopStage.idle,
                    "candidate_branch": None,
                    "candidate_sha": None,
                    "last_error": None,
                }
            ).mark_processed(default, head),
            title=f"mark observed at {head[:12]}",
        )

    def _content_changed_since(self, base: str, head: str) -> bool:
        """Whether any wiki *content* differs between two default-branch points.

        Bookkeeping the loop writes about itself — ``log.md`` and ``.knotica/``
        state (loop-state, metrics, arena, compiled) — never counts as content.
        Prompts (``.knotica/prompts/``) DO count: they are the evolvable
        substrate, and a human prompt edit deserves a fresh observation.
        """
        try:
            changed = self._vcs.changed_paths(base, head)
        except Exception:  # noqa: BLE001 — unknown base (e.g. rewritten history): observe
            return True
        for path in changed:
            if path == "log.md":
                continue
            parts = path.split("/")
            if ".knotica" in parts:
                knotica_idx = parts.index(".knotica")
                inside = parts[knotica_idx + 1 :]
                if inside and inside[0] == "prompts":
                    return True
                continue
            return True
        return False

    def _maybe_redirect_to_gaps(
        self,
        state: LoopState,
        default: str,
        head: str,
        scalar: float,
        baseline: float,
        outcome: EvalOutcome,
    ) -> LoopCycleResult | None:
        """Classify a regression's cause; redirect to a gap record when the arena is futile.

        Every knowledge-cause verdict (``genuine_gap``/``dilution``) is persisted
        as a gap record regardless of route -- a mixed regression still logs its
        knowledge gaps for P3 while the arena heals the prompt-recoverable ones.
        The route only decides whether to *skip* the arena: it returns a fail
        result (arena skipped) only when *every* regressed id is a knowledge cause
        (a missing or displaced reference page that racing prompt variants cannot
        recover). Otherwise returns ``None`` so the caller runs the unchanged
        arena heal: a prompt-recoverable fault in the mix, a manifest without a
        diagnostic delta, or an absent eval-run manifest all fall through. Any
        genuine classifier failure is isolated here and surfaced on loop-state --
        it never blocks the heal path and writes no unverified gap record.

        The classifier reads the eval *clone* store only; gap records are written
        to the live vault so the next observe (bookkeeping-only diff under
        ``.knotica/gaps/``) and the out-of-process P3 reader both see them.
        """

        def _record_classification_failure(exc: BaseException) -> None:
            write_loop_state(
                self._store,
                self._root,
                state.model_copy(update={"last_error": f"gap classification skipped: {exc}"}),
                title="gap classification failed; falling through to arena heal",
            )

        with best_effort(on_error=_record_classification_failure) as attempt:
            classified = self._classify_and_persist_gaps(outcome, scalar, baseline)
            if classified is None:
                return None
            classification, records = classified
        if attempt.failed:
            return None

        if classification.route != "REDIRECT":
            # A prompt-recoverable fault is in the mix: the knowledge gaps are
            # already persisted above; let the caller run the arena heal.
            return None

        # Every regressed id is a knowledge cause -- the arena is futile. Absorb
        # the gap-record commit into the cursor so the next observe sees only
        # bookkeeping under ``.knotica/gaps/`` (this state write is bookkeeping too).
        gap_head = self._vcs.head_sha()
        generation = int(outcome.generation)
        write_loop_state(
            self._store,
            self._root,
            state.model_copy(
                update={"stage": LoopStage.failed, "last_decision": LoopDecision.fail}
            ).mark_processed(default, gap_head),
            title=f"regression redirected to {len(records)} knowledge gaps at gen-{generation}",
        )
        return LoopCycleResult(
            acted=True,
            branch=default,
            sha=head,
            decision=LoopDecision.fail,
            scalar=scalar,
            message=f"regression logged as {len(records)} gaps; arena skipped",
        )

    def _classify_and_persist_gaps(
        self, outcome: EvalOutcome, scalar: float, baseline: float
    ) -> tuple[object, list[object]] | None:
        """Classify a regression from the clone manifest and persist knowledge gaps.

        Returns ``None`` when no diagnostic substrate exists (missing or absent
        eval-run manifest on the clone) -- the caller falls through to the
        unchanged arena heal. Exceptions propagate to the caller's isolation
        boundary; this helper owns only the classify -> build -> write sequence.
        """
        from knotica.core.gap_classifier import (
            build_gap_records,
            classify_regression,
            prior_generation_of,
            read_regression_manifest,
            regressed_ids_from_manifest,
            write_gap_records,
        )

        generation = int(outcome.generation)
        clone_root = outcome.clone_root
        try:
            manifest = read_regression_manifest(clone_root, self._topic, generation)
        except FileNotFoundError:
            # No eval-run manifest on this clone (e.g. a fake/test eval, or a
            # generation that wrote none): no diagnostic substrate -- fall
            # through to the unchanged arena heal, byte-identical.
            return None
        if manifest is None:
            return None
        classification = classify_regression(
            store=LocalFSStore(clone_root),
            topic=self._topic,
            clone_root=clone_root,
            generation=generation,
            manifest=manifest,
            regressed_ids=regressed_ids_from_manifest(manifest),
        )
        records = build_gap_records(
            classification.verdicts,
            topic=self._topic,
            generation=generation,
            scalar_at_detection=scalar,
            baseline_scalar=baseline,
            prior_generation=prior_generation_of(manifest),
        )
        write_gap_records(self._store, self._root, self._topic, records)
        self._maybe_discover_for_gaps()
        return classification, records

    def _maybe_discover_for_gaps(self) -> None:
        """Opt-in: drain the just-written open ``genuine_gap``s into staged suggestions.

        Off by default -- when ``discover_on_regression`` is disabled this returns
        immediately, so the regression path is byte-identical to pre-P3 (no
        ``discovery`` import, no extra commit). When enabled, it runs the P3
        discovery drain for the topic's open ``genuine_gap``s in its **own**
        ``VaultTransaction`` (never piggybacked on the gap-record commit, dec-008),
        capped by ``max_gaps`` (the fixed-budget defense). It is failure-isolated
        exactly like the classifier: a discovery error is swallowed so the heal
        path always proceeds -- the loop-side drain is best-effort bookkeeping, and
        the on-demand ``knotica gapfill discover`` CLI is the error-surfacing path.
        ``gapfill`` is imported lazily (and referenced as a module attribute) so the
        drain stays off the runtime path when the flag is off.
        """
        if not self._discover_on_regression:
            return
        from knotica.core import gapfill

        with best_effort():
            service = gapfill.build_default_discovery_service()
            gapfill.refresh_suggestions_for_gaps(
                self._store,
                self._root,
                self._topic,
                service=service,
                max_gaps=self._gapfill_max_gaps,
            )

    def _heal_prompts_after_regression(
        self, state: LoopState, default: str, head: str, scalar: float
    ) -> LoopCycleResult:
        """Race prompt variants after a default-branch regression (content stays)."""
        baseline = float(state.baseline_scalar or 0.0)
        state = write_loop_state(
            self._store,
            self._root,
            state.model_copy(update={"stage": LoopStage.racing}),
            title="arena racing after observation regression",
        )

        def _resolve(arena: ArenaState, *, won: bool) -> LoopCycleResult:
            # The winner's promotion commit moved HEAD; absorb it into the cursor.
            merged_head = self._vcs.head_sha()
            write_loop_state(
                self._store,
                self._root,
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
            store=self._store,
            root=self._root,
            topic=self._topic,
            arena_score=self._arena_score,
            arena_variants=self._arena_variants,
            arena_n=self._arena_n,
            candidate_branch=None,
            baseline=baseline,
            on_win=lambda arena: _resolve(arena, won=True),
            on_lose=lambda arena: _resolve(arena, won=False),
        )

    def set_baseline_policy(self, policy: str) -> LoopState:
        """Persist the gate policy: ``latest`` (track reality) or ``best`` (ratchet)."""
        cleaned = policy.strip().lower()
        if cleaned not in {"latest", "best"}:
            raise ValueError(f"baseline policy must be 'latest' or 'best', got {policy!r}")
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        return write_loop_state(
            self._store,
            self._root,
            state.model_copy(update={"baseline_policy": cleaned}),
            title=f"baseline policy {cleaned}",
        )

    def rebaseline(self, mode: str = "best") -> LoopState:
        """Re-freeze the baseline from metrics history — no eval, no CLI math.

        ``best`` freezes the high-water mark, ``latest`` the most recent scalar —
        both restricted to records from the *current instrument* (the harness
        version of the newest record), because cross-instrument scalars are
        never comparable.
        """
        from knotica.core.metrics import read_metrics_window

        cleaned = mode.strip().lower()
        if cleaned not in {"latest", "best"}:
            raise ValueError(f"rebaseline mode must be 'latest' or 'best', got {mode!r}")
        window = read_metrics_window(self._store, self._topic)
        records = list(window["records"])
        if not records:
            raise ValueError(f"topic {self._topic!r} has no metrics history to rebaseline from")
        current_instrument = records[-1].harness_version
        comparable = [r for r in records if r.harness_version == current_instrument]
        chosen = (
            max(comparable, key=lambda r: float(r.scalar)) if cleaned == "best" else comparable[-1]
        )
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        return write_loop_state(
            self._store,
            self._root,
            state.model_copy(
                update={
                    "baseline_scalar": float(chosen.scalar),
                    "baseline_harness_version": chosen.harness_version,
                    "baseline_corpus_ref": chosen.corpus_ref,
                    "stage": LoopStage.idle,
                }
            ),
            title=f"rebaseline {cleaned} at {float(chosen.scalar):.4f}",
        )

    def poll_once(self) -> LoopCycleResult:
        """Process at most one unhandled ``loop/*`` tip; no-op when idle."""
        from knotica.core import candidate_gate

        return candidate_gate.poll_once(self)

    def _keep(
        self, state: LoopState, branch: str, sha: str, outcome: EvalOutcome
    ) -> LoopCycleResult:
        """Fetch eval tip → FF-merge onto default branch → mark passed.

        Thin delegator kept on the class: :mod:`knotica.core.source_gate`
        calls ``runner._keep(...)`` directly on a passing source candidate.
        """
        from knotica.core import candidate_gate

        return candidate_gate.keep(self, state, branch, sha, outcome)

    def _race_then_resolve(
        self, state: LoopState, branch: str, sha: str, outcome: EvalOutcome
    ) -> LoopCycleResult:
        """On gate fail: race prompt variants; promote winner or revert candidate."""
        baseline = float(state.baseline_scalar or 0.0)
        state = write_loop_state(
            self._store,
            self._root,
            state.model_copy(
                update={
                    "stage": LoopStage.racing,
                    "last_scalar": float(outcome.scalar),
                    "last_generation": int(outcome.generation),
                    "last_harness_version": outcome.harness_version,
                }
            ),
            title=f"arena racing after {branch}",
        )

        def _drop_candidate() -> None:
            default = self._vcs.default_branch()
            if self._vcs.current_branch() == branch:
                self._vcs.checkout_branch(default)
            self._safe_delete_branch(branch)

        def _on_win(arena: ArenaState) -> LoopCycleResult:
            # Post-race resolve: the winner promotion already moved HEAD (its own
            # transaction); this span brackets the candidate cleanup + state write
            # so a concurrent pass cannot interleave. The race itself ran unlocked.
            with self._mutation_span():
                _drop_candidate()
                write_loop_state(
                    self._store,
                    self._root,
                    state.model_copy(
                        update={
                            "stage": LoopStage.passed,
                            "last_scalar": float(arena.winner_scalar or outcome.scalar),
                            "last_decision": LoopDecision.pass_,
                            "candidate_branch": None,
                            "candidate_sha": None,
                            "last_error": None,
                        }
                    ).mark_processed(branch, sha),
                    title=f"arena healed {branch}",
                )
            return LoopCycleResult(
                acted=True,
                branch=branch,
                sha=sha,
                decision=LoopDecision.pass_,
                scalar=float(arena.winner_scalar or outcome.scalar),
                message=f"arena winner {arena.winner_id}; deleted wound {branch}",
            )

        def _on_lose(arena: ArenaState) -> LoopCycleResult:
            # Post-race resolve (no winner): bracket the candidate cleanup + state
            # write; the race itself ran unlocked on a throwaway clone.
            with self._mutation_span():
                _drop_candidate()
                write_loop_state(
                    self._store,
                    self._root,
                    state.model_copy(
                        update={
                            "stage": LoopStage.failed,
                            "last_decision": LoopDecision.fail,
                            "candidate_branch": None,
                            "candidate_sha": None,
                            "last_error": arena.message,
                        }
                    ).mark_processed(branch, sha),
                    title=f"arena no-winner; reverted {branch}",
                )
            return LoopCycleResult(
                acted=True,
                branch=branch,
                sha=sha,
                decision=LoopDecision.fail,
                scalar=float(outcome.scalar),
                message=f"arena no winner; deleted {branch}",
            )

        return run_arena_and_resolve(
            store=self._store,
            root=self._root,
            topic=self._topic,
            arena_score=self._arena_score,
            arena_variants=self._arena_variants,
            arena_n=self._arena_n,
            candidate_branch=branch,
            baseline=baseline,
            on_win=_on_win,
            on_lose=_on_lose,
        )

    def _mutation_span(self) -> AbstractContextManager[None]:
        """The widened, reentrant flock bracketing this pass's real-vault git span.

        Every contiguous checkout/merge/branch-delete/commit sequence on the live
        vault runs inside one of these so a concurrent pass (a background watcher
        vs. a synchronous gate) cannot interleave its git steps and corrupt the
        tree. Nested acquisitions reuse the held flock; eval and the arena race
        stay outside it (they run on a throwaway clone).
        """
        return vault_mutation_span(self._root)

    def _safe_delete_branch(self, branch: str) -> None:
        """Delete ``branch`` if it still exists."""
        if self._vcs.branch_exists(branch):
            self._vcs.delete_branch(branch, force=True)

    def _prune_result_branches(self, *, keep: int = 5) -> None:
        """Drop merged ``loop/r/*`` audit pointers beyond the newest ``keep``.

        Result branches are already ancestors of the default branch after their
        merge — the history lives in main; the pointers are convenience. Only
        merged pointers are pruned (an unmerged result branch is evidence of an
        interrupted run and is deliberately left for recovery). Best-effort:
        pruning failures never fail the observation that triggered them.
        """
        with best_effort():
            merged = [
                (self._vcs.commit_timestamp(sha), branch)
                for branch, sha in self._vcs.list_branch_tips(RESULT_BRANCH_PREFIX)
                if self._vcs.is_ancestor(sha, "HEAD")
            ]
            merged.sort(reverse=True)
            for _, branch in merged[keep:]:
                self._safe_delete_branch(branch)


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
    """Production evaluate callable — imports evals lazily (keeps MCP cold path clean).

    Streams per-example progress into the vault's runtime progress file (read
    by ``wiki_status``) so a minutes-long eval is watchable, not a black box.
    """
    from knotica.core.loop_progress import clear_progress, write_progress
    from knotica.evals.harness import run_eval

    # Question context persists across substage events (the metric fires
    # "judging" without knowing which question index is in flight).
    context = {"current": 0, "total": 0, "detail": ""}

    def _write(substage: str, sub_current: int, sub_total: int) -> None:
        write_progress(
            source_root,
            topic,
            phase="evaluating",
            current=int(context["current"]),
            total=int(context["total"]),
            detail=str(context["detail"]),
            substage=substage,
            sub_current=sub_current,
            sub_total=sub_total,
        )

    def _on_example(current: int, total: int, question: str) -> None:
        context.update(current=current, total=total, detail=question)
        _write("answering", 0, 0)

    def _on_substage(substage: str, sub_current: int, sub_total: int) -> None:
        _write(substage, sub_current, sub_total)

    write_progress(source_root, topic, phase="preparing", detail="clone + golden set")
    try:
        result = run_eval(
            topic,
            source_root=source_root,
            ref=ref,
            on_example=_on_example,
            on_substage=_on_substage,
            **overrides,
        )
    finally:
        clear_progress(source_root, topic)
    return wrap_harness_result(result)


# Bottom-of-file re-export (not top-level): loop_factory.py top-imports
# LoopRunner / harness_evaluate / _local_now / EvaluateFn from *this* module,
# so importing loop_factory before this module finishes defining those names
# would deadlock the cycle. Placing the import here — after every name
# loop_factory depends on already exists in this module's namespace — makes
# the cycle resolve safely, but only holds because loop.py is the sole entry
# point every external importer of build_loop_runner uses (see SYSTEMS_PLAN.md
# and IMPLEMENTATION_PLAN.md Step 3 for the accepted risk).
from knotica.core.loop_factory import build_loop_runner  # noqa: E402
