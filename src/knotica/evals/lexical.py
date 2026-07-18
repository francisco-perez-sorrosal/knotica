"""Cheap lexical overlap scoring for compile post-eval (baseline vs compiled).

Compile compares runners with the mean-over-golden helper. Cold-start
``baseline_probe`` is separate: a fixed zero UX anchor (see
:mod:`knotica.core.baseline_probe`), not this lexical Q&A path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from knotica.evals.runner import BaselineRunner
    from knotica.store import VaultStore

__all__ = [
    "LEXICAL_GOLDEN_LIMIT",
    "lexical_overlap_score",
    "mean_lexical_runner_score",
    "lexical_pair_score",
]

#: Golden examples scored in one lexical pass (matches compile post-eval).
LEXICAL_GOLDEN_LIMIT = 20


def lexical_overlap_score(gold_answer: str, pred_answer: str) -> float:
    """Token overlap of ``pred_answer`` against gold tokens longer than three chars."""
    gold = (gold_answer or "").lower()
    pred_l = (pred_answer or "").lower()
    tokens = [t for t in gold.split() if len(t) > 3]
    if not tokens:
        return 0.0
    hits = sum(1 for token in tokens if token in pred_l)
    return hits / len(tokens)


def mean_lexical_runner_score(
    runner: BaselineRunner,
    store: VaultStore,
    topic: str,
    golden: list[Any],
    *,
    limit: int = LEXICAL_GOLDEN_LIMIT,
) -> float:
    """Mean lexical score of ``runner`` predictions on up to ``limit`` golden rows."""
    scores: list[float] = []
    for record in golden[:limit]:
        try:
            pred = runner.run(store, topic, record.query)
        except Exception:  # noqa: BLE001 — one bad example should not abort the probe
            scores.append(0.0)
            continue
        gold = (getattr(record, "corrected_answer", None) or record.answer or "").lower()
        scores.append(lexical_overlap_score(gold, pred.answer))
    return sum(scores) / max(1, len(scores))


def lexical_pair_score(
    store: VaultStore,
    topic: str,
    baseline: BaselineRunner,
    compiled: BaselineRunner,
    golden: list[Any],
    *,
    limit: int = LEXICAL_GOLDEN_LIMIT,
) -> tuple[float, float]:
    """Score baseline vs compiled; injectable for tests."""
    return (
        mean_lexical_runner_score(baseline, store, topic, golden, limit=limit),
        mean_lexical_runner_score(compiled, store, topic, golden, limit=limit),
    )
