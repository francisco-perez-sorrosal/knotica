"""Read-only answer to "would a run right now actually run?".

``observe_default`` consults four guards before it evaluates anything: a live
ingest, the quiet window, the failure-retry floor, and the eval cadence. Any of
them turns a billed, human-confirmed call into a no-op -- and the operator
found out only *after* confirming, because a two-phase preview quoted the cost
of a run it had no way to know would be declined. Two consecutive confirmed
no-ops in one reported session cost a round-trip each and reported ``billed:
true`` for work nobody did.

This module answers the same question from the same predicates, before the
confirm. It is deliberately **not** a second implementation of them: it calls
the runner's own, so the preview cannot drift from the decision it previews.

A free function taking the driving :class:`~knotica.core.loop.LoopRunner` as its
first parameter, mirroring :mod:`knotica.core.candidate_gate` and
:mod:`knotica.core.source_gate` -- both extracted from ``loop.py`` the same way,
for the same reason (td-008: the module is already far over the line ceiling and
must not grow).

**Pure reads.** ``_observation_hold`` is deliberately not called: it maintains
the quiet-window bookkeeping, so consulting it here would let a preview move the
window it is describing. The live-ingest half of that guard is re-read directly,
which is stateless.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from knotica.core.loop_attempt import is_same_content_retry, retry_hold
from knotica.core.loop_state import LoopState, empty_loop_state, read_loop_state

if TYPE_CHECKING:
    from knotica.core.loop import LoopRunner

__all__ = ["hold_preview"]

#: Reported for a live ingest regardless of ``force`` -- it is not pacing.
_INGEST_HOLD = "observation held: ingest in progress"


def hold_preview(runner: "LoopRunner", *, force: bool = False) -> dict[str, Any]:
    """Reasons this run would decline, plus the cadence wait when one applies.

    ``force`` mirrors the argument of the same name on
    :meth:`~knotica.core.loop.LoopRunner.observe_default`: a forced eval clears
    both *pacing* holds (dec-081), so quoting them at a caller about to force
    past them would be noise. The ingest hold is not pacing and is reported
    either way.
    """
    from knotica.core.ingest_activity import has_active_ingest

    state = read_loop_state(runner._store, runner._topic) or empty_loop_state(runner._topic)
    now = runner._now_fn()
    reasons: list[str] = []
    if has_active_ingest(runner._store, stale_after_seconds=runner._ingest_hold_stale_seconds):
        reasons.append(_INGEST_HOLD)
    remaining: float | None = None
    if not force:
        retrying = is_same_content_retry(
            state, runner._vcs.head_sha(), content_changed=runner._content_changed_since
        )
        failure = retry_hold(
            runner._root, runner._topic, state, same_content_retry=retrying, now=now
        )
        if failure is not None:
            reasons.append(failure)
        cadence = runner._cadence_hold(state, now)
        if cadence is not None:
            reasons.append(cadence)
            remaining = _cadence_remaining_seconds(runner, state, now)
    return {"held": bool(reasons), "reasons": reasons, "cadence_remaining_seconds": remaining}


def _cadence_remaining_seconds(
    runner: "LoopRunner", state: LoopState, now: datetime
) -> float | None:
    """Seconds until the minimum-interval floor releases (``None`` when unset).

    The floor's *message* already exists on the runner; this is the number
    behind it, which is what an operator deciding whether to wait actually
    needs.
    """
    if runner._eval_min_interval_hours <= 0 or state.last_eval_started_at is None:
        return None
    elapsed = (now - state.last_eval_started_at).total_seconds()
    return max(0.0, runner._eval_min_interval_hours * 3600.0 - elapsed)
