"""Behavioral spec for the eval error classifier -- the shared leaf module.

``evals.error_capture`` turns a caught exception (the runner's or the judge's)
into a small, stable classification the dashboard can render at a glance:
``classify_error(exc) -> (error_class, detail)`` where ``error_class`` is one of
``"rate_limit_429"`` / ``"parse_error"`` / ``"other"``, and ``detail`` is the
truncated exception message.

Classification is driven by two independent signals, in order:

1. a ``status_code`` attribute (the raw Anthropic-SDK-style shape) equal to
   ``429`` -- checked first, regardless of message text;
2. a ``"429"`` / ``"rate_limit"`` substring in ``str(exc)`` -- the fallback that
   catches both a :class:`~knotica.core.errors.KnoticaError`-wrapped 429
   (``evals.llm._llm_api_error``'s message shape: "...returned HTTP 429: ...")
   and Anthropic's own ``RateLimitError`` body text (which carries
   ``"rate_limit_error"``).

A :class:`~knotica.evals.judge.JudgeParseError` or a JSON-decode-shaped failure
classifies as ``"parse_error"``; everything else falls back to ``"other"``.
``detail`` mirrors the existing ``detail[:200]`` truncation convention already
used for the scalar progress ``detail`` field (``loop_progress.py``).

Pure, dependency-light, zero I/O -- no fixtures beyond builders needed. Written
concurrently with the implementation (disjoint files); RED until
``evals/error_capture.py`` lands.
"""

import json

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals import error_capture
from knotica.evals.judge import JudgeParseError

# --------------------------------------------------------------------------- #
# Builders (the "how" -- kept out of the test bodies)
# --------------------------------------------------------------------------- #


def _status_code_error(status_code: int, message: str) -> Exception:
    """A raw SDK-style exception: plain ``Exception`` carrying a ``status_code`` attr.

    Mirrors the shape ``anthropic.APIStatusError`` presents before
    ``evals.llm._llm_api_error`` wraps it -- the "by type" 429 signal.
    """
    exc = Exception(message)
    exc.status_code = status_code  # type: ignore[attr-defined]
    return exc


def _knotica_wrapped_429_error() -> KnoticaError:
    """A ``KnoticaError`` shaped exactly like ``evals.llm._llm_api_error``'s 429 wrap.

    No ``status_code`` attribute -- the classifier must fall back to the "HTTP 429"
    substring already baked into the wrapped message.
    """
    return KnoticaError(
        ErrorCode.LLM_API_ERROR,
        "eval LLM call failed in oauth mode because the Messages API returned"
        " HTTP 429: Number of request tokens has exceeded your per-minute rate"
        " limit (request_id: req_01AbCdEfGhIjKlMnOpQrSt)",
        retryable=True,
    )


def _anthropic_style_rate_limit_error() -> Exception:
    """An Anthropic SDK ``RateLimitError``-shaped body: ``status_code`` + ``rate_limit_error`` text.

    No literal "429" substring in the message -- only the ``rate_limit_error``
    error-type text the real SDK body carries, to prove the "rate_limit" fallback
    signal is checked independently of the "429" one.
    """
    exc = Exception(
        "Error code: 429 - {'type': 'error', 'error': {'type': 'rate_limit_error',"
        " 'message': 'This request would exceed your organization\\'s rate limit.'}}"
    )
    exc.status_code = 429  # type: ignore[attr-defined]
    return exc


def _judge_parse_error() -> JudgeParseError:
    """A realistic instrument-failure error, worded like ``judge._parse_score``'s raise."""
    return JudgeParseError(
        "The judge returned no parseable score in [0,1]. Expected a JSON object"
        ' like {"score": <0..1>}; got: the candidate cannot be graded from this'
    )


def _json_decode_error() -> json.JSONDecodeError:
    """A stdlib JSON decode failure -- the "JSON-decode-shaped" parse_error case."""
    return json.JSONDecodeError("Expecting value", "not json at all", 0)


def _long_message_error(length: int) -> Exception:
    """An exception whose message is ``length`` characters of harmless filler."""
    return RuntimeError("x" * length)


# --------------------------------------------------------------------------- #
# rate_limit_429 -- signal 1: a status_code == 429 attribute (checked first)
# --------------------------------------------------------------------------- #


def test_classifies_a_status_code_429_attribute_as_rate_limit_regardless_of_message() -> None:
    # The message deliberately carries neither "429" nor "rate_limit" text, so a
    # pass here proves classification reads the status_code attribute itself --
    # not merely a lucky substring match on the message.
    exc = _status_code_error(429, "Server returned an unexpected error.")

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "rate_limit_429", (
        f"a status_code=429 attribute must classify as rate_limit_429 even with a"
        f" message carrying no rate-limit wording; got {error_class!r}"
    )
    assert detail == str(exc)[:200]


# --------------------------------------------------------------------------- #
# rate_limit_429 -- signal 2: substring fallback ("429" / "rate_limit" in str(exc))
# --------------------------------------------------------------------------- #


