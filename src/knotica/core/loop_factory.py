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

from knotica.core.arena import ScoreFn, VariantSpec
from knotica.core.gapfill_config import GapfillHookConfig
from knotica.core.loop import EvaluateFn, LoopRunner, _local_now, harness_evaluate
from knotica.core.loop import DEFAULT_BRANCH_PREFIX
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
    eval_min_interval_hours: float = 0.0,
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
    (value convergence is a separate, deferred decision).

    ``gapfill_config`` folds the two loop-side gap-fill knobs
    (``discover_on_regression`` / ``max_gaps``) into one object: pass a resolved
    :class:`GapfillHookConfig` (the watcher does) or ``None`` for the off-by-default
    settings (the gate does). ``evaluate`` defaults to :func:`harness_evaluate` when
    omitted. ``runner_cls`` is a construction seam: a caller passes the (possibly
    test-substituted) class bound in its own module so an existing monkeypatch on
    that binding continues to intercept construction through the factory.
    """
    gapfill = gapfill_config if gapfill_config is not None else GapfillHookConfig()
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
