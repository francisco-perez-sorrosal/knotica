"""Race prompt variants and dispatch on the winner outcome.

Extracted from :mod:`knotica.core.loop` — shared by the post-observation-
regression heal and the post-gate-fail candidate heal, both of which build
variants identically, race with the same ``promote_on_win`` contract, and
compute the winner the same way. They differ only in ``candidate_branch``
(``None`` for a regression heal, the wound branch for a gate-fail heal) and
in what the win/lose branches do afterward — expressed by the two callbacks.

Zero runtime dependency on :mod:`knotica.core.loop`: the ``LoopCycleResult``
return type is only referenced under ``TYPE_CHECKING`` to avoid a runtime
import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from knotica.core.arena import (
    ArenaStage,
    ArenaState,
    ScoreFn,
    VariantSpec,
    generate_variant_bodies,
    load_base_query_body,
    race_variants,
)
from knotica.store import VaultStore

if TYPE_CHECKING:
    from knotica.core.loop import LoopCycleResult

__all__ = ["run_arena_and_resolve"]


def run_arena_and_resolve(
    *,
    store: VaultStore,
    root: Path,
    topic: str,
    arena_score: ScoreFn | None,
    arena_variants: list[VariantSpec] | None,
    arena_n: int,
    candidate_branch: str | None,
    baseline: float,
    on_win: Callable[[ArenaState], "LoopCycleResult"],
    on_lose: Callable[[ArenaState], "LoopCycleResult"],
) -> "LoopCycleResult":
    """Generate prompt variants, race them, and dispatch on the winner outcome.

    The caller writes the ``racing`` state before calling this helper (the
    pre-set state args that genuinely diverge between the two call sites).
    """
    assert arena_score is not None
    variants = arena_variants or generate_variant_bodies(
        load_base_query_body(store, topic),
        n=arena_n,
    )
    arena = race_variants(
        store,
        root,
        topic,
        variants,
        baseline_scalar=baseline,
        score=arena_score,
        candidate_branch=candidate_branch,
        promote_on_win=True,
    )
    won = arena.stage == ArenaStage.completed and arena.winner_id is not None
    return on_win(arena) if won else on_lose(arena)
