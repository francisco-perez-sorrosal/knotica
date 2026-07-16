"""LLM access for the eval harness -- the one network boundary, behind a DI seam.

``evals/`` is a headless process distinct from the MCP server: the server does no
LLM work (client-as-brain), but the evaluator legitimately drives Anthropic's
Messages API to answer golden questions and to grade them. That makes this
module a **trust boundary** the rest of the codebase does not have.

Three rules hold that boundary:

* **Env-only key.** :class:`AnthropicClient` reads ``ANTHROPIC_API_KEY`` from the
  process environment and nowhere else -- never ``config.toml``, never the vault,
  never a constructor argument. The key is handed straight to the SDK client;
  knotica keeps no copy of its own on the instance and never logs it or echoes it
  in an error message. It does remain reachable through the SDK client object
  (which must hold it to authenticate), so the boundary this module guarantees is
  that *knotica never surfaces or persists the key itself*, not that the process
  cannot reach it at all.
* **Fail before the network.** A missing key raises a typed, actionable error
  (the house :class:`~knotica.core.errors.KnoticaError` ``NOT_CONFIGURED``
  contract shape) *before* the SDK client is constructed and before any request
  is made -- so an offline test run never reaches the network even if a real key
  happens to be exported.
* **Lazy dependency.** ``anthropic`` lives in the optional ``evals`` dependency
  group, off the ``uvx --from ... knotica mcp`` cold-start path. It is imported
  lazily inside :class:`AnthropicClient` construction, so ``import
  knotica.evals.llm`` succeeds with the base environment and only constructing a
  real client requires the group (a missing group raises an actionable "run
  ``uv sync --group evals``" error). :class:`FakeLLMClient` needs no third-party
  dependency at all.

The seam itself is the :class:`LLMClient` protocol; tests inject
:class:`FakeLLMClient` (canned completions + synthetic usage, zero network),
mirroring the ``store_factory`` DI convention in ``core.prompts``. Model
snapshots are always **arguments** to :meth:`LLMClient.complete` (its
``snapshot`` parameter), never hardcoded here -- the pinned defaults live in
``evals.config``.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "API_KEY_ENV_VAR",
    "AnthropicClient",
    "Completion",
    "FakeLLMClient",
    "LLMClient",
    "Message",
    "TokenUsage",
]

#: The single environment variable the evaluator authenticates with. Read from
#: the process environment only -- never from ``config.toml`` or the vault.
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Exact per-call token counts, taken verbatim from the model response.

    Never hand-converted across models -- tokenizers differ, so each call's own
    ``usage`` is the ground truth for the cost term. ``cache_read_tokens`` and
    ``cache_creation_tokens`` default to ``0`` for responses that report no
    cache activity. :attr:`total_tokens` is the input+output sum the harness
    uses as the per-item size measure ``T``.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Input + output tokens -- the size measure the cost penalty is based on."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class Message:
    """One conversation turn: a ``role`` (``"user"`` / ``"assistant"``) and text.

    The ``system`` prompt is a separate argument to :meth:`LLMClient.complete`,
    never a message -- mirroring the Messages API shape.
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    """A model response: the assembled text plus the call's exact token usage."""

    text: str
    usage: TokenUsage


@runtime_checkable
class LLMClient(Protocol):
    """Structural seam over a single ``complete`` call -- the injectable boundary.

    The default implementation is :class:`AnthropicClient` (real Messages API,
    ``evals`` dependency group); tests inject :class:`FakeLLMClient` for a
    deterministic, zero-network run. ``snapshot`` is always an argument so the
    pinned model id lives in ``evals.config``, never in this module.
    """

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int,
    ) -> Completion:
        """Return one :class:`Completion` for the given system + message turns.

        ``snapshot`` is the exact dated model id; ``temperature`` defaults to
        ``0.0`` for determinism; ``max_tokens`` is required (the API demands it).
        The returned completion carries the response's exact :class:`TokenUsage`.
        """
        ...


