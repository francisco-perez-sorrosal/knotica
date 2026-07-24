"""Eval error classification -- the shared leaf both the runner and the judge feed.

A dspy-driven eval swallows the exception that fails a golden example: the
program raises, ``dspy.Evaluate`` scores the failure triple and moves on, and
the *reason* -- a throttled 429, an unparseable judge response, something else
-- is otherwise lost to ``loop.err.log``. :func:`classify_error` turns a caught
exception into a small, stable classification the dashboard can render at a
glance: ``(error_class, detail)`` where ``error_class`` is one of
``"rate_limit_429"`` / ``"parse_error"`` / ``"other"``, and ``detail`` is the
truncated exception message.

Pure, dependency-light, zero I/O. Deliberately does not import
``knotica.evals.harness`` or ``knotica.evals.scorer`` -- this module is the
shared leaf *they* import, so importing either back would create a cycle.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypedDict

from knotica.evals.judge import JudgeParseError

__all__ = ["ExampleOutcome", "OnOutcome", "classify_error"]

#: Longest exception message recorded per outcome -- mirrors the existing
#: ``detail[:200]`` convention already used for the scalar progress field
#: (``knotica.core.loop_progress.write_progress``).
_DETAIL_LIMIT = 200

#: Exception shapes that mean "the grading/decode instrument failed to
#: produce a parseable result", not "the model call itself failed".
_PARSE_ERROR_TYPES: tuple[type[Exception], ...] = (JudgeParseError, json.JSONDecodeError)

#: Substrings in an exception's string form that mean "the call was
#: rate-limited" -- catches both a ``KnoticaError``-wrapped 429
#: (``evals.llm._llm_api_error``'s "...returned HTTP 429..." message) and an
#: Anthropic SDK ``RateLimitError`` body (which carries ``"rate_limit_error"``
#: but not the literal text "429").
_RATE_LIMIT_SUBSTRINGS = ("429", "rate_limit")


class ExampleOutcome(TypedDict):
    """One golden example's recorded eval outcome.

    A plain-dict shape (not a runtime object) -- this is exactly what
    accumulates into ``loop_progress.write_progress(..., examples=[...])`` and
    round-trips through JSON, so ``TypedDict`` documents the four fields
    without forcing an intermediate construction step.
    """

    id: str
    status: str
    error_class: str
    detail: str


#: Fired once per golden example as the eval proceeds: ``(id, status,
#: error_class, detail) -> None``. The runner (``harness.py``) calls it on a
#: caught exception; the scorer (``scorer.py``) calls it on success or a judge
#: parse failure. Mirrors the existing ``on_example``/``on_substage`` seam.
OnOutcome = Callable[[str, str, str, str], None]


def classify_error(exc: Exception) -> tuple[str, str]:
    """Classify a caught eval exception into ``(error_class, detail)``.

    Checked in order: a ``status_code == 429`` attribute (the raw SDK-style
    shape) regardless of message text; a ``"429"``/``"rate_limit"`` substring
    in ``str(exc)`` (the fallback covering both a wrapped and a native
    rate-limit message); the parse-error exception types; everything else
    falls back to ``"other"``. ``detail`` is the exception message truncated
    to 200 chars.
    """
    detail = str(exc)[:_DETAIL_LIMIT]
    if _is_rate_limited(exc):
        return "rate_limit_429", detail
    if isinstance(exc, _PARSE_ERROR_TYPES):
        return "parse_error", detail
    return "other", detail


def _is_rate_limited(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return any(signal in message for signal in _RATE_LIMIT_SUBSTRINGS)
