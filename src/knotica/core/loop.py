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

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from datetime import time as _time_of_day
from pathlib import Path
from typing import Any, Protocol

from knotica.core import branch_namespaces
from knotica.core.arena import ArenaState, ScoreFn, ScorerInfo, VariantSpec
from knotica.core.arena_resolve import run_arena_and_resolve
from knotica.core.best_effort import best_effort
from knotica.core.loop_state import (
    LoopDecision,
    LoopStage,
    LoopState,
    empty_loop_state,
    eval_records,
    newest_eval_scalar,
    read_loop_state,
    refuse_unreachable,
    write_loop_state,
)
from knotica.core.transaction import VaultTransaction, vault_mutation_span
from knotica.core.vault_layout import SCORED_FAMILIES, family_of
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
DEFAULT_BRANCH_PREFIX: str = branch_namespaces.DEFAULT_BRANCH_PREFIX
RESULT_BRANCH_PREFIX: str = branch_namespaces.RESULT_BRANCH_PREFIX

#: ``log.md`` is an append-only journal: concurrent branches legitimately add
#: different lines at the same location, so it must merge with git's union
#: driver (keep both sides) instead of conflicting. The loop self-heals this
#: attribute into any vault before its first merge.
_GITATTRIBUTES_PATH = ".gitattributes"
_LOG_UNION_RULE = "log.md merge=union"


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
        arena_scorer_info: ScorerInfo | None = None,
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
        # Travels with the callable, never separately: a race that recorded one
        # scorer's provenance beside another's scalars would be worse than
        # recording none. ``None`` means the heuristic default.
        self._arena_scorer_info = arena_scorer_info
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
        """Freeze the gate baseline into loop-state (does not run eval).

        Shares :meth:`rebaseline`'s reachability refusal -- a manual freeze
        above what the corpus measures jams the queue the same way (skipped
        honestly when there is no same-instrument history to compare against).
        ``harness_version`` defaults to the *current* instrument rather than
        ``None``, which would silently disarm both guards keyed on it
        (``compute_gate``'s mismatch branch, ``observe_default``'s re-freeze).
        """
        from knotica.core.gate_inputs import current_harness_version

        instrument = harness_version if harness_version is not None else current_harness_version()
        refuse_unreachable(float(scalar), newest_eval_scalar(self._store, self._topic, instrument))
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        state = state.model_copy(
            update={
                "baseline_scalar": float(scalar),
                "baseline_harness_version": instrument,
                "baseline_corpus_ref": corpus_ref,
                "baseline_golden_manifest_sha": self._golden_manifest_sha(),
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

        The autonomous "observe" leg and the billing boundary every caller
        patches. Thin delegator; the procedure lives in
        :mod:`knotica.core.loop_observe`.
        """
        from knotica.core import loop_observe

        return loop_observe.observe_default(self, auto_baseline=auto_baseline, force=force)

    def _golden_manifest_sha(self) -> str | None:
        """Digest of the golden set a baseline frozen right now was measured on.

        Recorded beside ``baseline_harness_version`` so a later comparison can
        ask "same questions?" as well as "same instrument?". ``None`` when no
        frozen set exists, which downstream reads as unknown, never as matching.
        """
        from knotica.core.gate_inputs import read_golden_manifest_sha

        return read_golden_manifest_sha(self._store, self._topic)

    def hold_preview(self, *, force: bool = False) -> dict[str, Any]:
        """Why a run right now would decline -- read-only, for a two-phase preview.

        Thin delegator; the procedure lives in :mod:`knotica.core.loop_holds`.
        """
        from knotica.core import loop_holds

        return loop_holds.hold_preview(self, force=force)

    def _cadence_hold(self, state: LoopState, now: datetime) -> str | None:
        """Reason to defer this observation eval on cadence grounds, or ``None``.

        Kept as a method rather than called as a free function, because the
        candidate-gate path's contract is that it never consults cadence at
        all -- and the test that proves it spies on this attribute. Thin
        delegator; the procedure lives in :mod:`knotica.core.loop_observe`.
        """
        from knotica.core import loop_observe

        return loop_observe.cadence_hold(self, state, now)

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

        Paths outside :data:`~knotica.core.vault_layout.SCORED_FAMILIES` — today
        the ``notes/`` family — do not count either. Nothing an unscored family
        holds can move the eval scalar, so observing one would bill a full eval
        run for a change that provably cannot alter its result: hand-authoring a
        personal note must never wake the loop. Keying on the scored set rather
        than on ``notes`` specifically means a future unscored family inherits
        the same inertness instead of silently re-billing.
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
            try:
                if family_of(path) not in SCORED_FAMILIES:
                    continue
            except ValueError:  # unclassifiable path: fall through and observe
                pass
            return True
        return False

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

        A ``best`` pick that exceeds the newest measurement is **refused**: a
        bar above what the default branch currently measures fails every
        candidate and arena variant by construction (the exact state
        ``status._baseline_unreachable`` calls "always a misconfiguration"),
        so freezing one knowingly is not a legitimate outcome — a field
        report proved the queue it silently jams. :meth:`set_baseline` shares
        the refusal.
        """
        cleaned = mode.strip().lower()
        if cleaned not in {"latest", "best"}:
            raise ValueError(f"rebaseline mode must be 'latest' or 'best', got {mode!r}")
        records = eval_records(self._store, self._topic)
        if not records:
            raise ValueError(f"topic {self._topic!r} has no metrics history to rebaseline from")
        current_instrument = records[-1].harness_version
        comparable = [r for r in records if r.harness_version == current_instrument]
        chosen = (
            max(comparable, key=lambda r: float(r.scalar)) if cleaned == "best" else comparable[-1]
        )
        refuse_unreachable(float(chosen.scalar), float(comparable[-1].scalar))
        state = read_loop_state(self._store, self._topic) or empty_loop_state(self._topic)
        return write_loop_state(
            self._store,
            self._root,
            state.model_copy(
                update={
                    "baseline_scalar": float(chosen.scalar),
                    "baseline_harness_version": chosen.harness_version,
                    "baseline_corpus_ref": chosen.corpus_ref,
                    "baseline_golden_manifest_sha": self._golden_manifest_sha(),
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
            arena_scorer_info=self._arena_scorer_info,
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

    The base run config resolves from ``[models]``, so an operator's worker/judge
    snapshots reach every eval, unattended ones included; ``**overrides`` still wins.
    """
    from knotica.core.loop_progress import clear_progress, write_progress
    from knotica.core.models_config import resolve_models_config
    from knotica.evals.harness import run_eval

    # Question + substage context persists across events -- an outcome write
    # (which is not itself a substage transition) replays whatever substage
    # was last reported rather than inventing an unrecognized label.
    context: dict[str, int | str] = {
        "current": 0,
        "total": 0,
        "detail": "",
        "substage": "",
        "sub_current": 0,
        "sub_total": 0,
    }

    # Single writer: one lock guards both the accumulated outcomes list and
    # every progress write that reads it, so the read-append-write triple is
    # one atomic unit across dspy's concurrent scoring threads — a lock that
    # only wrapped the write itself would still let two threads interleave
    # their list reads and silently drop an update.
    lock = threading.Lock()
    outcomes: list[dict[str, str]] = []

    def _write_locked() -> None:
        write_progress(
            source_root,
            topic,
            phase="evaluating",
            current=int(context["current"]),
            total=int(context["total"]),
            detail=str(context["detail"]),
            substage=str(context["substage"]),
            sub_current=int(context["sub_current"]),
            sub_total=int(context["sub_total"]),
            examples=list(outcomes),
        )

    def _on_example(current: int, total: int, question: str) -> None:
        with lock:
            context.update(
                current=current,
                total=total,
                detail=question,
                substage="answering",
                sub_current=0,
                sub_total=0,
            )
            _write_locked()

    def _on_substage(substage: str, sub_current: int, sub_total: int) -> None:
        with lock:
            context.update(substage=substage, sub_current=sub_current, sub_total=sub_total)
            _write_locked()

    def _on_outcome(id_: str, status: str, error_class: str, detail: str) -> None:
        with lock:
            outcomes.append(
                {"id": id_, "status": status, "error_class": error_class, "detail": detail}
            )
            _write_locked()

    # Resolve `[models]` HERE, not at the call sites and not in `build_loop_runner`:
    # the watcher, the service daemon, MCP `run_once` and the ingest candidate gate
    # all pass `evaluate=harness_evaluate` explicitly, bypassing the factory's
    # `evaluate=None` default, so a factory-sited fix would reach none of them --
    # this callable is the seam they genuinely share. Without it they scored on the
    # packaged snapshots while `knotica eval` scored on the operator's: two
    # `harness_version` values alternating on one topic, each switch tripping the
    # instrument-changed re-freeze, so the gate re-baselined instead of comparing.
    # Explicit `**overrides` still win -- `run_eval` layers them onto this base via
    # `with_overrides`, the same caller-wins contract `query_engine` gives
    # `[models].query`. One-time cost on an install that HAS a `[models]` table: the
    # first eval after this rotates `harness_version` and re-freezes the baseline
    # once. That is the designed response to an instrument change, NOT a regression,
    # and it replaces the repeated thrash above; a default install is unchanged.
    models_base = resolve_models_config().to_harness_base()

    write_progress(source_root, topic, phase="preparing", detail="clone + golden set")
    try:
        result = run_eval(
            topic,
            source_root=source_root,
            ref=ref,
            config=models_base,
            on_example=_on_example,
            on_substage=_on_substage,
            on_outcome=_on_outcome,
            # mypy resolves this open passthrough against ``run_eval``'s *named*
            # keyword params rather than its own ``**overrides: object`` catch-all.
            **overrides,  # type: ignore[arg-type]
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
# point every external importer of build_loop_runner uses (an accepted,
# deliberate risk of this import-cycle resolution).
from knotica.core.loop_factory import build_loop_runner  # noqa: E402
