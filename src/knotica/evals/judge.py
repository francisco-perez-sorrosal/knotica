"""LLM-as-judge: reference-based QA grading with a packaged, hashed instrument.

The scalar's quality term rests on one number per golden example: how well a
candidate answer matches its reference answer. That number comes from here.
Four properties make it trustworthy, each load-bearing:

* **Reference-based grading.** The judge sees the question, the *reference*
  answer, and the *candidate* answer, and scores semantic equivalence and
  coverage on a bounded ``[0,1]`` rubric -- it grades meaning, not surface form,
  so an equally-correct paraphrase is not penalized for wording.
* **N-sample median.** Even at ``temperature=0`` a hosted model has residual
  non-determinism. :func:`grade` draws ``n`` samples (default 3, odd) at
  ``temperature=0`` and takes the median, which dampens that jitter and -- for an
  odd ``n`` -- is always one of the actual samples.
* **Warm-cache reproducibility.** The **final median** is cached on the tuple
  ``(judge_snapshot, judge_prompt_hash, question, candidate, reference)`` via
  :mod:`knotica.evals.cache`. The ``n`` samples are drawn *only on a miss*; a warm
  re-run of the same frozen generation returns the stored median and makes **zero**
  LLM calls, which is what lets the topic scalar reproduce bit-for-bit.
* **Reward-hacking guard.** The grading instrument -- the rubric plus the way the
  inputs are framed -- is a **packaged module constant** (:data:`JUDGE_PROMPT`),
  never read from the vault. Its ``sha256`` (:data:`JUDGE_PROMPT_HASH`) is computed
  at import time and folds into both the cache keyspace and the harness fingerprint,
  so any edit to the instrument rotates the cache and the recorded ``harness_version``.
  A future schema-evolving loop that edits *vault* prompts therefore cannot touch
  the instrument it is measured against.

**Unparseable / out-of-range policy (made visible, never silent).** The judge is
asked for a bounded score. A response carrying a number that overshoots the range
(e.g. ``1.5``) is *clamped* to ``[0,1]`` -- a deterministic recovery from a minor
formatting slip that still reflects the model's clear intent. A response carrying
**no** parseable score is an *instrument failure*, not a low grade: it raises the
typed :class:`JudgeParseError` rather than returning a silent ``0.0``. Silently
scoring an unparseable response ``0.0`` would conflate "the candidate is wrong"
(a legitimate ``0.0``) with "the grader misbehaved" (a broken instrument) and
quietly corrupt the scalar; the typed error surfaces the second case loudly.

The model snapshot is always the ``judge_snapshot`` argument -- never hardcoded
here. The pinned default lives in ``evals.config``.
"""

import hashlib
import json
import re
import statistics
from collections.abc import Callable
from typing import cast

from knotica.evals.cache import ResponseCache
from knotica.evals.llm import LLMClient, Message

__all__ = [
    "DEFAULT_N_JUDGE_SAMPLES",
    "JUDGE_CACHE_NAMESPACE",
    "JUDGE_MAX_TOKENS",
    "JUDGE_PROMPT",
    "JUDGE_PROMPT_HASH",
    "JUDGE_SYSTEM_PROMPT",
    "JUDGE_USER_TEMPLATE",
    "JudgeParseError",
    "grade",
]

#: The response-cache namespace the judge tags its lookups with, so a shared cache
#: reports the judge's hit-rate separately from the runner's (see
#: :meth:`knotica.evals.cache.ResponseCache.stats_for`).
JUDGE_CACHE_NAMESPACE = "judge"

#: Default number of judge samples per example. Odd so the median is always an
#: actual sample; kept small because the cache means the cost is paid once per
#: unique input tuple across a whole run.
DEFAULT_N_JUDGE_SAMPLES = 3

#: Upper bound on judge-response length. The judge returns a brief reasoning plus
#: a single bounded score, so a small ceiling both suffices and caps per-call cost.
JUDGE_MAX_TOKENS = 512

#: Longest offending snippet echoed in a :class:`JudgeParseError` message -- enough
#: to debug the malformed response without an unbounded error payload.
_ERROR_SNIPPET_LIMIT = 200

#: The system-role rubric: an impartial reference-based grader that rewards
#: semantic equivalence and coverage, not surface wording.
JUDGE_SYSTEM_PROMPT = (
    "You are an impartial grader of question-answering quality. You are given a "
    "question, a reference answer that is known to be correct, and a candidate "
    "answer to grade.\n\n"
    "Score how well the candidate answer matches the reference answer in MEANING "
    "and COVERAGE, on a scale from 0 to 1:\n"
    "- 1.0 = the candidate conveys the same correct information as the reference "
    "(a faithful paraphrase scores 1.0 -- do not penalize different wording).\n"
    "- 0.0 = the candidate is wrong, contradicts the reference, or is unrelated.\n"
    "- Intermediate values = the candidate is partially correct or covers only "
    "some of the reference's claims.\n\n"
    "Grade meaning, not style. Reward correct paraphrases; penalize factual errors, "
    "contradictions, and omissions of the reference's substantive claims."
)

