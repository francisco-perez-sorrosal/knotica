"""Factory for constructing :class:`~knotica.core.loop.LoopRunner`.

Split out of ``loop.py`` (td-008 cohesion pass) as a verbatim move — see
``loop.py``'s bottom-of-file re-export for the import-ordering rationale.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from datetime import time as _time_of_day
from pathlib import Path

from knotica.core.arena import ScoreFn, VariantSpec, heuristic_arena_score
from knotica.core.gapfill_config import GapfillHookConfig
from knotica.core.loop import EvaluateFn, LoopRunner, _local_now, harness_evaluate
from knotica.core.loop import DEFAULT_BRANCH_PREFIX
from knotica.core.loop_cadence_config import resolve_loop_cadence_config
from knotica.store import VaultStore


def build_loop_runner(
    vault: str | Path,
    topic: str,
    *,
    evaluate: EvaluateFn | None = None,
    store: VaultStore | None = None,
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    push_remote: str | None = None,
    arena_enabled: bool = True,
    arena_score: ScoreFn | None = None,
    arena_variants: list[VariantSpec] | None = None,
    arena_n: int = 4,
    gapfill_config: GapfillHookConfig | None = None,
    observe_quiet_seconds: float = 0.0,
    ingest_hold_stale_seconds: float = 600.0,
    clock: Callable[[], float] = time.monotonic,
    eval_min_interval_hours: float | None = None,
    eval_window: tuple[_time_of_day, _time_of_day] | None = None,
    now_fn: Callable[[], datetime] = _local_now,
    runner_cls: type[LoopRunner] = LoopRunner,
) -> LoopRunner:
    """Construct a :class:`LoopRunner`, the single factory both construction sites share.

    The background watcher (``cli/loop.py``) and the synchronous MCP gate
    (``mcp_server/tools_source_ingest.py``) once built runners independently, with
    no shared seam. This factory unifies **construction** while leaving each site's
    **effective config values** intact: every knob a caller omits falls through to
    the same default the raw ``LoopRunner`` would have used, so the watcher's 20s
    quiet window and the gate's immediate-observe default remain divergent by design
    (value convergence is a separate, deferred decision). Three knobs are deliberate
    exceptions, resolved *here* when omitted rather than passed through:
    ``eval_min_interval_hours``, ``eval_window`` and ``arena_score``. Each is inert
    at its raw default and each was in fact forgotten by real call sites -- see the
    comments in the body for what that silently cost.

    ``gapfill_config`` folds the two loop-side gap-fill knobs
    (``discover_on_regression`` / ``max_gaps``) into one object: pass a resolved
    :class:`GapfillHookConfig` (the watcher does) or ``None`` for the off-by-default
    settings (the gate does). ``evaluate`` defaults to :func:`harness_evaluate` when
    omitted. ``runner_cls`` is a construction seam: a caller passes the (possibly
    test-substituted) class bound in its own module so an existing monkeypatch on
    that binding continues to intercept construction through the factory.
    """
    gapfill = gapfill_config if gapfill_config is not None else GapfillHookConfig()
    # Resolve the [loop] cadence here rather than at each call site. Both real
    # sites (the watcher and the service daemon) previously omitted it, so the
    # documented `eval_min_interval_hours` silently ran at the 0.0 default and
    # never throttled anything -- config that parses, validates, and is editable
    # through an MCP tool, yet reaches no runner. Defaulting in the one shared
    # factory makes forgetting it impossible; an explicit value still wins, which
    # is what tests and `--eval-*` style overrides pass.
    if eval_min_interval_hours is None or eval_window is None:
        cadence = resolve_loop_cadence_config()
        if eval_min_interval_hours is None:
            eval_min_interval_hours = cadence.eval_min_interval_hours
        if eval_window is None:
            # `eval_window` is the same knob's sibling: same `[loop]` table, same
            # resolver, and fully implemented downstream (`LoopRunner._cadence_hold`
            # / `_within_window`, midnight wrap included) -- but it was resolved
            # nowhere, so no call site ever handed one to a runner and the
            # documented window held nothing back. Same treatment for the same
            # reason; an explicit window still wins.
            eval_window = cadence.parsed_window()
    # The same trap one knob over: `arena_enabled` defaults True while
    # `arena_score` defaults None, and both guards that gate the arena
    # (`LoopRunner`'s regression branch and `candidate_gate`'s gate-fail branch)
    # require BOTH -- so the bare signature reads "arena on" and behaves "arena
    # off". The service daemon and `loop action=run_eval` both built runners that
    # way, and a regression there recorded "observation regression (arena
    # disabled)" instead of healing. Defaulting the scorer here makes that
    # omission unexpressible; `--no-arena` still wins because it flips
    # `arena_enabled`, and an explicit scorer still wins.
    if arena_enabled and arena_score is None:
        arena_score = heuristic_arena_score
    return runner_cls(
        vault,
        topic,
        evaluate=evaluate if evaluate is not None else harness_evaluate,
        store=store,
        branch_prefix=branch_prefix,
        push_remote=push_remote,
        arena_enabled=arena_enabled,
        arena_score=arena_score,
        arena_variants=arena_variants,
        arena_n=arena_n,
        discover_on_regression=gapfill.discover_on_regression,
        max_gaps=gapfill.max_gaps,
        observe_quiet_seconds=observe_quiet_seconds,
        ingest_hold_stale_seconds=ingest_hold_stale_seconds,
        clock=clock,
        eval_min_interval_hours=eval_min_interval_hours,
        eval_window=eval_window,
        now_fn=now_fn,
    )
