"""Behavioral spec for the eval harness's LLM client seam.

The eval harness is the first knotica-owned LLM access, and its credential is a
new trust boundary. These tests pin that boundary and the dependency-injection
seam that keeps the whole suite offline:

- **Env-only credential, OAuth-first.** ``AnthropicClient`` resolves its
  credential from the process environment and nowhere else -- never a
  ``config.toml``, never the vault. Resolution prefers a subscription
  ``CLAUDE_CODE_OAUTH_TOKEN`` (no metered spend); it falls back to the metered
  ``ANTHROPIC_API_KEY`` only when the OAuth token is absent, and that fallback is
  loud (a ``MeteredApiKeyFallbackWarning``). Neither set raises the typed,
  actionable "not configured" error naming *both* variables *before any network
  attempt*, so an offline test run can never reach the wire even on a machine that
  happens to export a real credential.
- **No credential leak.** Neither the OAuth token nor the API key ever appears in
  the client's ``repr`` or ``str`` (or in an error message) -- a client committed
  into a clone's git history (or logged) must not carry the secret; only the
  non-secret auth *mode* is kept.
- **Injectable fake.** ``FakeLLMClient`` conforms to the ``complete`` protocol
  and replays canned completions with synthetic token usage preserved exactly
  (no rounding, no cross-model conversion), so every downstream eval test runs
  with zero network via constructor injection.
- **Lazy SDK import.** Importing the module must not pull in the heavy
  ``anthropic`` SDK -- it is imported only when a real call is actually made, so
  the module stays cheap to import and the cold-start-isolation guarantee holds.
  A missing ``evals`` dependency group surfaces as the house typed error naming
  the exact install command.
- **Faithful response mapping.** The real client's response-shaping helpers map
  the SDK's block-based content to plain answer text and its usage object to
  exact token counts -- absent/``None`` cache fields become ``0``, never leak
  through -- because this mapping is the cost term's ground truth on the live
  path the fake never exercises.

Zero network throughout: an autouse guard replaces ``socket.socket`` so any
accidental network attempt fails loudly instead of silently succeeding.

Written concurrently with the client implementation (disjoint files). The
``complete`` keyword signature is fixed by the interface contract; the *field
shapes* of ``Completion`` / ``Message`` / ``TokenUsage`` and the
``FakeLLMClient`` constructor are the implementer's call and are pinned here to
the most natural reading of the design (see ``# PINNED INTERFACE`` below). A
mismatch surfaces as a loud construction-time ``TypeError`` at the integration
checkpoint, not a silent wrong value -- reconcile there.
"""

import socket
import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.llm import (
    AUTH_MODE_API_KEY,
    AUTH_MODE_OAUTH,
    AnthropicClient,
    Completion,
    FakeLLMClient,
    LLMClient,
    Message,
    MeteredApiKeyFallbackWarning,
    TokenUsage,
    _extract_text,
    _usage_from_response,
)

#: The fallback (metered) credential env var.
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

#: The preferred (subscription) credential env var -- OAuth wins when present.
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

#: A synthetic, structurally-plausible sentinel used to prove the API-key value is
#: never echoed and never sourced from a config file. NOT a real credential.
SENTINEL_KEY = "sk-ant-api03-SENTINEL-do-not-leak-0000000000"

#: The OAuth-token sentinel -- proves the subscription token is likewise never
#: echoed on any path. NOT a real credential.
SENTINEL_OAUTH_TOKEN = "sk-ant-oat01-SENTINEL-do-not-leak-0000000000"

