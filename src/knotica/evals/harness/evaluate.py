"""Driving ``dspy.Evaluate`` over the devset, and refusing a run that failed.

Everything between "the program and metric are built" and "the triples are
trustworthy" lives here: the progress/error-capture program wrapper, the Evaluate
call itself, and the failure rejection.

The two error-capture seams are the load-bearing subtlety. ``dspy.Evaluate`` (3.2)
skips the metric entirely for an example whose program call raised, so
:func:`_with_example_progress`'s ``forward`` wrapper is the *only* place a runner
failure is ever observable; the scorer's own ``score()`` seam covers the judge leg.
Both fire ``on_outcome`` and then **re-raise unchanged**, so dspy still records the
failure triple and :func:`_reject_on_failures` still aborts identically -- capture
observes, it never swallows.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from typing import Any, cast

from knotica.core.records import QARecord
from knotica.evals import golden
from knotica.evals.config import HarnessConfig
from knotica.evals.error_capture import OnOutcome, classify_error
from knotica.evals.harness.errors import EvalRunError
from knotica.evals.runner import Prediction

_LOGGER = logging.getLogger(__name__)

#: One ``dspy.Evaluate`` result triple. dspy is untyped, so ``.results`` arrives
#: as opaque objects. The gold element is a ``dspy.Example`` (untyped, hence
#: ``Any``); the prediction is always the runner's concrete ``Prediction``. Stated
#: once here rather than re-narrowed at each of the dozen downstream accesses.
_EvalTriple = tuple[Any, "Prediction", float]


def _question_id_map(records: Sequence[QARecord]) -> dict[str, str]:
    """Map each golden record's question to its stable id, one-time-built.

    A question shared by two or more records makes the map ambiguous for the
    runner seam (which only has the question in scope when ``program(question=
    ...)`` raises -- no ``gold`` object is reachable there); such a question
    falls back to mapping onto itself, so the outcome key is still the question
    string rather than a guessed or raised id. Golden questions are expected to
    be unique in practice (see :mod:`knotica.evals.golden`); the fallback exists
    for the rare collision, not the common case.
    """
    counts: dict[str, int] = {}
    for record in records:
        counts[record.query] = counts.get(record.query, 0) + 1
    return {
        record.query: (record.id if counts[record.query] == 1 else record.query)
        for record in records
    }


def _remap_scorer_outcome_by_question(
    on_outcome: OnOutcome, records: Sequence[QARecord], question_id_map: dict[str, str]
) -> OnOutcome:
    """Wrap ``on_outcome`` so the scorer's ``gold.id`` key resolves through the
    same question -> outcome-key fallback the runner seam uses.

    The scorer only ever sees ``gold.id`` (unique per record, even when two
    records share a question), but the runner seam can only key by question, so
    a shared question falls back to the question string there. Without this
    remap the two seams would report the *same* colliding example under two
    different keys depending on which seam happened to fire -- this keeps both
    consistent by resolving the id back through the same collision map.
    """
    id_key = {record.id: question_id_map.get(record.query, record.id) for record in records}

    def _remapped(id: str, status: str, error_class: str, detail: str) -> None:
        on_outcome(id_key.get(id, id), status, error_class, detail)

    return _remapped


def _with_example_progress(
    # The lazily-imported ``dspy`` module itself. Genuinely untyped (dspy ships no
    # stubs), so ``Any`` is the honest annotation for it rather than a silencer.
    dspy: Any,
    program: Any,
    total: int,
    on_example: Callable[[int, int, str], None] | None,
    on_substage: Callable[[str, int, int], None] | None = None,
    on_outcome: OnOutcome | None = None,
    question_id_map: dict[str, str] | None = None,
) -> object:
    """Wrap ``program`` so each forward reports ``(i, total, question)`` first.

    Counting is safe because it is lock-guarded, not because the harness is
    single-threaded (``num_threads`` defaults to 4); callbacks fire *before* the
    example runs so a watcher shows a question in flight, not one just finished.
    ``on_substage`` additionally marks the "answering" leg (metric marks "judging").

    A ``program(question=question)`` exception is the runner-error capture seam:
    ``dspy.Evaluate`` (3.2) skips the metric entirely for an example whose
    program call raised, so this is the *only* place such a failure is ever
    observable. On a caught exception, ``on_outcome`` fires once -- classified by
    :func:`~knotica.evals.error_capture.classify_error`, keyed by
    ``question_id_map`` (falling back to the question itself) -- and the
    exception is re-raised unchanged so ``dspy.Evaluate`` still records the
    failure triple and :func:`_reject_on_failures` still aborts identically.
    """

    class _ProgressProgram(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self._count = 0
            # dspy.Evaluate shares this one instance across worker threads;
            # under num_threads > 1 the count reports examples *started*.
            self._count_lock = threading.Lock()

        def forward(self, question: str) -> object:
            with self._count_lock:
                self._count += 1
                started = self._count
            try:
                if on_example is not None:
                    on_example(started, total, question)
                if on_substage is not None:
                    on_substage("answering", 0, 0)
            except Exception:  # noqa: BLE001 — progress must never break the run
                _LOGGER.debug("progress callback failed", exc_info=True)
            try:
                return program(question=question)
            except Exception as exc:
                if on_outcome is not None:
                    outcome_id = (question_id_map or {}).get(question, question)
                    on_outcome(outcome_id, "error", *classify_error(exc))
                raise

    return _ProgressProgram()


def _run_evaluate(
    # ``dspy``, ``program`` and ``metric`` are dspy objects -- genuinely untyped
    # (dspy ships no stubs), so ``Any`` is honest here rather than a silencer.
    dspy: Any,
    records: Sequence[QARecord],
    program: Any,
    metric: Any,
    config: HarnessConfig,
) -> list[_EvalTriple]:
    """Score the golden devset with ``dspy.Evaluate`` and return its ``.results``.

    Builds the devset (lazy ``dspy.Example`` conversion), runs the program over
    it with the bound metric, and returns the per-example
    ``(gold, prediction, quality)`` triples. ``max_errors`` is set past the
    devset size so a per-example failure never aborts the pass early -- the
    harness collects every result and decides on failures itself. The topic
    scalar is recomputed from these triples; ``EvaluationResult.score`` is ignored.

    ``failure_score`` is the configured Evaluate failure policy -- the same value
    the fingerprint (``runner_config_hash``) and the manifest record -- so the
    instrument the record describes is the one actually applied. It is inert on a
    clean pass (:func:`_reject_on_failures` aborts on any failure), but a caller
    that raised it must see it reach ``dspy.Evaluate``, not dspy's own default.
    """
    devset = [golden.to_example(record) for record in records]
    evaluator = dspy.Evaluate(
        devset=devset,
        metric=metric,
        num_threads=config.num_threads,
        display_progress=False,
        max_errors=len(devset) + 1,
        failure_score=config.failure_score,
    )
    # The one narrowing point: dspy hands back untyped triples, and every
    # downstream consumer relies on the concrete shape declared by _EvalTriple.
    return cast(list[_EvalTriple], list(evaluator(program).results))


def _reject_on_failures(topic: str, results: Sequence[_EvalTriple]) -> None:
    """Abort loudly if any example failed with an instrument error.

    ``dspy.Evaluate`` catches a per-example exception (a malformed runner
    response or an unparseable judge score) and records a failure-scored triple
    whose prediction is empty. Such a failure is an instrument failure, not a
    legitimate ``0.0`` quality, so a scalar averaged over silently zeroed
    examples is not trustworthy -- the run is refused rather than emit one.
    """
    failed = [gold for gold, prediction, _quality in results if _is_failed_prediction(prediction)]
    if not failed:
        return
    raise EvalRunError(
        topic,
        (
            f"{len(failed)} of {len(results)} golden examples produced no scored "
            "prediction (a malformed baseline response or an unparseable judge "
            "score, surfaced by dspy as a failure score)"
        ),
    )


def _is_failed_prediction(prediction: object) -> bool:
    """Whether a ``.results`` prediction is the empty failure sentinel.

    A successful :func:`~knotica.evals.program.BaselineProgram` prediction always
    carries a ``usage``; ``dspy.Evaluate``'s failure sentinel is an empty
    ``dspy.Prediction`` with no fields, so an absent ``usage`` marks the failure.
    """
    return getattr(prediction, "usage", None) is None
