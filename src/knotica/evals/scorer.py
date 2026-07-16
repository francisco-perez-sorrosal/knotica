"""The triple-consumer scoring seam -- per-example quality as one bounded callable.

One callable, three consumers, all reading the *same* number: the per-example
quality of a baseline answer against its golden reference. That callable is

* **knotica's per-example quality** -- what the topic scalar averages over the
  golden devset before applying lint-cleanliness and the token-cost discount;
* **the DSPy metric** -- ``dspy.Evaluate`` invokes it as ``metric(gold, pred)``
  over the devset (the 2-argument, float-branch path exercised now); and
* **the SIA scoring core** -- a later schema-evolution loop wraps the same
  callable to score a generation's artifacts.

The quality is a bounded ``[0, 1]`` blend of two legs::

    quality = w_qa * qa_accuracy + w_cite * citation_validity   # w_qa + w_cite = 1
              v1:  w_qa = 0.7,  w_cite = 0.3

``qa_accuracy`` is the LLM-as-judge's reference-based grade (:mod:`knotica.evals.judge`);
``citation_validity`` is deterministic citation integrity (:mod:`knotica.evals.citations`).

**The binding problem, and why this module exports a factory.** ``dspy.Evaluate``
calls the metric with exactly two positional arguments -- ``metric(gold,
prediction)`` -- so the collaborators the score needs (the LLM client, the pinned
judge snapshot, the clone store, the topic, the shared judge cache, and the
weights) cannot be passed at call time. They are **bound up front** by
:func:`build_metric`, which returns the closed-over ``score`` callable. There is
no module-level ``score`` reading hidden globals: every collaborator is an
explicit argument to the factory, and every tunable constant is an overridable
factory keyword (so the config layer can thread packaged or CLI-overridden values
through without a hidden global).

**The trace branch (the DSPy metric contract).** The returned ``score`` takes an
optional third ``trace`` argument:

* ``trace is None`` -> returns the bounded ``float`` quality (knotica eval /
  ``dspy.Evaluate`` / SIA -- the Phase-2 path);
* ``trace is not None`` -> returns ``bool`` (``quality >= threshold``), the
  DSPy-bootstrap "is this a good enough demonstration?" contract.

**Reference-aware citation guard (a reward-hacking guard).** Citation validity
normally delegates to :func:`knotica.evals.citations.integrity`, whose
empty-citations reading is a vacuous ``1.0`` (an answer that cites nothing raises
no unresolved-citation violation). That vacuous reading opens a reward-hacking
vector: under later optimization, citing *nothing* would out-score citing
*imperfectly*. This module closes it at the one place where both the candidate
and the golden reference are in scope -- when the golden reference itself carries
citations and the candidate cites nothing, the citation leg is ``0.0``, not the
vacuous ``1.0``. When the reference makes no citations there is nothing to
demand, so the delegation to ``integrity`` is unchanged.

**Judge-instrument failures stay visible.** A judge response that yields no
parseable score raises :class:`~knotica.evals.judge.JudgeParseError`. This module
does **not** catch it: an instrument failure is not a legitimate ``0.0`` grade,
and swallowing it into a low score would silently corrupt the scalar. It
propagates out of ``score`` for the orchestration layer to surface.

``gold`` and ``prediction`` are **duck-typed**: ``score`` reads only the named
attributes (:class:`GoldExample` / :class:`ScoredPrediction`), so a ``dspy.Example``
/ ``dspy.Prediction``, the runner's ``Prediction``, or a test stand-in all satisfy
the seam -- and this module needs no ``dspy`` import.
"""

from collections.abc import Callable, Sequence
from typing import Protocol

from knotica.evals import citations, judge
from knotica.evals.cache import ResponseCache
from knotica.evals.llm import LLMClient
from knotica.store import VaultStore

__all__ = [
    "DEFAULT_THRESHOLD",
    "W_CITE",
    "W_QA",
    "GoldExample",
    "ScoredPrediction",
    "build_metric",
]

#: v1 per-example quality weight on the judge-assessed QA accuracy leg. The
#: dominant term: answer correctness matters more than citation bookkeeping.
W_QA = 0.7

#: v1 per-example quality weight on the deterministic citation-validity leg.
#: Complements :data:`W_QA` -- the two sum to ``1.0`` so the blend stays in ``[0, 1]``.
W_CITE = 0.3

#: v1 bool-branch cutoff: ``quality >= DEFAULT_THRESHOLD`` marks an example as a
#: "good enough" demonstration for the DSPy bootstrap contract. Exercised only on
#: the Phase-3a optimizer path (``trace is not None``); untuned and overridable.
#: A later step relocates the packaged default into the eval config layer; this
#: local default keeps the seam self-contained until then.
DEFAULT_THRESHOLD = 0.5