#: A minimal, distinctive JSON schema for the structured-outputs pass-through
#: tests -- its identity is asserted verbatim inside ``output_config``.
_TEST_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def _scrub_eval_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from the no-credential state (both variables absent).

    A real ``CLAUDE_CODE_OAUTH_TOKEN`` (which this very environment exports) or
    ``ANTHROPIC_API_KEY`` on the dev machine must never leak into these tests: it
    would mask the resolution contract and risk a real API call. Tests that need a
    credential present set it explicitly.
    """
    monkeypatch.delenv(OAUTH_TOKEN_ENV, raising=False)
    monkeypatch.delenv(ANTHROPIC_KEY_ENV, raising=False)


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket creation in this module fail loudly.

    The trust-boundary tests assert the client never reaches the network -- an
    absent key must raise *before* any network attempt, and the fake must never
    touch the wire. Replacing ``socket.socket`` turns a silent, key-dependent
    network success into a hard failure, so "before any network attempt" is an
    actively enforced property rather than an implied one. ``subprocess`` uses
    OS pipes, not ``socket.socket``, so the import-isolation child is unaffected.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the eval LLM test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


# ---------------------------------------------------------------------------
# PINNED INTERFACE (documented negotiable -- see module docstring)
#
# The `complete` keyword signature is fixed by the interface contract. The field
# names below are the implementer's call; they are pinned to the design's most
# natural reading (`response.usage` mirrors Anthropic's own `input_tokens` /
# `output_tokens`; a `Completion` carries the answer text plus that usage; the
# fake replays a list of canned completions). If they diverge, these three
# builders are the single reconciliation point.
# ---------------------------------------------------------------------------


def _usage(*, input_tokens: int = 12, output_tokens: int = 34) -> TokenUsage:
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _completion(
    *, text: str = "The answer, with a citation.", usage: TokenUsage | None = None
) -> Completion:
    return Completion(text=text, usage=usage if usage is not None else _usage())


def _fake(completions: list[Completion]) -> FakeLLMClient:
    return FakeLLMClient(completions=completions)


# ---------------------------------------------------------------------------
# Missing key -> a typed, actionable error before any network attempt
# ---------------------------------------------------------------------------


def test_constructing_the_anthropic_client_without_a_key_raises_a_typed_error() -> None:
    with pytest.raises(KnoticaError) as excinfo:
        AnthropicClient()

    err = excinfo.value
    actionable = f"{err.message} {err.fix}"
    assert ANTHROPIC_KEY_ENV in actionable, (
        "the not-configured error must name the exact env var to set, "
        f"so the failure is self-explanatory; got message={err.message!r} fix={err.fix!r}"
    )


def test_constructing_the_anthropic_client_with_a_key_present_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A dummy value is enough: construction validates key presence and builds the
    # SDK client, which makes no network call on construction (the autouse guard
    # would fire on any socket). Requires the `evals` group installed, so skip on
    # the base test env where `anthropic` is absent (the lazy import would raise).
    pytest.importorskip("anthropic")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")

    client = AnthropicClient()

    assert client is not None, "a present key lets construction succeed offline"


def test_real_and_fake_clients_conform_to_the_llm_client_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Runtime-checkable protocol conformance: isinstance() verifies the `complete`
    # method genuinely exists on each class. This pins the seam against structural
    # regressions (e.g. a method orphaned out of the class body by a mis-placed
    # helper) that zero-network tests cannot observe through call sites.
    pytest.importorskip("anthropic")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")

    assert isinstance(AnthropicClient(), LLMClient), (
        "AnthropicClient must satisfy the LLMClient protocol"
    )
    canned = Completion(
        text="canned",
        usage=TokenUsage(
            input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_creation_tokens=0
        ),
    )
    assert isinstance(FakeLLMClient(completions=canned), LLMClient), (
        "FakeLLMClient must satisfy the LLMClient protocol"
    )


def _client_with_sdk_raising(monkeypatch: pytest.MonkeyPatch, status_code: int) -> AnthropicClient:
    """An offline AnthropicClient whose SDK stub raises a real APIStatusError."""
    anthropic_sdk = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    client = AnthropicClient()
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code, request=request)
    sdk_error = anthropic_sdk.APIStatusError("Error", response=response, body=None)

    class _RaisingMessages:
        @staticmethod
        def create(**_kwargs: object) -> object:
            raise sdk_error

    class _RaisingSdkClient:
        messages = _RaisingMessages()

    monkeypatch.setattr(client, "_client", _RaisingSdkClient())
    return client


def _complete_once(client: AnthropicClient) -> None:
    client.complete(
        snapshot="snapshot-under-test",
        system="system",
        messages=[Message(role="user", content="question")],
        max_tokens=16,
    )


class _RecordingMessages:
    """A stub ``messages`` resource that records the kwargs passed to ``create``.

    ``create`` returns a minimal well-formed response (one text block + a usage
    object) so :meth:`AnthropicClient.complete` maps text and usage without a
    network call -- the autouse socket guard would fire on any real socket.
    """

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


# ---------------------------------------------------------------------------
# Structured outputs -- a json_schema constrains the request via output_config
# ---------------------------------------------------------------------------


def test_complete_sends_output_config_when_a_json_schema_is_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A schema on the call must reach the SDK as the canonical `output_config`
    # structured-outputs parameter, shaped exactly {"format": {"type":
    # "json_schema", "schema": <schema>}} -- the surface that guarantees the model
    # emits schema-valid JSON rather than a hand-formatted (and fallible) blob.
    client, messages = _recording_client(monkeypatch)

    client.complete(
        snapshot="snapshot-under-test",
        system="system",
        messages=[Message(role="user", content="question")],
        max_tokens=16,
        json_schema=_TEST_JSON_SCHEMA,
    )

    assert messages.received_kwargs.get("output_config") == {
        "format": {"type": "json_schema", "schema": _TEST_JSON_SCHEMA}
    }, "a provided json_schema must land as the canonical output_config structured-outputs param"


def test_complete_omits_output_config_when_no_json_schema_is_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without a schema the request must be byte-for-byte the pre-existing call: no
    # output_config key at all, so existing unconstrained callers stay unchanged.
    client, messages = _recording_client(monkeypatch)

    client.complete(
        snapshot="snapshot-under-test",
        system="system",
        messages=[Message(role="user", content="question")],
        max_tokens=16,
    )

    assert "output_config" not in messages.received_kwargs, (
        "an unconstrained call must send no output_config, leaving existing callers unchanged"
    )


def test_metered_rate_limit_surfaces_as_retryable_typed_error_naming_the_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A live 429 must land in the typed envelope (never a raw SDK traceback),
    # carry the active auth mode for diagnosability, be retryable, and leak no
    # credential material.
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MeteredApiKeyFallbackWarning)
        client = _client_with_sdk_raising(monkeypatch, 429)

    with pytest.raises(KnoticaError) as caught:
        _complete_once(client)

    error = caught.value
    assert error.code is ErrorCode.LLM_API_ERROR, "SDK failures map to the LLM_API_ERROR code"
    assert error.retryable is True, "a rate limit clears on its own -- retryable"
    assert "api_key mode" in error.message, "the active auth mode makes the failure diagnosable"
    assert "sk-ant-dummy-value-not-real" not in error.message + error.fix, (
        "credential material must never appear in the envelope"
    )


def test_oauth_throttle_fix_text_names_the_token_fallback_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An opaque 429/401 in OAuth mode is the token-not-accepted signature: the
    # fix must point at unsetting CLAUDE_CODE_OAUTH_TOKEN to fall back, so the
    # operator is never left guessing which credential produced the failure.
    monkeypatch.setenv(OAUTH_TOKEN_ENV, "oat-dummy-value-not-real")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")
    client = _client_with_sdk_raising(monkeypatch, 429)

    with pytest.raises(KnoticaError) as caught:
        _complete_once(client)

    error = caught.value
    assert error.code is ErrorCode.LLM_API_ERROR
    assert "oauth mode" in error.message, "the OAuth mode must be named in the message"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in error.fix, (
        "the fix must name the exact fallback action for a rejected subscription token"
    )
    assert "oat-dummy-value-not-real" not in error.message + error.fix, (
        "token material must never appear in the envelope"
    )


# ---------------------------------------------------------------------------
# OAuth-first credential resolution (subscription preferred, metered fallback loud)
# ---------------------------------------------------------------------------


def test_oauth_token_present_resolves_oauth_mode_with_no_metered_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both credentials present: the subscription OAuth token must win, and no
    # metered-spend warning may fire (there is no metered spend). Construction
    # builds the SDK client (lazy `anthropic` import), so skip on the base env.
    pytest.importorskip("anthropic")
    monkeypatch.setenv(OAUTH_TOKEN_ENV, "oat-dummy-value-not-real")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")

    with warnings.catch_warnings():
        warnings.simplefilter("error", MeteredApiKeyFallbackWarning)
        client = AnthropicClient()

    assert client.auth_mode == AUTH_MODE_OAUTH, (
        "a present OAuth token must resolve OAuth mode and win over the metered key"
    )


def test_only_api_key_present_warns_about_metered_spend_and_resolves_api_key_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OAuth token absent (autouse scrub) but the metered key present: resolution
    # must fall back to the metered key AND announce it loudly with the exact
    # spend language, so metered API-credit spend is never silent.
    pytest.importorskip("anthropic")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")

    with pytest.warns(MeteredApiKeyFallbackWarning) as caught:
        client = AnthropicClient()

    assert client.auth_mode == AUTH_MODE_API_KEY, (
        "an OAuth-absent metered key resolves api_key mode"
    )
    message = str(caught[0].message)
    assert "metered" in message.lower(), "the warning must name the metered spend plainly"
    assert OAUTH_TOKEN_ENV in message, "the warning must name the unset OAuth token to set instead"


def test_no_credential_present_raises_a_typed_error_naming_both_variables() -> None:
    # Neither credential set (autouse scrub): the typed not-configured error must
    # name BOTH variables (the OAuth one as preferred) so the fix is self-evident.
    with pytest.raises(KnoticaError) as excinfo:
        AnthropicClient()

    err = excinfo.value
    actionable = f"{err.message} {err.fix}"
    assert OAUTH_TOKEN_ENV in actionable, (
        f"the not-configured error must name the preferred OAuth var; got {actionable!r}"
    )
    assert ANTHROPIC_KEY_ENV in actionable, (
        f"the not-configured error must name the fallback API-key var; got {actionable!r}"
    )


# ---------------------------------------------------------------------------
# The credential is never echoed
# ---------------------------------------------------------------------------


def test_the_client_never_echoes_the_api_key_in_its_repr_or_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Constructing a real client builds the SDK client (lazy `anthropic` import),
    # so skip on the base test env where the `evals` group is not installed.
    pytest.importorskip("anthropic")
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, SENTINEL_KEY)

    client = AnthropicClient()

    assert SENTINEL_KEY not in repr(client), "the key value must not appear in repr(client)"
    assert SENTINEL_KEY not in str(client), "the key value must not appear in str(client)"


def test_the_client_never_echoes_the_oauth_token_in_its_repr_or_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The subscription OAuth token is just as secret as the API key: it must not
    # appear in repr/str either (the no-echo discipline extends to both credentials).
    pytest.importorskip("anthropic")
    monkeypatch.setenv(OAUTH_TOKEN_ENV, SENTINEL_OAUTH_TOKEN)

    client = AnthropicClient()

    assert SENTINEL_OAUTH_TOKEN not in repr(client), (
        "the OAuth token must not appear in repr(client)"
    )
    assert SENTINEL_OAUTH_TOKEN not in str(client), "the OAuth token must not appear in str(client)"


@pytest.mark.parametrize(
    "env_var, sentinel",
    [(ANTHROPIC_KEY_ENV, SENTINEL_KEY), (OAUTH_TOKEN_ENV, SENTINEL_OAUTH_TOKEN)],
    ids=["api-key", "oauth-token"],
)
def test_a_missing_eval_group_error_never_echoes_the_credential(
    monkeypatch: pytest.MonkeyPatch, env_var: str, sentinel: str
) -> None:
    # With a credential present, construction gets past resolution and reaches the
    # lazy import; a `None` entry in sys.modules makes `import anthropic` raise,
    # reshaped into the house typed error -- which must never carry the secret, for
    # either credential. (The api-key case also warns during resolution; suppress
    # it here -- the warning text is pinned elsewhere, this pins the no-leak.)
    monkeypatch.setenv(env_var, sentinel)
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MeteredApiKeyFallbackWarning)
        with pytest.raises(KnoticaError) as excinfo:
            AnthropicClient()

    err = excinfo.value
    assert sentinel not in f"{err.message} {err.fix}", (
        "a missing-eval-group error must never echo the credential value"
    )


# ---------------------------------------------------------------------------
# The key is sourced from the environment only -- never a config file
# ---------------------------------------------------------------------------


def test_a_decoy_key_in_a_config_file_is_never_picked_up(
    isolated_home: Path,
) -> None:
    # `isolated_home` redirects HOME/XDG into tmp and clears KNOTICA_CONFIG, so
    # the only discoverable config is the decoy we plant here. With the env var
    # absent (autouse scrub), a client that consulted a config file would find
    # the decoy and "succeed"; the env-only contract requires it to still raise.
    config_dir = isolated_home / ".config" / "knotica"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        f'schema_version = 1\nanthropic_api_key = "{SENTINEL_KEY}"\n',
        encoding="utf-8",
    )

    with pytest.raises(KnoticaError) as excinfo:
        AnthropicClient()

    err = excinfo.value
    assert SENTINEL_KEY not in f"{err.message} {err.fix}", (
        "the decoy config value must neither configure the client nor be echoed"
    )


# ---------------------------------------------------------------------------
# The fake conforms to the protocol and round-trips synthetic usage exactly
# ---------------------------------------------------------------------------


def test_fake_client_returns_its_canned_completion_deterministically() -> None:
    canned = _completion(
        text="Guillotine patches are reversible.",
        usage=_usage(input_tokens=17, output_tokens=42),
    )
    client = _fake([canned])

    result = client.complete(
        snapshot="worker-snapshot",
        system="You answer from the vault.",
        messages=[Message(role="user", content="Are guillotine patches reversible?")],
        temperature=0.0,
        max_tokens=256,
    )

    assert result == canned, "the fake replays exactly the completion it was seeded with"


def test_fake_client_preserves_exact_token_counts_without_conversion() -> None:
    client = _fake([_completion(usage=_usage(input_tokens=17, output_tokens=42))])

    result = client.complete(
        snapshot="worker-snapshot",
        system="",
        messages=[Message(role="user", content="q")],
        max_tokens=8,
    )

    # Exact numbers survive verbatim -- the token-cost term is only faithful if
    # usage is carried as-is, never rounded or hand-converted across models.
    assert result.usage.input_tokens == 17
    assert result.usage.output_tokens == 42


def test_fake_client_records_the_json_schema_passed_on_the_call() -> None:
    # The fake records the structured-output schema so a runner test can assert the
    # schema pass-through that would reach the real client's output_config.
    client = _fake([_completion()])

    client.complete(
        snapshot="worker-snapshot",
        system="",
        messages=[Message(role="user", content="q")],
        max_tokens=8,
        json_schema=_TEST_JSON_SCHEMA,
    )

    assert client.calls[0].json_schema == _TEST_JSON_SCHEMA, (
        "the fake must record the exact schema it was called with, for pass-through assertions"
    )


def test_fake_client_records_no_json_schema_when_the_call_is_unconstrained() -> None:
    client = _fake([_completion()])

    client.complete(
        snapshot="worker-snapshot",
        system="",
        messages=[Message(role="user", content="q")],
        max_tokens=8,
    )

    assert client.calls[0].json_schema is None, "an unconstrained call records no schema"


def test_fake_client_satisfies_the_llm_client_protocol() -> None:
    # Conformance proven by a successful call over the full keyword signature,
    # with `temperature` defaulted -- the strongest proof the fake is a drop-in
    # for the protocol without depending on runtime-checkable introspection.
    client = _fake([_completion()])

    result = client.complete(
        snapshot="worker-snapshot",
        system="sys",
        messages=[Message(role="user", content="hi")],
        max_tokens=64,
    )

    assert isinstance(result, Completion), "complete() returns a Completion"


# ---------------------------------------------------------------------------
# Importing the module does not import the anthropic SDK (lazy import)
# ---------------------------------------------------------------------------


def test_importing_the_llm_module_does_not_import_the_anthropic_sdk() -> None:
    # A fresh interpreter is required: a same-process check false-positives
    # because `anthropic` may already be loaded by an earlier test. A top-level
    # `import anthropic` in the module fails this either way -- with the SDK
    # installed it lands in the child's sys.modules; without it the import
    # crashes -- so the lazy-import property is pinned regardless of the env.
    script = (
        "import sys\n"
        "import knotica.evals.llm\n"
        "import knotica.evals\n"
        "leaked = sorted(m for m in sys.modules if m == 'anthropic' or m.startswith('anthropic.'))\n"
        "assert not leaked, leaked\n"
        "print('IMPORT_ISOLATION_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "importing the eval LLM seam must not import the anthropic SDK; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "IMPORT_ISOLATION_OK" in result.stdout


# ---------------------------------------------------------------------------
# Response usage mapping -- the cost term's ground truth on the live path
#
# `FakeLLMClient` supplies usage directly, so these exercise the real client's
# SDK-response -> TokenUsage mapping with stub objects shaped like Anthropic's
# `Usage` (no network, no SDK). Attribute access, not construction, is the seam.
# ---------------------------------------------------------------------------


def test_usage_mapping_carries_input_and_output_tokens_verbatim() -> None:
    sdk_usage = SimpleNamespace(
        input_tokens=123,
        output_tokens=456,
        cache_read_input_tokens=7,
        cache_creation_input_tokens=8,
    )

    mapped = _usage_from_response(sdk_usage)

    assert mapped.input_tokens == 123
    assert mapped.output_tokens == 456
    assert mapped.cache_read_tokens == 7
    assert mapped.cache_creation_tokens == 8


def test_usage_mapping_coerces_none_cache_fields_to_zero() -> None:
    # Anthropic reports `None` (not 0) for a call with no cache activity; the
    # token counts must see 0, never a None that would poison later arithmetic.
    sdk_usage = SimpleNamespace(
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=None,
        cache_creation_input_tokens=None,
    )

    mapped = _usage_from_response(sdk_usage)

    assert mapped.cache_read_tokens == 0
    assert mapped.cache_creation_tokens == 0


def test_usage_mapping_defaults_absent_cache_fields_to_zero() -> None:
    # A leaner response object with no cache attributes at all still maps cleanly.
    sdk_usage = SimpleNamespace(input_tokens=10, output_tokens=20)

    mapped = _usage_from_response(sdk_usage)

    assert mapped.cache_read_tokens == 0
    assert mapped.cache_creation_tokens == 0


# ---------------------------------------------------------------------------
# Response text extraction -- assemble text blocks, skip everything else
# ---------------------------------------------------------------------------


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def test_text_extraction_joins_consecutive_text_blocks() -> None:
    blocks = [_text_block("Agent memory "), _text_block("is reusable.")]

    assert _extract_text(blocks) == "Agent memory is reusable."


def test_text_extraction_skips_non_text_blocks() -> None:
    # A tool-use block carries no answer text and must not corrupt the answer.
    blocks = [
        _text_block("Answer. "),
        SimpleNamespace(type="tool_use", id="toolu_x", input={}),
        _text_block("Done."),
    ]

    assert _extract_text(blocks) == "Answer. Done."


def test_text_extraction_skips_blocks_without_a_type() -> None:
    blocks = [SimpleNamespace(text="orphan text with no type marker"), _text_block("kept")]

    assert _extract_text(blocks) == "kept", "only blocks explicitly typed 'text' contribute"


def test_text_extraction_of_no_blocks_is_the_empty_string() -> None:
    assert _extract_text([]) == ""


# ---------------------------------------------------------------------------
# Missing eval dependency group -> actionable typed error naming the fix
# ---------------------------------------------------------------------------


def test_missing_anthropic_package_raises_an_actionable_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The key is present so construction gets past the key check and reaches the
    # lazy import; a `None` entry in sys.modules makes `import anthropic` raise
    # ImportError, which must be reshaped into the house typed error that names
    # the exact install command -- never a bare ImportError.
    monkeypatch.setenv(ANTHROPIC_KEY_ENV, "sk-ant-dummy-value-not-real")
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(KnoticaError) as excinfo:
        AnthropicClient()

    err = excinfo.value
    combined = f"{err.message} {err.fix}"
    assert "uv sync --group evals" in combined, (
        "a missing eval dependency group must surface the exact install command "
        f"so the failure is self-fixing; got message={err.message!r} fix={err.fix!r}"
    )
    assert "code repo" in combined and "not the vault" in combined, (
        "the fix must distinguish the knotica package repo from the vault data repo"
    )
    assert "--with anthropic" in combined, (
        "Desktop uvx users need the --with remediation in the same error"
    )


def test_cache_concurrent_same_key_computes_exactly_once() -> None:
    import threading

    from knotica.evals.cache import ResponseCache

    cache = ResponseCache()
    compute_calls: list[int] = []
    barrier = threading.Barrier(4)

    def compute() -> float:
        compute_calls.append(1)
        return 0.5

    def worker() -> None:
        barrier.wait()
        cache.get_or_compute(
            snapshot="snap", prompt_hash="hash", inputs=["same"], compute=compute
        )

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(compute_calls) == 1, "concurrent misses on one key must compute once"
    assert cache.hits == 3
    assert cache.misses == 1