#: The user-role framing. Per-example content is spliced in at call time by
#: :func:`_build_user_message` via literal-safe replacement of the ``[[...]]``
#: sentinels (they never occur in real content and, unlike ``str.format``, cannot
#: choke on braces or ``$`` in candidate text).
JUDGE_USER_TEMPLATE = (
    "Grade the candidate answer against the reference answer for the question below.\n\n"
    "QUESTION:\n[[QUESTION]]\n\n"
    "REFERENCE ANSWER:\n[[REFERENCE]]\n\n"
    "CANDIDATE ANSWER:\n[[CANDIDATE]]\n\n"
    'Respond with ONLY a JSON object of the form {"reasoning": "<one sentence>", '
    '"score": <number between 0 and 1>} and nothing else.'
)

#: The full grading instrument as a single canonical string: the rubric plus the
#: input framing. This is exactly what :data:`JUDGE_PROMPT_HASH` digests, so any
#: edit to either part rotates the hash (and thus the cache keyspace and the
#: harness fingerprint). Splicing per-example content does not touch this constant.
JUDGE_PROMPT = f"{JUDGE_SYSTEM_PROMPT}\n\n---\n\n{JUDGE_USER_TEMPLATE}"

#: ``sha256`` of :data:`JUDGE_PROMPT`, computed once at import. The reward-hacking
#: fingerprint (folds into the response-cache key and ``harness_version``).
JUDGE_PROMPT_HASH = hashlib.sha256(JUDGE_PROMPT.encode("utf-8")).hexdigest()

#: A signed decimal literal (``0``, ``0.6``, ``.6``, ``-0.2``, ``1.5``). Shared by
#: the labeled-score and bare-number extractors.
_NUMBER = r"-?\d*\.?\d+"

#: Matches a ``score``-labeled number anywhere in the response, e.g. ``"score": 0.8``
#: or ``Score = 0.8``. Preferred over a bare-float scan so a stray number in the
#: reasoning prose is never mistaken for the score.
_SCORE_LABEL_RE = re.compile(rf'"?score"?\s*[:=]\s*({_NUMBER})', re.IGNORECASE)

#: Matches a response that is *only* a number (the simplest well-formed reply).
_BARE_NUMBER_RE = re.compile(_NUMBER)


class JudgeParseError(ValueError):
    """Raised when a judge response carries no parseable score in ``[0,1]``.

    A typed, visible signal for an *instrument* failure -- deliberately distinct
    from a legitimate low grade. A candidate that is simply wrong scores ``0.0``
    through normal grading; a judge response that yields no score at all means the
    grader itself misbehaved, and that must surface rather than silently score the
    example ``0.0`` and corrupt the scalar. The message names what was expected and
    echoes a bounded snippet of the offending response for debugging.
    """


def grade(
    llm_client: LLMClient,
    judge_snapshot: str,
    question: str,
    candidate: str,
    reference: str,
    *,
    n: int = DEFAULT_N_JUDGE_SAMPLES,
    cache: ResponseCache | None = None,
    on_sample: Callable[[int, int], None] | None = None,
) -> float:
    """Return the reference-based quality score in ``[0,1]`` for one QA example.

    Draws ``n`` samples from ``llm_client`` at ``temperature=0`` on
    ``judge_snapshot`` and returns their median. The **median** is cached on
    ``(judge_snapshot, judge_prompt_hash, question, candidate, reference)``: on a
    warm hit the samples are not drawn again and the client is never called, so a
    frozen-corpus re-run is free and bit-for-bit reproducible.

    Args:
        llm_client: The injected client (real :class:`~knotica.evals.llm.AnthropicClient`
            in production, ``FakeLLMClient`` in tests -- the zero-network seam).
        judge_snapshot: Exact dated judge model id (an argument, never hardcoded).
        question: The golden question.
        candidate: The answer under test.
        reference: The known-correct reference answer.
        n: Number of samples to median over. Must be odd (default ``3``) so the
            median is a real drawn sample, never an average of two middles.
        cache: The response cache shared across a run. ``None`` uses a fresh,
            single-call cache (no cross-call reuse) -- the harness passes one cache
            per run so every judged example reuses warm entries.

    Returns:
        The median score, a bounded ``float`` in ``[0,1]``.

    Raises:
        ValueError: if ``n`` is not a positive odd number.
        JudgeParseError: if a drawn sample carries no parseable score.
    """
    if n < 1 or n % 2 == 0:
        raise ValueError(f"n must be a positive odd number of judge samples, got {n}.")
    active_cache = cache if cache is not None else ResponseCache()
    result = active_cache.get_or_compute(
        snapshot=judge_snapshot,
        prompt_hash=JUDGE_PROMPT_HASH,
        inputs=[question, candidate, reference],
        compute=_median_of_samples(
            llm_client, judge_snapshot, question, candidate, reference, n, on_sample=on_sample
        ),
        namespace=JUDGE_CACHE_NAMESPACE,
    )
    return cast(float, result)