class GoldExample(Protocol):
    """The golden example's duck-typed read surface.

    ``score`` reads only these attributes, so a ``dspy.Example`` (built from a
    ``QARecord`` with ``question`` / ``reference_answer`` / ``citations``) or any
    equivalently-shaped stand-in satisfies it structurally -- no ``dspy`` import.
    ``citations`` is the *reference's* citation keys, consulted by the
    reference-aware citation guard.
    """

    question: str
    reference_answer: str
    citations: Sequence[str]


class ScoredPrediction(Protocol):
    """The baseline prediction's duck-typed read surface.

    ``score`` reads the answer text (for judge grading) and the citation keys
    (for citation validity). A ``dspy.Prediction``, the runner's ``Prediction``,
    or a test stand-in exposing ``answer`` and ``citations`` all satisfy it.
    """

    answer: str
    citations: Sequence[str]


def build_metric(
    llm_client: LLMClient,
    judge_snapshot: str,
    store: VaultStore,
    topic: str,
    *,
    cache: ResponseCache | None = None,
    w_qa: float = W_QA,
    w_cite: float = W_CITE,
    threshold: float = DEFAULT_THRESHOLD,
    n_judge_samples: int = judge.DEFAULT_N_JUDGE_SAMPLES,
) -> Callable[..., float | bool]:
    """Bind the scorer's collaborators and return the DSPy-native ``score`` metric.

    The returned callable has the signature ``score(gold, prediction, trace=None)``
    -- exactly the argument shape ``dspy.Evaluate`` invokes (``metric(gold,
    prediction)``, 2-arg float branch). Binding here rather than at call time is
    what lets the same callable serve DSPy, ``knotica eval``, and the SIA loop.

    Args:
        llm_client: The injected LLM client for judge grading (real
            :class:`~knotica.evals.llm.AnthropicClient` in production,
            ``FakeLLMClient`` in tests -- the zero-network seam).
        judge_snapshot: Exact dated judge model id (an argument, never hardcoded).
        store: The clone store citation validity resolves sources against.
        topic: The topic whose ``sources/<topic>/`` directory backs citations.
        cache: The response cache shared across a run, threaded to the judge so
            recurring inputs (and warm re-runs) reuse the stored median. ``None``
            gives each judged example a fresh, non-shared cache.
        w_qa: Weight on the QA-accuracy leg (default :data:`W_QA`).
        w_cite: Weight on the citation-validity leg (default :data:`W_CITE`).
        threshold: The bool-branch cutoff (default :data:`DEFAULT_THRESHOLD`).
        n_judge_samples: Judge samples to median over (default
            :data:`~knotica.evals.judge.DEFAULT_N_JUDGE_SAMPLES`).

    Returns:
        The bound ``score`` callable: ``float`` when ``trace is None``, else ``bool``.
    """

    def score(
        gold: GoldExample,
        prediction: ScoredPrediction,
        trace: object | None = None,
    ) -> float | bool:
        """Per-example quality in ``[0, 1]``; the DSPy metric contract.

        Blends judge-assessed QA accuracy and citation validity into a bounded
        quality. Returns that ``float`` when ``trace is None`` (the ``knotica
        eval`` / ``dspy.Evaluate`` / SIA path), or ``quality >= threshold`` as a
        ``bool`` when ``trace`` is set (the DSPy-bootstrap contract).

        Citation validity applies the reference-aware guard: when the golden
        reference carries citations and the candidate cites nothing, the leg is
        ``0.0`` rather than the vacuous ``1.0``. A judge-instrument failure
        (:class:`~knotica.evals.judge.JudgeParseError`) propagates rather than
        collapsing into a low score.
        """
        qa_accuracy = judge.grade(
            llm_client,
            judge_snapshot,
            gold.question,
            prediction.answer,
            gold.reference_answer,
            n=n_judge_samples,
            cache=cache,
        )
        citation_validity = _citation_validity(store, topic, gold, prediction)
        quality = _clamp_unit(w_qa * qa_accuracy + w_cite * citation_validity)
        if trace is None:
            return quality
        return quality >= threshold

    return score


def _citation_validity(
    store: VaultStore,
    topic: str,
    gold: GoldExample,
    prediction: ScoredPrediction,
) -> float:
    """Citation validity in ``[0, 1]`` with the reference-aware reward-hacking guard.

    Delegates to :func:`knotica.evals.citations.integrity`, except when the
    golden reference itself carries citations and the candidate cites nothing:
    ``integrity`` would return a vacuous ``1.0``, rewarding citation-dropping over
    imperfect citing. In that one case the leg is ``0.0`` -- a reference that
    cites sources demands the candidate cite some. When the reference makes no
    citations there is nothing to demand, so the delegation is unchanged.
    """
    if gold.citations and not prediction.citations:
        return 0.0
    return citations.integrity(store, topic, prediction)


def _clamp_unit(value: float) -> float:
    """Clamp a quality value to the closed unit interval ``[0, 1]``.

    Keeps ``score`` total even under weight overrides that do not sum to ``1``;
    for the v1 weights the blend is already bounded and this is transparent.
    """
    return max(0.0, min(1.0, value))
