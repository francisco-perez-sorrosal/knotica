"""Regression → knowledge-gap redirect: classify a regression, skip a futile arena.

Extracted from :mod:`knotica.core.loop`'s ``LoopRunner`` alongside the observe
leg (:mod:`knotica.core.loop_observe`), the only caller of any of it. The three
functions here form one sequence -- classify the regression, persist whatever
knowledge gaps it names, optionally drain them into staged suggestions -- and
answer one question the observe leg asks once: *is racing prompt variants going
to help, or is the fault a missing page no prompt can recover?*

Free functions taking the driving :class:`~knotica.core.loop.LoopRunner` as an
explicit first parameter, mirroring :mod:`knotica.core.candidate_gate` and
:mod:`knotica.core.source_gate`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from knotica.core.best_effort import best_effort
from knotica.core.loop_state import LoopDecision, LoopStage, LoopState, write_loop_state
from knotica.store import LocalFSStore

if TYPE_CHECKING:
    from knotica.core.gap_classifier import RegressionClassification
    from knotica.core.loop import EvalOutcome, LoopCycleResult, LoopRunner
    from knotica.core.records import GapRecord

__all__ = [
    "classify_and_persist_gaps",
    "maybe_discover_for_gaps",
    "maybe_redirect_to_gaps",
]


def maybe_redirect_to_gaps(
    runner: "LoopRunner",
    state: LoopState,
    default: str,
    head: str,
    scalar: float,
    baseline: float,
    outcome: "EvalOutcome",
) -> "LoopCycleResult | None":
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
    from knotica.core.loop import LoopCycleResult

    def _record_classification_failure(exc: BaseException) -> None:
        write_loop_state(
            runner._store,
            runner._root,
            state.model_copy(update={"last_error": f"gap classification skipped: {exc}"}),
            title="gap classification failed; falling through to arena heal",
        )

    with best_effort(on_error=_record_classification_failure) as attempt:
        classified = classify_and_persist_gaps(runner, outcome, scalar, baseline)
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
    gap_head = runner._vcs.head_sha()
    generation = int(outcome.generation)
    write_loop_state(
        runner._store,
        runner._root,
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


def classify_and_persist_gaps(
    runner: "LoopRunner", outcome: "EvalOutcome", scalar: float, baseline: float
) -> "tuple[RegressionClassification, list[GapRecord]] | None":
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
        manifest = read_regression_manifest(clone_root, runner._topic, generation)
    except FileNotFoundError:
        # No eval-run manifest on this clone (e.g. a fake/test eval, or a
        # generation that wrote none): no diagnostic substrate -- fall
        # through to the unchanged arena heal, byte-identical.
        return None
    if manifest is None:
        return None
    classification = classify_regression(
        store=LocalFSStore(clone_root),
        topic=runner._topic,
        clone_root=clone_root,
        generation=generation,
        manifest=manifest,
        regressed_ids=regressed_ids_from_manifest(manifest),
    )
    records = build_gap_records(
        classification.verdicts,
        topic=runner._topic,
        generation=generation,
        scalar_at_detection=scalar,
        baseline_scalar=baseline,
        prior_generation=prior_generation_of(manifest),
    )
    write_gap_records(runner._store, runner._root, runner._topic, records)
    maybe_discover_for_gaps(runner)
    return classification, records


def maybe_discover_for_gaps(runner: "LoopRunner") -> None:
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
    if not runner._discover_on_regression:
        return
    from knotica.core import gapfill

    with best_effort():
        service = gapfill.build_default_discovery_service()
        gapfill.refresh_suggestions_for_gaps(
            runner._store,
            runner._root,
            runner._topic,
            service=service,
            max_gaps=runner._gapfill_max_gaps,
        )