def test_classifies_a_knotica_wrapped_http_429_message_as_rate_limit() -> None:
    # No status_code attribute on a KnoticaError -- only the wrapped "HTTP 429"
    # text in the message, exactly as evals.llm._llm_api_error produces it.
    exc = _knotica_wrapped_429_error()

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "rate_limit_429", (
        f"a KnoticaError whose message contains 'HTTP 429' must classify as"
        f" rate_limit_429 via the substring fallback; got {error_class!r}"
    )
    assert detail == str(exc)[:200]


def test_classifies_an_anthropic_style_rate_limit_error_text_as_rate_limit() -> None:
    exc = _anthropic_style_rate_limit_error()

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "rate_limit_429", (
        f"an Anthropic RateLimitError-shaped body (status_code=429 +"
        f" 'rate_limit_error' text) must classify as rate_limit_429; got {error_class!r}"
    )
    assert detail == str(exc)[:200]


# --------------------------------------------------------------------------- #
# parse_error -- judge instrument failures and JSON/schema decode failures
# --------------------------------------------------------------------------- #


def test_classifies_a_judge_parse_error_as_parse_error() -> None:
    exc = _judge_parse_error()

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "parse_error", (
        f"a JudgeParseError (unparseable judge response) must classify as"
        f" parse_error; got {error_class!r}"
    )
    assert detail == str(exc)[:200]


def test_classifies_a_json_decode_error_as_parse_error() -> None:
    exc = _json_decode_error()

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "parse_error", (
        f"a json.JSONDecodeError must classify as parse_error (the JSON/schema/decode"
        f" family named alongside JudgeParseError); got {error_class!r}"
    )
    assert detail == str(exc)[:200]


# --------------------------------------------------------------------------- #
# other -- the fallback for anything carrying no rate-limit or parse signal
# --------------------------------------------------------------------------- #


def test_classifies_a_generic_value_error_with_no_signal_as_other() -> None:
    exc = ValueError("the candidate answer was empty")

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "other", (
        f"a plain ValueError with no rate-limit or parse-error signal must fall"
        f" back to 'other'; got {error_class!r}"
    )
    assert detail == str(exc)[:200]


def test_classifies_a_generic_runtime_error_with_no_signal_as_other() -> None:
    exc = RuntimeError("connection reset by peer")

    error_class, detail = error_capture.classify_error(exc)

    assert error_class == "other", (
        f"a plain RuntimeError with no rate-limit or parse-error signal must fall"
        f" back to 'other'; got {error_class!r}"
    )
    assert detail == str(exc)[:200]


# --------------------------------------------------------------------------- #
# detail truncation -- mirrors the existing detail[:200] convention
# --------------------------------------------------------------------------- #


def test_detail_truncates_a_long_exception_message_to_200_characters() -> None:
    exc = _long_message_error(300)

    _error_class, detail = error_capture.classify_error(exc)

    assert len(detail) == 200, f"detail must be capped at 200 chars; got {len(detail)} chars"
    assert detail == str(exc)[:200], "the truncated detail must be a straight [:200] slice"


def test_detail_preserves_a_short_exception_message_verbatim() -> None:
    exc = ValueError("boom")

    _error_class, detail = error_capture.classify_error(exc)

    assert detail == "boom", (
        f"a message shorter than the 200-char cap must pass through verbatim; got {detail!r}"
    )


# --------------------------------------------------------------------------- #
# OnOutcome / ExampleOutcome shapes (implied by the per-example capture contract)
# --------------------------------------------------------------------------- #


def test_on_outcome_type_alias_is_exported() -> None:
    # A behavioral existence check, not a strict typing check (this project runs
    # no type-check gate): the harness and scorer wiring (a later step) threads a
    # 4-arg (id, status, error_class, detail) callback named OnOutcome, so the
    # symbol must be importable from this shared leaf module.
    assert hasattr(error_capture, "OnOutcome"), (
        "error_capture must export an OnOutcome callback type alias for"
        " (id, status, error_class, detail) -> None"
    )


def test_example_outcome_shape_declares_the_four_outcome_fields() -> None:
    # Loose on mechanism (TypedDict vs dataclass -- an implementation choice left
    # open by the architecture): only the field names are pinned, via __annotations__,
    # which both TypedDict and dataclass populate.
    assert hasattr(error_capture, "ExampleOutcome"), (
        "error_capture must export an ExampleOutcome shape documenting"
        " {id, status, error_class, detail}"
    )
    field_names = set(error_capture.ExampleOutcome.__annotations__)

    assert field_names == {"id", "status", "error_class", "detail"}, (
        f"ExampleOutcome must declare exactly the four outcome fields; got {field_names!r}"
    )


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(_status_code_error(429, "throttled"), id="status-code-429"),
        pytest.param(_judge_parse_error(), id="judge-parse-error"),
        pytest.param(ValueError("plain failure"), id="generic-other"),
    ],
)
def test_classify_error_always_returns_a_two_item_string_tuple(exc: Exception) -> None:
    result = error_capture.classify_error(exc)

    assert isinstance(result, tuple) and len(result) == 2, (
        f"classify_error must return a 2-tuple (error_class, detail); got {result!r}"
    )
    error_class, detail = result
    assert isinstance(error_class, str) and isinstance(detail, str), (
        f"both tuple elements must be strings; got {type(error_class).__name__},"
        f" {type(detail).__name__}"
    )
