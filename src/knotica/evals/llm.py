"""LLM access for the eval harness -- the one network boundary, behind a DI seam.

``evals/`` is a headless process distinct from the MCP server: the server does no
LLM work (client-as-brain), but the evaluator legitimately drives Anthropic's
Messages API to answer golden questions and to grade them. That makes this
module a **trust boundary** the rest of the codebase does not have.

Three rules hold that boundary:

* **Env-only credential, subscription-first.** :class:`AnthropicClient` resolves
  its credential from the process environment and nowhere else -- never
  ``config.toml``, never the vault, never a constructor argument. Resolution is
  **OAuth-first**: a ``CLAUDE_CODE_OAUTH_TOKEN`` (a Claude subscription bearer
  token; no metered spend) is preferred; only when it is absent does the harness
  fall back to the metered ``ANTHROPIC_API_KEY``, and that fallback is **noisy**
  (a :class:`MeteredApiKeyFallbackWarning`) so metered API-credit spend is never
  silent. Whichever credential wins is handed straight to the SDK client; knotica
  keeps no copy of its own on the instance and never logs it or echoes it in an
  error message. It does remain reachable through the SDK client object (which
  must hold it to authenticate), so the boundary this module guarantees is that
  *knotica never surfaces or persists the credential itself*, not that the process
  cannot reach it at all. Only the resolved auth **mode** (``"oauth"`` /
  ``"api_key"`` -- never secret) is kept, on :attr:`AnthropicClient.auth_mode`.
* **Fail before the network.** A missing credential (neither variable set) raises
  a typed, actionable error (the house :class:`~knotica.core.errors.KnoticaError`
  ``NOT_CONFIGURED`` contract shape) naming *both* variables (the OAuth one as
  preferred) *before* the SDK client is constructed and before any request is
  made -- so an offline test run never reaches the network even if a real
  credential happens to be exported. An OAuth ``401``/``403`` at call time is a
  typed, actionable failure to fix or unset the token -- never silently retried on
  the metered key.
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

import logging
import os
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "API_KEY_ENV_VAR",
    "AUTH_MODE_API_KEY",
    "AUTH_MODE_OAUTH",
    "OAUTH_TOKEN_ENV_VAR",
    "AnthropicClient",
    "Completion",
    "FakeLLMClient",
    "LLMClient",
    "Message",
    "MeteredApiKeyFallbackWarning",
    "TokenUsage",
]

_LOGGER = logging.getLogger(__name__)

#: The **preferred** credential env var: a Claude subscription OAuth bearer token
#: (the same one Claude Code exports). Present -> OAuth mode, no metered spend.
#: Read from the process environment only -- never ``config.toml``, never the vault.
OAUTH_TOKEN_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

#: The **fallback** credential env var: a metered Anthropic API key. Used only when
#: :data:`OAUTH_TOKEN_ENV_VAR` is absent, and its selection is announced loudly (a
#: :class:`MeteredApiKeyFallbackWarning`) because it spends real API credits. Read
#: from the process environment only -- never ``config.toml`` or the vault.
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

#: The resolved auth-mode markers -- the *mode* is not secret and is recorded in
#: the per-run manifest; the credential itself never is.
AUTH_MODE_OAUTH = "oauth"
AUTH_MODE_API_KEY = "api_key"

#: The beta flag that unlocks ``Authorization: Bearer`` (OAuth) auth on the
#: Messages API. Verified against the installed ``anthropic`` 0.116 SDK source
#: (2026-07-16): the ``auth_token=`` constructor path emits only
#: ``Authorization: Bearer <token>`` and does **not** add this header -- the SDK
#: injects it only on its own credentials-provider path, which no-ops the moment a
#: static ``auth_token`` is set. So an OAuth bearer token is accepted on
#: ``/v1/messages`` only if this header is added explicitly, which is what
#: :func:`_build_sdk_client` does. (The SDK's own constant docstring states this
#: flag "unlocks ``Authorization: Bearer`` auth at all".)
_OAUTH_BETA_HEADER_NAME = "anthropic-beta"
_OAUTH_BETA_HEADER_VALUE = "oauth-2025-04-20"

#: The canonical Messages API structured-outputs parameter and its inner format
#: discriminator. Verified against the installed ``anthropic`` 0.116 SDK source
#: (2026-07-16): ``messages.create`` accepts ``output_config: OutputConfigParam``,
#: whose ``format`` is a ``JSONOutputFormatParam`` == ``{"type": "json_schema",
#: "schema": <dict>}`` with *both* inner fields required. ``output_config`` is the
#: canonical parameter -- the deprecated ``output_format`` is merely merged into it
#: by the SDK. Passing this is what *guarantees* a schema-valid JSON response, so a
#: structured completion cannot be unparseable short of a truncation or refusal.
_OUTPUT_CONFIG_KWARG = "output_config"
_JSON_SCHEMA_FORMAT_TYPE = "json_schema"


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
        json_schema: dict[str, object] | None = None,
    ) -> Completion:
        """Return one :class:`Completion` for the given system + message turns.

        ``snapshot`` is the exact dated model id; ``temperature`` defaults to
        ``0.0`` for determinism; ``max_tokens`` is required (the API demands it).
        The returned completion carries the response's exact :class:`TokenUsage`.

        ``json_schema`` is optional: when provided, the completion is constrained to
        that JSON schema via the Messages API structured-outputs surface, so the
        response is schema-valid JSON at the source (see :data:`_OUTPUT_CONFIG_KWARG`).
        Omitting it leaves the call unconstrained -- the additive default keeps every
        existing caller unchanged.
        """
        ...


class MeteredApiKeyFallbackWarning(UserWarning):
    """The harness fell back to the metered ``ANTHROPIC_API_KEY``.

    Emitted (not raised) at credential resolution when
    :data:`OAUTH_TOKEN_ENV_VAR` is absent but :data:`API_KEY_ENV_VAR` is set:
    the run proceeds, but on metered API credits rather than the (free)
    subscription OAuth token. The warning makes that spend impossible to miss --
    an adapter surfaces it as a visible stderr line. Named after the
    ``GoldenSetFloorWarning`` house convention.
    """


def _resolve_credential() -> tuple[str, str]:
    """Resolve ``(auth_mode, credential)`` OAuth-first, or raise before any network.

    Resolution is **absence-based** at resolution time: a present
    :data:`OAUTH_TOKEN_ENV_VAR` wins (OAuth mode, no metered spend); otherwise a
    present :data:`API_KEY_ENV_VAR` is used and a :class:`MeteredApiKeyFallbackWarning`
    is emitted (loud, because it spends credits); otherwise the typed
    ``NOT_CONFIGURED`` error naming *both* variables is raised before any SDK
    import or network attempt. The credential value is returned but never logged,
    echoed, or stored beyond the caller's immediate hand-off to the SDK client.
    """
    oauth_token = os.environ.get(OAUTH_TOKEN_ENV_VAR)
    if oauth_token:
        return AUTH_MODE_OAUTH, oauth_token
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if api_key:
        warnings.warn(_metered_fallback_message(), MeteredApiKeyFallbackWarning, stacklevel=2)
        return AUTH_MODE_API_KEY, api_key
    raise KnoticaError(
        ErrorCode.NOT_CONFIGURED,
        (
            f"Eval is not configured: neither {OAUTH_TOKEN_ENV_VAR} (preferred) nor "
            f"{API_KEY_ENV_VAR} is set, so the eval harness cannot authenticate its "
            "LLM calls."
        ),
        fix=(
            f"Set {OAUTH_TOKEN_ENV_VAR} to authenticate with your Claude subscription "
            f"(no metered spend), or {API_KEY_ENV_VAR} to use metered API credits. "
            "Both are read from the environment only, never from config.toml or the "
            "vault."
        ),
    )


def _metered_fallback_message() -> str:
    """The loud metered-fallback warning text (names the unset OAuth var + the spend)."""
    return (
        f"Falling back to the metered {API_KEY_ENV_VAR}: {OAUTH_TOKEN_ENV_VAR} is not "
        "set, so this eval run will spend metered Anthropic API credits. Set "
        f"{OAUTH_TOKEN_ENV_VAR} to authenticate with your Claude subscription "
        "(no metered spend) instead."
    )


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
    """The default :class:`LLMClient`: the Anthropic Messages API, env-credential-guarded.

    Construction resolves the credential from the environment **OAuth-first**
    (:func:`_resolve_credential` -- raising the typed clean error naming both
    variables if neither is set, *before* the SDK client exists or any request is
    made; warning loudly when it falls back to the metered key), then lazily
    imports ``anthropic`` and builds the SDK client the credential is handed to.
    knotica keeps no copy of the credential on the instance of its own; it is
    reachable only through the SDK client, which must hold it to authenticate. The
    resolved auth *mode* (``"oauth"`` / ``"api_key"`` -- not secret) is kept on
    :attr:`auth_mode` for the per-run manifest.
    """

    def __init__(self) -> None:
        auth_mode, credential = _resolve_credential()
        anthropic = _import_anthropic()
        #: The resolved auth mode -- not secret; recorded in the run manifest. The
        #: credential itself never lands on ``self``: it flows to the SDK client
        #: only, which must hold it to authenticate.
        self.auth_mode = auth_mode
        self._client = _build_sdk_client(anthropic, auth_mode, credential)
        if auth_mode == AUTH_MODE_OAUTH:
            # One INFO line naming the mode (never any token material) so an OAuth
            # run's provenance is visible without echoing the credential.
            _LOGGER.info("eval LLM auth: OAuth subscription mode (no metered API-credit spend)")

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int,
        json_schema: dict[str, object] | None = None,
    ) -> Completion:
        """Call the Messages API and return the text + exact usage as a Completion.

        When ``json_schema`` is provided, the request carries an ``output_config``
        that constrains the model to emit schema-valid JSON (see
        :data:`_OUTPUT_CONFIG_KWARG`); when it is ``None`` no ``output_config`` is
        sent, so an unconstrained call is byte-for-byte the pre-existing request.

        SDK transport failures (rate limits, auth rejections, server errors,
        network drops) are re-raised as typed :class:`KnoticaError`s carrying the
        active auth mode -- adapters render the envelope, never a raw traceback.
        """
        anthropic = _import_anthropic()
        create_kwargs: dict[str, object] = {
            "model": snapshot,
            "system": system,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            create_kwargs[_OUTPUT_CONFIG_KWARG] = _structured_output_config(json_schema)
        try:
            response = self._client.messages.create(**create_kwargs)
        except anthropic.APIStatusError as exc:  # type: ignore[attr-defined]
            raise _llm_api_error(exc, self.auth_mode) from exc
        except anthropic.APIConnectionError as exc:  # type: ignore[attr-defined]
            raise KnoticaError(
                ErrorCode.LLM_API_ERROR,
                f"eval LLM call failed in {self.auth_mode} mode because the network"
                " connection to the Messages API dropped before a response.",
                fix="Check connectivity and re-run; the SDK already retried with backoff.",
                retryable=True,
            ) from exc
        return Completion(
            text=_extract_text(response.content),
            usage=_usage_from_response(response.usage),
        )


def _build_sdk_client(anthropic: object, auth_mode: str, credential: str) -> object:
    """Build the Anthropic SDK client for the resolved ``auth_mode`` + ``credential``.

    OAuth mode authenticates with a bearer token via the SDK's ``auth_token=``
    mechanism, *plus* the ``anthropic-beta: oauth-2025-04-20`` header the SDK does
    not add for a static ``auth_token`` (see :data:`_OAUTH_BETA_HEADER_VALUE`) --
    without it the Messages API rejects the bearer token. API-key mode hands the
    key to ``api_key=`` unchanged. Either way the credential lands only on the SDK
    client; knotica keeps no copy.
    """
    if auth_mode == AUTH_MODE_OAUTH:
        return anthropic.Anthropic(  # type: ignore[attr-defined]
            auth_token=credential,
            default_headers={_OAUTH_BETA_HEADER_NAME: _OAUTH_BETA_HEADER_VALUE},
        )
    return anthropic.Anthropic(api_key=credential)  # type: ignore[attr-defined]


def _structured_output_config(json_schema: dict[str, object]) -> dict[str, object]:
    """Wrap a JSON schema as the Messages API ``output_config`` for structured outputs.

    Shapes ``{"format": {"type": "json_schema", "schema": <schema>}}`` -- the exact
    ``OutputConfigParam`` shape the installed ``anthropic`` 0.116 SDK expects (see
    :data:`_OUTPUT_CONFIG_KWARG`). With it, the model emits schema-valid JSON, so a
    structured completion cannot be unparseable short of a truncation or refusal.
    """
    return {"format": {"type": _JSON_SCHEMA_FORMAT_TYPE, "schema": json_schema}}


def _llm_api_error(exc: Exception, auth_mode: str) -> KnoticaError:
    """Map an SDK ``APIStatusError`` to the typed envelope error, auth-mode-tagged.

    The auth mode in the message is what makes a live failure diagnosable: an
    opaque 429 in OAuth mode reads very differently from a metered-tier rate
    limit. Transient statuses (429/5xx/529) are retryable; auth rejections are
    not. Error bodies carry no secrets; the credential never appears here.
    """
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    detail = str(getattr(exc, "message", "") or exc)[:200]
    suffix = f" (request_id: {request_id})" if request_id else ""
    message = (
        f"eval LLM call failed in {auth_mode} mode because the Messages API"
        f" returned HTTP {status}: {detail}{suffix}"
    )
    if auth_mode == AUTH_MODE_OAUTH and status in (401, 403, 429):
        return KnoticaError(
            ErrorCode.LLM_API_ERROR,
            message,
            fix=(
                "A rejected or throttled subscription token: Claude Code OAuth tokens"
                " may not be accepted for direct Messages API calls. Unset"
                " CLAUDE_CODE_OAUTH_TOKEN to fall back to the metered"
                " ANTHROPIC_API_KEY (the spend warning will fire), or wait and re-run."
            ),
            retryable=status == 429,
        )
    if status in (401, 403):
        return KnoticaError(
            ErrorCode.LLM_API_ERROR,
            message,
            fix="Check that ANTHROPIC_API_KEY is valid and has model access.",
            retryable=False,
        )
    if status == 429:
        return KnoticaError(
            ErrorCode.LLM_API_ERROR,
            message,
            fix=(
                "Your API tier's rate limit; the SDK already retried with backoff."
                " Wait a minute and re-run -- completed judge scores are cached."
            ),
            retryable=True,
        )
    return KnoticaError(ErrorCode.LLM_API_ERROR, message, retryable=True)


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
    """One recorded invocation of :meth:`FakeLLMClient.complete`, for assertions.

    ``json_schema`` records the structured-output schema the caller passed (``None``
    when the call was unconstrained), so a test can assert the schema pass-through
    that reaches the real client's ``output_config``.
    """

    snapshot: str
    system: str
    messages: tuple[Message, ...]
    temperature: float
    max_tokens: int
    json_schema: dict[str, object] | None = None


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
        json_schema: dict[str, object] | None = None,
    ) -> Completion:
        """Record the call and return the next canned completion (clamped to the last)."""
        self.calls.append(
            FakeCall(
                snapshot=snapshot,
                system=system,
                messages=tuple(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=json_schema,
            )
        )
        index = min(len(self.calls) - 1, len(self._completions) - 1)
        return self._completions[index]

    @property
    def call_count(self) -> int:
        """How many times :meth:`complete` has been invoked."""
        return len(self.calls)