def _require_api_key() -> str:
    """Return the env API key, or raise the typed clean error before any network.

    Read from the environment only. The raised error names *what* is missing and
    *how* to fix it, and never echoes the key value.
    """
    key = os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            (
                f"Eval is not configured: the {API_KEY_ENV_VAR} environment variable "
                "is not set, so the eval harness cannot authenticate its LLM calls."
            ),
            fix=(
                f"Set {API_KEY_ENV_VAR} in your environment before running eval "
                "(it is read from the environment only, never from config.toml or "
                "the vault)."
            ),
        )
    return key


def _import_anthropic() -> object:
    """Import the ``anthropic`` module lazily, or raise an actionable typed error.

    Keeps the heavy optional dependency off every import path that merely reads
    this module. A missing package means the ``evals`` group was not installed.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            (
                "The eval dependency group is not installed: the `anthropic` "
                "package is unavailable, so AnthropicClient cannot be constructed."
            ),
            fix="Install the eval dependency group: run `uv sync --group evals`.",
        ) from exc
    return anthropic


class AnthropicClient:
    """The default :class:`LLMClient`: the Anthropic Messages API, env-key-guarded.

    Construction resolves ``ANTHROPIC_API_KEY`` from the environment (raising the
    typed clean error if absent, *before* the SDK client exists or any request is
    made), then lazily imports ``anthropic`` and builds the SDK client the key is
    handed to. knotica keeps no copy of the key on the instance of its own; it is
    reachable only through the SDK client, which must hold it to authenticate.
    """

    def __init__(self) -> None:
        key = _require_api_key()
        anthropic = _import_anthropic()
        # The key flows to the SDK client only; knotica keeps no copy of its own
        # on `self`, and never logs it or puts it in an error message. It stays
        # reachable through the SDK client, which must hold it to authenticate.
        self._client = anthropic.Anthropic(api_key=key)

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int,
    ) -> Completion:
        """Call the Messages API and return the text + exact usage as a Completion."""
        response = self._client.messages.create(
            model=snapshot,
            system=system,
            messages=[{"role": message.role, "content": message.content} for message in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return Completion(
            text=_extract_text(response.content),
            usage=_usage_from_response(response.usage),
        )


def _extract_text(blocks: list) -> str:
    """Join the text of every text block in a Messages API response.

    The response ``content`` is block-based, not a plain string; non-text blocks
    (e.g. tool-use) are skipped.
    """
    return "".join(block.text for block in blocks if getattr(block, "type", None) == "text")


def _usage_from_response(usage: object) -> TokenUsage:
    """Map an SDK ``Usage`` object to :class:`TokenUsage`, coercing absent cache fields to 0."""
    return TokenUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


@dataclass(frozen=True, slots=True)
class FakeCall:
    """One recorded invocation of :meth:`FakeLLMClient.complete`, for assertions."""

    snapshot: str
    system: str
    messages: tuple[Message, ...]
    temperature: float
    max_tokens: int


class FakeLLMClient:
    """A zero-network :class:`LLMClient` that replays canned completions.

    Construct with a single :class:`Completion` (replayed for every call) or a
    sequence (replayed one-per-call, in order; a call past the end reuses the
    last). Every invocation is recorded on :attr:`calls`, so a test can assert
    the exact request and the call count -- for example, that a warm cache
    avoided a second call. Needs no third-party dependency and never touches the
    network, preserving the suite's zero-network discipline.
    """

    def __init__(self, completions: Completion | Sequence[Completion]) -> None:
        self._completions: tuple[Completion, ...] = (
            (completions,) if isinstance(completions, Completion) else tuple(completions)
        )
        if not self._completions:
            raise ValueError("FakeLLMClient needs at least one canned completion.")
        self.calls: list[FakeCall] = []

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int,
    ) -> Completion:
        """Record the call and return the next canned completion (clamped to the last)."""
        self.calls.append(
            FakeCall(
                snapshot=snapshot,
                system=system,
                messages=tuple(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        index = min(len(self.calls) - 1, len(self._completions) - 1)
        return self._completions[index]

    @property
    def call_count(self) -> int:
        """How many times :meth:`complete` has been invoked."""
        return len(self.calls)
