"""Behavioral spec for the LLM boundary's response-completeness guard.

A Messages API call can come back ``200 OK`` and still carry no answer: the model
may be cut off at the caller's ``max_tokens`` budget or at its own context window,
or it may decline the request outright. The response says which via ``stop_reason``,
and that field exists *only* on the response object -- so this boundary is the one
layer that can tell "the model never finished" from "the model finished badly."

What these tests pin:

- **An unfinished response never becomes a Completion.** Returning the prefix
  pushed the diagnosis downstream to a JSON parser, which reported "invalid JSON"
  -- a true statement naming the wrong cause and hiding the fixable one (the
  budget). The guard raises here instead, while the evidence is still in hand.
- **A fix that names a change, and retryability that tracks reproducibility.**
  The original rule was blanket non-retryable, justified by "the calls are
  ``temperature=0``". That premise only holds when the request can pin its own
  output length. A judge snapshot that rejects ``temperature`` and thinks
  adaptively by default pins neither lever, so an identical request genuinely
  can succeed on retry — and calling it non-retryable parked a transient
  failure behind the loop's hour-long floor. ``max_tokens`` truncation is now
  retryable exactly when the request could not pin its length; a context-window
  overflow never is, because a too-large input cannot be resampled smaller.
- **Truncation and refusal are distinct.** Both are non-answers, but a bigger
  budget resolves only the first, so they carry different remedies.
- **The guard is narrow.** A finished response passes through untouched and
  carries its stop reason; a response reporting no stop reason at all is not
  treated as evidence of truncation.

Zero network: an autouse guard replaces ``socket.socket``, and the SDK client is
monkeypatched with a stub, so no test here reaches the wire.
"""

import socket
import warnings
from types import SimpleNamespace

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.llm import (
    AnthropicClient,
    Completion,
    Message,
    MeteredApiKeyFallbackWarning,
)

#: The fallback (metered) credential env var -- set to a dummy so construction
#: reaches the SDK-client stage, where the stub replaces the real client.
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

#: The preferred (subscription) credential env var.
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

#: A synthetic, structurally-plausible sentinel. NOT a real credential.
DUMMY_KEY = "sk-ant-dummy-value-not-real"

#: A prefix of a JSON object -- what the Messages API actually returns when a
#: structured-output response is cut off mid-string. Schema-valid JSON is
#: guaranteed only for a response that *finishes*.
TRUNCATED_JSON_PREFIX = '{"answer": "Cumulative prospect theory'


@pytest.fixture(autouse=True)
def _scrub_eval_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from the no-credential state, then set what it needs.

    A real credential exported on the dev machine must never leak in: it would
    mask the resolution contract and risk a real API call.
    """
    monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket creation in this module fail loudly."""

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the eval LLM test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


class _StoppingMessages:
    """A stub ``messages`` resource whose response reports a given ``stop_reason``.

    ``omit_stop_reason`` models a response shape that carries no such field at
    all, so the guard's fail-open branch is exercised against a real absence
    rather than an explicit ``None``.
    """

    def __init__(self, stop_reason: str, *, omit_stop_reason: bool = False) -> None:
        self._stop_reason = stop_reason
        self._omit_stop_reason = omit_stop_reason

    def create(self, **_kwargs: object) -> SimpleNamespace:
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=TRUNCATED_JSON_PREFIX)],
            usage=SimpleNamespace(input_tokens=900, output_tokens=64),
        )
        if not self._omit_stop_reason:
            response.stop_reason = self._stop_reason
        return response


def _client_with_stop_reason(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str, *, omit_stop_reason: bool = False
) -> AnthropicClient:
    """An offline ``AnthropicClient`` whose SDK stub returns the given stop reason."""
    pytest.importorskip("anthropic")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, DUMMY_KEY)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MeteredApiKeyFallbackWarning)
        client = AnthropicClient()
    stub = _StoppingMessages(stop_reason, omit_stop_reason=omit_stop_reason)
    monkeypatch.setattr(client, "_client", SimpleNamespace(messages=stub))
    return client


