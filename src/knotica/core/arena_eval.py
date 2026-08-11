"""Eval-backed arena scorer -- score a prompt variant on the gate's own instrument.

The arena's default scorer is a keyword heuristic: free, deterministic, and on
its own scale. That makes it demo-safe and useless for deciding anything against
the gate, which is why :func:`knotica.core.arena.incomparable_reason` refuses to
rank the two. This module is the other option -- a scorer that runs the *real*
golden-set harness per variant, so its scalars are the same measurement the gate
baseline is, and a win means what it appears to mean.

**It bills.** One full eval per variant: a four-variant race over a
twenty-one-question golden set is eighty-four worker+judge pairs. That is why
it is opt-in through ``[loop] arena_scorer = "eval"`` rather than the default,
and why :func:`build_eval_scorer` reports the cost it is about to incur through
the same :class:`~knotica.core.arena.ScorerInfo` the race records.

The variant body reaches the harness through ``run_eval``'s
``instructions_override``, which swaps the clone's ``query.md`` body and touches
nothing else -- same retrieval, same judge, same golden set, same scalar
formula, therefore the same ``harness_version``. Substituting the prompt is
exactly the comparison the arena exists to make; substituting anything else
would break the comparability this module is for.

Lazily imports the eval group, like every other model-calling path in ``core``,
so the lean install (no ``evals`` extra) still imports cleanly and fails with a
typed error only if a race is actually attempted.
"""

from __future__ import annotations

from pathlib import Path

from knotica.core.arena import EVAL_SCORER_ID, ScoreFn, ScorerInfo
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gate_inputs import current_harness_version, read_golden_manifest_sha
from knotica.store import VaultStore

__all__ = ["build_eval_scorer", "estimated_race_calls"]


def build_eval_scorer(
    store: VaultStore,
    topic: str,
    *,
    num_threads: int = 4,
) -> tuple[ScoreFn, ScorerInfo]:
    """A scorer that evaluates a variant prompt on the gate's own instrument.

    Returns the callable and the :class:`ScorerInfo` describing it -- both, so a
    caller can never race with the scorer while recording someone else's
    provenance. The descriptor claims ``comparable_to_eval=True``, which is the
    claim that unlocks ranking against the gate baseline; it is true here
    precisely because only the prompt body differs between this measurement and
    the baseline's.

    Raises:
        KnoticaError: ``NOT_CONFIGURED`` when the topic has no frozen golden
            set. A scorer with nothing to score would otherwise return numbers
            with no measurement behind them -- the failure mode this whole
            module exists to end.
    """
    manifest_sha = read_golden_manifest_sha(store, topic)
    if manifest_sha is None:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            f"the eval-backed arena scorer needs a frozen golden set for topic {topic!r}.",
            fix="Freeze one with `datasets action=freeze`, or set "
            '`[loop] arena_scorer = "heuristic"` to race without eval scoring.',
        )

    def _score(scored_topic: str, vault_root: Path, body: str) -> float:
        from knotica.evals.harness import run_eval

        result = run_eval(
            scored_topic,
            source_root=vault_root,
            num_threads=num_threads,
            instructions_override=body,
        )
        return float(result.record.scalar)

    return _score, ScorerInfo(
        id=EVAL_SCORER_ID,
        comparable_to_eval=True,
        n_examples=_golden_size(store, topic),
        golden_manifest_sha=manifest_sha,
        harness_version=current_harness_version(),
    )


def estimated_race_calls(store: VaultStore, topic: str, *, n_variants: int) -> int | None:
    """Worker+judge call pairs an eval-backed race of ``n_variants`` would make.

    ``None`` when the golden set cannot be sized. For a cost quote only -- the
    arena never gates on this number, it just refuses to hide it.
    """
    size = _golden_size(store, topic)
    return None if size is None else size * max(0, n_variants)


def _golden_size(store: VaultStore, topic: str) -> int | None:
    """Question count of the topic's frozen golden set (``None`` when unreadable)."""
    try:
        from knotica.evals.golden import load as load_golden

        return len(load_golden(store, topic))
    except Exception:  # noqa: BLE001 -- an unsizable set is a missing quote, not a failure
        return None
