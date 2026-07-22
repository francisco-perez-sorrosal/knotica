"""Behavioral spec for the temperature-omission predicate and the repinned defaults.

Sonnet 5 (and later Opus generations) reject the Messages API ``temperature``
argument outright -- a 400 error. The harness must omit the key entirely for those
snapshots while continuing to send ``temperature=0`` for every model that still
accepts it (Haiku 4.5, the current worker pin, and the historical 4.6 generation).
These tests pin that classification at three levels:

- ``_snapshot_accepts_temperature`` -- the pure predicate deciding inclusion.
- ``FakeLLMClient`` -- the injectable test double must apply the same
  conditionalization on its recorded ``FakeCall.temperature`` so tests written
  against the fake catch a regression exactly as the real client would.
- ``AnthropicClient.complete`` -- the real client's ``create_kwargs`` must
  actually omit (not merely null-out) the ``temperature`` key for a
  temperature-incompatible snapshot, and include it for one that accepts it.

Also pins the repinned module constants (``WORKER_SNAPSHOT`` /
``JUDGE_SNAPSHOT``) so a future rotation is caught here rather than discovered
downstream via an opaque 400.
"""

from types import SimpleNamespace

import pytest

from knotica.evals.config import JUDGE_SNAPSHOT, WORKER_SNAPSHOT
from knotica.evals.llm import (
    AnthropicClient,
    Completion,
    FakeLLMClient,
    Message,
    TokenUsage,
    _snapshot_accepts_temperature,
)

#: The fallback (metered) credential env var -- set so AnthropicClient() constructs
#: offline without hitting the "not configured" path.
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

#: A synthetic completion for FakeLLMClient -- content is irrelevant to these tests.
_CANNED_COMPLETION = Completion(
    text="canned",
    usage=TokenUsage(input_tokens=1, output_tokens=1),
)


# ---------------------------------------------------------------------------
# Repinned module constants
# ---------------------------------------------------------------------------


def test_worker_snapshot_is_pinned_to_haiku_4_5() -> None:
    assert WORKER_SNAPSHOT == "claude-haiku-4-5-20251001", (
        "the worker pin must be the exact dated Haiku 4.5 catalog id"
    )


def test_judge_snapshot_is_pinned_to_sonnet_5() -> None:
    assert JUDGE_SNAPSHOT == "claude-sonnet-5", (
        "the judge pin must be the exact Sonnet 5 catalog id"
    )


# ---------------------------------------------------------------------------
# _snapshot_accepts_temperature -- the pure classification predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snapshot",
    [
        "claude-sonnet-5",
        "claude-opus-4-7",
        "claude-opus-4-7-20260201",
        "claude-opus-4-8",
    ],
)
def test_temperature_incompatible_snapshots_are_rejected(snapshot: str) -> None:
    assert _snapshot_accepts_temperature(snapshot) is False, (
        f"{snapshot!r} must be classified as rejecting temperature -- the Messages "
        "API 400s on it for this generation"
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        "claude-haiku-4-5-20251001",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-6-20260101",
    ],
)
def test_temperature_compatible_snapshots_are_accepted(snapshot: str) -> None:
    assert _snapshot_accepts_temperature(snapshot) is True, (
        f"{snapshot!r} must still accept temperature=0 -- only Sonnet 5 and "
        "Opus 4.7+/4.8+ reject it"
    )


# ---------------------------------------------------------------------------
# FakeLLMClient -- the injectable double applies the same conditionalization
# ---------------------------------------------------------------------------


def test_fake_client_records_no_temperature_for_sonnet_5() -> None:
    fake = FakeLLMClient(completions=_CANNED_COMPLETION)

    fake.complete(
        snapshot="claude-sonnet-5",
        system="system",
        messages=[Message(role="user", content="question")],
        max_tokens=16,
    )

    assert fake.calls[0].temperature is None, (
        "a Sonnet 5 call must record temperature=None -- mirroring the omitted "
        "create_kwargs key on the real client"
    )


def test_fake_client_records_temperature_zero_for_haiku_4_5() -> None:
    fake = FakeLLMClient(completions=_CANNED_COMPLETION)

    fake.complete(
        snapshot="claude-haiku-4-5-20251001",
        system="system",
        messages=[Message(role="user", content="question")],
        temperature=0.0,
        max_tokens=16,
    )

    assert fake.calls[0].temperature == 0.0, (
        "a Haiku 4.5 call must still record the explicit temperature"
    )


# ---------------------------------------------------------------------------
# AnthropicClient.complete -- the real client's create_kwargs actually omit the key
# ---------------------------------------------------------------------------


class _RecordingMessages:
    """A stub ``messages`` resource that records the kwargs passed to ``create``."""

    def __init__(self) -> None:
        self.received_kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.received_kwargs = dict(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def _recording_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AnthropicClient, _RecordingMessages]:
    """An offline AnthropicClient whose SDK stub records the ``create()`` kwargs."""
    pytest.importorskip("anthropic")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")
    client = AnthropicClient()
    messages = _RecordingMessages()
    monkeypatch.setattr(client, "_client", SimpleNamespace(messages=messages))
    return client, messages


def test_sonnet_5_request_omits_the_temperature_key_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, messages = _recording_client(monkeypatch)

    client.complete(
        snapshot="claude-sonnet-5",
        system="system",
        messages=[Message(role="user", content="question")],
        max_tokens=16,
    )

    assert "temperature" not in messages.received_kwargs, (
        "a Sonnet 5 request must not send temperature at all -- the Messages API "
        f"400s on it; got kwargs={messages.received_kwargs!r}"
    )


def test_haiku_4_5_request_still_sends_temperature_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, messages = _recording_client(monkeypatch)

    client.complete(
        snapshot="claude-haiku-4-5-20251001",
        system="system",
        messages=[Message(role="user", content="question")],
        temperature=0.0,
        max_tokens=16,
    )

    assert messages.received_kwargs.get("temperature") == 0.0, (
        "a Haiku 4.5 request must still send the explicit temperature=0"
    )


def test_historical_4_6_generation_request_still_sends_temperature_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, messages = _recording_client(monkeypatch)

    client.complete(
        snapshot="claude-opus-4-6",
        system="system",
        messages=[Message(role="user", content="question")],
        temperature=0.0,
        max_tokens=16,
    )

    assert messages.received_kwargs.get("temperature") == 0.0, (
        "the historical 4.6-generation pin must still send the explicit "
        "temperature=0 -- only Sonnet 5 and Opus 4.7+/4.8+ omit it"
    )