def _median_of_samples(
    llm_client: LLMClient,
    judge_snapshot: str,
    question: str,
    candidate: str,
    reference: str,
    n: int,
    *,
    on_sample: Callable[[int, int], None] | None = None,
) -> Callable[[], float]:
    """Build the cache-miss callback: draw ``n`` samples and return their median.

    Returned as a thunk so :meth:`ResponseCache.get_or_compute` invokes it only on
    a miss -- the whole point of caching the final median rather than each sample.
    ``on_sample`` (fired before each draw) therefore reports only real network
    samples: a warm cache hit draws nothing and reports nothing, honestly.
    """

    def compute() -> float:
        samples: list[float] = []
        for index in range(n):
            if on_sample is not None:
                on_sample(index + 1, n)
            samples.append(_draw_sample(llm_client, judge_snapshot, question, candidate, reference))
        return statistics.median(samples)

    return compute


def _draw_sample(
    llm_client: LLMClient,
    judge_snapshot: str,
    question: str,
    candidate: str,
    reference: str,
) -> float:
    """Make one ``temperature=0`` judge call and parse its bounded score."""
    completion = llm_client.complete(
        snapshot=judge_snapshot,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[
            Message(role="user", content=_build_user_message(question, reference, candidate))
        ],
        temperature=0.0,
        max_tokens=JUDGE_MAX_TOKENS,
    )
    return _parse_score(completion.text)


def _build_user_message(question: str, reference: str, candidate: str) -> str:
    """Splice per-example content into the framing via literal-safe replacement.

    ``str.replace`` on the ``[[...]]`` sentinels is robust to any content -- braces
    or ``$`` in a candidate answer would break ``str.format`` / ``string.Template``.
    """
    return (
        JUDGE_USER_TEMPLATE.replace("[[QUESTION]]", question)
        .replace("[[REFERENCE]]", reference)
        .replace("[[CANDIDATE]]", candidate)
    )


def _parse_score(text: str) -> float:
    """Extract the bounded score from a judge response, clamping to ``[0,1]``.

    Tries, in order: an exact JSON object with a numeric ``score``; a
    ``score``-labeled number inside prose- or partially-malformed output; a
    response that is only a number. A parsed number is clamped to ``[0,1]``. When
    no number can be extracted, raises :class:`JudgeParseError` -- an unparseable
    response is an instrument failure, not a silent ``0.0``.
    """
    stripped = text.strip()
    for extractor in (_score_from_json, _score_from_label, _score_from_bare_number):
        score = extractor(stripped)
        if score is not None:
            return _clamp_unit(score)
    raise JudgeParseError(
        "The judge returned no parseable score in [0,1]. Expected a JSON object "
        f'like {{"score": <0..1>}}; got: {_truncate(stripped)}'
    )


def _score_from_json(text: str) -> float | None:
    """Return the numeric ``score`` of an exact JSON object, or ``None``.

    Rejects a boolean ``score`` (``bool`` is an ``int`` subclass) so ``true``/``false``
    is not silently read as ``1``/``0``.
    """
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    value = obj.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _score_from_label(text: str) -> float | None:
    """Return the first ``score``-labeled number, or ``None``."""
    match = _SCORE_LABEL_RE.search(text)
    return float(match.group(1)) if match else None


def _score_from_bare_number(text: str) -> float | None:
    """Return the value when the whole response is a single number, else ``None``."""
    match = _BARE_NUMBER_RE.fullmatch(text)
    return float(match.group()) if match else None


def _clamp_unit(value: float) -> float:
    """Clamp a score to the closed unit interval ``[0,1]``."""
    return max(0.0, min(1.0, value))


def _truncate(text: str, limit: int = _ERROR_SNIPPET_LIMIT) -> str:
    """Truncate an offending snippet for a readable, bounded error message."""
    return text if len(text) <= limit else f"{text[:limit]}…"
