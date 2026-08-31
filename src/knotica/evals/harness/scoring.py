"""From per-example triples to the one topic scalar, via the topic's frozen budget.

Three stages in the order the run applies them, which is why they share a module:

1. :func:`_per_example_breakdown` re-derives each example's QA and citation legs
   from the same ``(gold, prediction)`` the scorer saw -- the judge grade off the
   warm cache (zero LLM calls), citation validity deterministically.
2. :func:`_resolve_budget` reads the topic's ``T_target``, frozen once at
   generation 0 and read back unchanged forever after, so the token-cost discount
   has a fixed reference across the topic's whole history.
3. :func:`_compose_scalar` folds mean quality, topic-attributable lint violations,
   and the measured-vs-budget token ratio into the single stable number.

The scalar formula itself lives in :mod:`knotica.evals.scalar`; this module supplies
its inputs and re-derives the record's component breakdown alongside it.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from knotica.core.lint import lint_vault, topic_of_violation
from knotica.core.links import iter_page_paths
from knotica.core.records import MetricsComponents
from knotica.evals import citations, judge, scalar
from knotica.evals.cache import ResponseCache
from knotica.evals.config import HarnessConfig
from knotica.evals.harness.accounting import _UsageAccountingClient
from knotica.evals.harness.evaluate import _EvalTriple
from knotica.evals.harness.paths import _eval_toml_path
from knotica.evals.runner import Prediction
from knotica.store import VaultStore

#: The schema-overlay filename excluded when counting a topic's content pages
#: (mirrors ``core.lint``'s content-page rule; kept a private constant per the
#: codebase convention of not importing a sibling module's private symbol).
_SCHEMA_OVERLAY_FILENAME = "SCHEMA.md"


@dataclass(frozen=True, slots=True)
class _ExampleBreakdown:
    """One golden example's scored components, re-derived for the record + manifest.

    ``id`` is the golden ``QARecord.id`` -- the edit-stable join key a later
    generation keys its per-question comparison on (rather than fragile question
    text). ``pages`` is the runner's ordered retrieval trace (rank = index), in
    ``QARecord.pages_used`` form, so a consumer can attribute a regression to a
    retrieval change.
    """

    id: str
    pages: tuple[str, ...]
    question: str
    qa_accuracy: float
    citation_validity: float
    quality: float
    total_tokens: int


@dataclass(frozen=True, slots=True)
class _Budget:
    """The token-cost budget for one run: measured ``T`` vs frozen ``T_target``."""

    T: float
    T_target: float
    newly_frozen: bool


def _per_example_breakdown(
    client: _UsageAccountingClient,
    store: VaultStore,
    topic: str,
    run_cache: ResponseCache,
    config: HarnessConfig,
    results: Sequence[_EvalTriple],
) -> list[_ExampleBreakdown]:
    """Re-derive each example's QA and citation components for the record + manifest.

    ``EvaluationResult.results`` carries only the composed quality per example,
    so the two legs are recovered here from the same ``(gold, prediction)``: the
    judge grade from the warm cache (a hit -- zero LLM calls, zero tokens) and
    deterministic citation validity. The recovered legs are faithful to what the
    scalar used by construction (identical deterministic functions + identical
    cached judge medians).
    """
    breakdown: list[_ExampleBreakdown] = []
    for gold, prediction, quality in results:
        qa_accuracy = judge.grade(
            client,
            config.judge_snapshot,
            gold.question,
            prediction.answer,
            gold.reference_answer,
            n=config.n_judge_samples,
            cache=run_cache,
        )
        breakdown.append(
            _ExampleBreakdown(
                id=gold.id,
                pages=tuple(prediction.pages),
                question=gold.question,
                qa_accuracy=qa_accuracy,
                citation_validity=_citation_validity(store, topic, gold, prediction),
                quality=float(quality),
                total_tokens=prediction.usage.total_tokens,
            )
        )
    return breakdown


def _citation_validity(store: VaultStore, topic: str, gold: Any, prediction: Prediction) -> float:
    """Deterministic citation validity with the scorer's reference-aware guard.

    Mirrors the guard the scorer applies (kept here rather than importing the
    scorer's private helper, per the codebase's no-cross-module-private-import
    convention): when the golden reference itself carries citations and the
    candidate cites nothing, the leg is ``0.0`` rather than the vacuous ``1.0``
    -- closing the citation-dropping reward-hacking vector. Otherwise it
    delegates to :func:`knotica.evals.citations.integrity`. This feeds only the
    record's ``citation_validity`` component; the scalar itself uses the quality
    already composed by the scorer in ``.results``.
    """
    if gold.citations and not prediction.citations:
        return 0.0
    return citations.integrity(store, topic, prediction)


def _compose_scalar(
    store: VaultStore, topic: str, breakdown: Sequence[_ExampleBreakdown], config: HarnessConfig
) -> tuple[float, MetricsComponents, _Budget]:
    """Compose the topic scalar and its four record components.

    Returns the scalar, the :class:`~knotica.core.records.MetricsComponents`
    breakdown, and the resolved :class:`_Budget` (``T`` / ``T_target`` / whether
    it was freshly frozen) so the caller can persist the frozen budget on
    generation 0.
    """
    quality_answers = statistics.mean(item.quality for item in breakdown)
    # Topic-attributable findings only (v2 counting rule -- the reason for the
    # SCALAR_FORMULA_VERSION bump): a scoped lint run also returns vault-level
    # findings (log.md, index.md, root schema), and counting those scored every
    # topic's cleanliness down for defects no page of the topic carries -- while
    # wiki_status, bucketing per topic, reported zero for the same generation.
    lint_violations = sum(
        1 for violation in lint_vault(store, topic) if topic_of_violation(violation.path) == topic
    )
    n_content_pages = _count_content_pages(store, topic)
    per_item_tokens = statistics.median(item.total_tokens for item in breakdown)
    budget = _resolve_budget(store, topic, per_item_tokens, config)
    scalar_value = scalar.compose(
        quality_answers,
        lint_violations,
        budget.T,
        budget.T_target,
        n_content_pages=n_content_pages,
        w_lint=config.w_lint,
        lam=config.lam,
    )
    components = MetricsComponents(
        qa_accuracy=statistics.mean(item.qa_accuracy for item in breakdown),
        citation_validity=statistics.mean(item.citation_validity for item in breakdown),
        lint_violations=float(lint_violations),
        token_cost=_cost_factor(budget.T, budget.T_target, config.lam),
    )
    return scalar_value, components, budget


def _count_content_pages(store: VaultStore, topic: str) -> int:
    """Count a topic's content pages (every ``.md`` page except its schema overlay).

    Mirrors ``core.lint``'s content-page rule so the scalar's lint-cleanliness
    reference ``L_ref = max(1, n_content_pages)`` normalizes violations by the
    same page set the linter counted.
    """
    overlay = f"{topic}/{_SCHEMA_OVERLAY_FILENAME}"
    return sum(1 for path in iter_page_paths(store, topic) if path != overlay)


def _cost_factor(per_item_tokens: float, target: float, lam: float) -> float:
    """The applied hinged token-cost discount multiplier in ``[0, 1]``.

    Mirrors the discount inside :func:`knotica.evals.scalar.compose` (kept local
    rather than importing that module's private helper) so the record's
    ``token_cost`` component reflects the exact multiplier the scalar applied:
    ``1.0`` at or under budget, shrinking linearly with the over-budget hinge.
    """
    if target <= 0:
        return 1.0
    overage = max(0.0, (per_item_tokens - target) / target)
    return max(0.0, min(1.0, 1.0 - lam * overage))


def _resolve_budget(
    store: VaultStore, topic: str, per_item_tokens: float, config: HarnessConfig
) -> _Budget:
    """Read the topic's frozen ``T_target``, or compute and mark it for freezing.

    On generation 0 the topic has no ``eval.toml``: ``T_target = tau * T`` is
    computed for this run and flagged ``newly_frozen`` so the caller persists it.
    On later generations the frozen value is read back unchanged, so the budget
    stays fixed across the topic's history.
    """
    eval_toml_path = _eval_toml_path(topic)
    if store.exists(eval_toml_path):
        return _Budget(
            T=per_item_tokens,
            T_target=_read_t_target(store.read_text(eval_toml_path)),
            newly_frozen=False,
        )
    return _Budget(T=per_item_tokens, T_target=config.tau * per_item_tokens, newly_frozen=True)


def _read_t_target(text: str) -> float:
    """Parse the frozen ``t_target`` from a topic's ``eval.toml``."""
    import tomllib

    return float(tomllib.loads(text)["t_target"])


def _format_eval_toml(budget: _Budget, config: HarnessConfig) -> str:
    """Render the topic's ``eval.toml`` recording the frozen budget and its provenance."""
    return (
        "# knotica eval budget target for this topic.\n"
        "# Frozen when the topic is first evaluated and read back unchanged on every\n"
        "# later generation, so the token-cost discount uses a fixed reference.\n"
        f"t_target = {budget.T_target}\n"
        f"tau = {config.tau}\n"
        f"scalar_formula_version = {config.scalar_formula_version}\n"
    )
