"""A truncated judge call must not discard a 21-question eval run.

Observed on the `decision-making` topic: four of seven identical-corpus eval
runs aborted with ``EvalRunError``, each on a *different* golden example. The
cause was not the golden set and not the corpus — it was the judge's output
budget.

The cause of the overrun is **not established**. Measured spend on this judge
is ~100 output tokens (45 on an exact match, ~100 on a partial match against a
500-character reference, eight samples across two examples), so the 512 ceiling
was already 5x typical usage. A first hypothesis — that Sonnet 5's
adaptive-by-default thinking was eating the budget — was tested directly and
**refuted**: identical calls with and without thinking explicitly disabled spend
the same ~100 tokens and neither truncates at 512.

So the fixes here are mitigations, deliberately layered rather than aimed at a
cause: 4x the budget, a 4x-again retry, and a single bad sample dropped instead
of taken out on the run. One unparseable score fails one example, and one failed
example fails the whole run — correctly, since a scalar from 20 of 21 questions
is not comparable to one from 21.

The run-level all-or-nothing rule is deliberately **not** weakened — these tests
assert it still holds when every sample fails.
"""

from __future__ import annotations

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.judge import (
    JUDGE_MAX_TOKENS,
    JUDGE_RETRY_MAX_TOKENS,
    JudgeParseError,
    grade,
)
from knotica.evals.llm import (
    Completion,
    LLMIncompleteResponseError,
    TokenUsage,
    _incomplete_response_error,
)

JUDGE = "claude-sonnet-5"


def _completion(text: str) -> Completion:
    return Completion(text=text, usage=TokenUsage(input_tokens=10, output_tokens=5))


class _ScriptedClient:
    """Returns/raises a scripted outcome per call, recording each request."""

    auth_mode = "api_key"

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> Completion:
        self.calls.append(kwargs)
        outcome = self._outcomes[min(len(self.calls) - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


def _truncated() -> LLMIncompleteResponseError:
    return _incomplete_response_error(
        "max_tokens", auth_mode="api_key", snapshot=JUDGE, max_tokens=512, reproducible=False
    )


def test_the_budget_has_headroom_well_beyond_measured_usage() -> None:
    """512 was 5x typical spend and still overran; 2048 is ~20x, with a retry above it."""
    assert JUDGE_MAX_TOKENS > 512
    assert JUDGE_RETRY_MAX_TOKENS > JUDGE_MAX_TOKENS


def test_the_judge_instrument_hash_is_unaffected_by_the_budget() -> None:
    """A ceiling bounds how long a response may be; it does not change what the
    judge writes within that bound. Folding it into the fingerprint would retire
    every baseline in every topic for a change that alters no score."""
    from knotica.evals.config import harness_version
    from knotica.evals.judge import JUDGE_PROMPT_HASH

    assert harness_version(JUDGE_PROMPT_HASH).startswith("c1d10c168a193fbd")


# ---------------------------------------------------------------------------
# A bad sample is retried, then dropped — never fatal on its own
# ---------------------------------------------------------------------------


def test_a_truncated_sample_is_retried_with_more_headroom() -> None:
    client = _ScriptedClient(_truncated(), _completion('{"score": 0.6}'))

    score = grade(client, JUDGE, "q", "candidate", "reference", n=1)

    assert score == pytest.approx(0.6)
    assert [call["max_tokens"] for call in client.calls] == [
        JUDGE_MAX_TOKENS,
        JUDGE_RETRY_MAX_TOKENS,
    ]


def test_an_unparseable_sample_is_also_retried() -> None:
    """A whole-but-scoreless response is the same class of failure as a cut-off one."""
    client = _ScriptedClient(_completion("I would rather not say."), _completion('{"score": 0.4}'))

    assert grade(client, JUDGE, "q", "c", "r", n=1) == pytest.approx(0.4)
    assert len(client.calls) == 2


def test_a_sample_failing_twice_is_dropped_and_the_others_still_score() -> None:
    """One bad sample out of three used to abort a 21-question run."""
    client = _ScriptedClient(
        _completion('{"score": 0.9}'),  # sample 1
        _truncated(),  # sample 2, first try
        _truncated(),  # sample 2, retry -> dropped
        _completion('{"score": 0.7}'),  # sample 3
    )

    score = grade(client, JUDGE, "q", "candidate", "reference", n=3)

    assert score == pytest.approx(0.8), "the median of the two survivors"


def test_every_sample_failing_is_still_an_instrument_failure() -> None:
    """The safety property holds: no score is not a low score."""
    client = _ScriptedClient(_truncated())

    with pytest.raises(JudgeParseError):
        grade(client, JUDGE, "q", "candidate", "reference", n=3)


def test_an_auth_failure_is_never_absorbed_into_the_median() -> None:
    """Only unfinished/unparseable responses are per-sample noise.

    Swallowing a rate limit or a rejected credential would compute a median from
    whichever calls happened to get through — a fabricated measurement.
    """
    client = _ScriptedClient(
        KnoticaError(ErrorCode.LLM_API_ERROR, "invalid credential", retryable=False)
    )

    with pytest.raises(KnoticaError) as excinfo:
        grade(client, JUDGE, "q", "candidate", "reference", n=3)

    assert not isinstance(excinfo.value, JudgeParseError)
    assert "credential" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Truncation retryability tracks whether the request could pin its own length
# ---------------------------------------------------------------------------


def test_truncation_is_retryable_when_the_request_could_not_pin_its_length() -> None:
    """Sonnet 5 rejects `temperature` and thinks by default, so it is not reproducible.

    Classifying this non-retryable is what parked a flaky truncation behind the
    loop's hour-long failure floor.
    """
    error = _incomplete_response_error(
        "max_tokens", auth_mode="api_key", snapshot=JUDGE, max_tokens=512, reproducible=False
    )

    assert error.retryable is True
    assert "identical retry may well succeed" in (error.fix or "")


def test_truncation_stays_non_retryable_when_the_request_is_reproducible() -> None:
    error = _incomplete_response_error(
        "max_tokens", auth_mode="api_key", snapshot=JUDGE, max_tokens=512, reproducible=True
    )

    assert error.retryable is False
    assert "truncates identically" in (error.fix or "")


def test_a_refusal_is_never_retryable() -> None:
    """A policy decision reproduces; only a length problem is transient."""
    error = _incomplete_response_error(
        "refusal", auth_mode="api_key", snapshot=JUDGE, max_tokens=512, reproducible=False
    )

    assert error.retryable is False