def _complete(
    client: AnthropicClient,
    *,
    max_tokens: int,
    snapshot: str = "snapshot-under-test",
) -> Completion:
    return client.complete(
        snapshot=snapshot,
        system="system",
        messages=[Message(role="user", content="question")],
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# A truncated response raises rather than returning its unusable prefix
# ---------------------------------------------------------------------------


def test_a_response_truncated_at_max_tokens_raises_instead_of_returning_the_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_with_stop_reason(monkeypatch, "max_tokens")

    with pytest.raises(KnoticaError) as caught:
        _complete(client, max_tokens=1024)

    error = caught.value
    assert error.code is ErrorCode.LLM_API_ERROR
    assert "1024" in error.message, "the message must name the budget that ran out"
    assert "max_tokens" in error.message, "the response's own stop reason must be reported"


def test_a_truncated_response_always_offers_the_changes_that_resolve_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_with_stop_reason(monkeypatch, "max_tokens")

    with pytest.raises(KnoticaError) as caught:
        _complete(client, max_tokens=1024)

    assert "narrower" in caught.value.fix and "budget" in caught.value.fix, (
        "the fix must offer the two changes that actually resolve a truncation"
    )


def test_truncation_is_retryable_only_when_the_request_could_not_pin_its_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old rule assumed every call was reproducible; some cannot be.

    A request pins its own output length only if it can pin its sampling.
    ``claude-sonnet-5`` rejects ``temperature`` outright, so the client omits it
    and identical calls draw different lengths (measured: 95/98/97 output tokens
    across three identical judge calls). Calling that non-retryable parked a
    transient failure behind the loop's hour-long non-retryable floor. A
    snapshot that accepts ``temperature`` keeps the original guarantee.
    """
    client = _client_with_stop_reason(monkeypatch, "max_tokens")

    with pytest.raises(KnoticaError) as unpinned:
        _complete(client, max_tokens=1024, snapshot="claude-sonnet-5")
    assert unpinned.value.retryable is True, "a snapshot that rejects temperature cannot pin length"

    with pytest.raises(KnoticaError) as pinned:
        _complete(client, max_tokens=1024, snapshot="claude-haiku-4-5")
    assert pinned.value.retryable is False, (
        "with temperature pinned, an identical retry truncates identically"
    )


def test_a_response_cut_off_by_the_context_window_is_treated_as_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A model whose context window runs out first produces the same unusable
    # prefix as a max_tokens cutoff; it must not pass as a complete answer.
    client = _client_with_stop_reason(monkeypatch, "model_context_window_exceeded")

    with pytest.raises(KnoticaError) as caught:
        _complete(client, max_tokens=4096)

    # Non-retryable *regardless* of whether the request pinned its output length
    # (this one did not). An input that does not fit cannot be made to fit by
    # sampling differently — unlike a max_tokens race, which can.
    assert caught.value.retryable is False


# ---------------------------------------------------------------------------
# A refusal is a distinct non-answer with a distinct remedy
# ---------------------------------------------------------------------------


def test_a_refused_response_raises_with_a_rephrase_fix_not_a_budget_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_with_stop_reason(monkeypatch, "refusal")

    with pytest.raises(KnoticaError) as caught:
        _complete(client, max_tokens=4096)

    error = caught.value
    assert error.retryable is False
    assert "Rephrase" in error.fix, "a refusal is fixed by rewording, not by a bigger budget"
    assert "budget" not in error.fix, "a budget bump is irrelevant to a refusal"


# ---------------------------------------------------------------------------
# The guard is narrow: finished responses pass through untouched
# ---------------------------------------------------------------------------


def test_a_completed_response_returns_normally_and_carries_its_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_with_stop_reason(monkeypatch, "end_turn")

    completion = _complete(client, max_tokens=4096)

    assert completion.stop_reason == "end_turn"
    assert completion.usage.output_tokens == 64, (
        "usage mapping must be unaffected by the completeness guard"
    )


def test_a_response_reporting_no_stop_reason_is_returned_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Absent stop_reason is not evidence of truncation. The guard fails open here
    # so a response shape that omits the field still yields a Completion.
    client = _client_with_stop_reason(monkeypatch, "unused", omit_stop_reason=True)

    completion = _complete(client, max_tokens=16)

    assert completion.text == TRUNCATED_JSON_PREFIX
    assert completion.stop_reason is None
