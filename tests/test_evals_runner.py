"""Behavioral spec for the eval baseline runner (``MessagesApiRunner``).

The runner is the headless replacement for the MCP query brain: given a store, a
topic, and a question, it resolves the vault's own ``query.md`` prompt, retrieves
relevant pages in-process, drives one LLM call, and returns a ``Prediction``
whose token ``usage`` is faithful to what the model reported. These tests pin
that behaviour from the acceptance criteria, not the implementation:

- **The vault's editable artifact is what gets driven.** The resolved
  ``query.md`` body reaches the model verbatim -- the thing being scored is the
  wiki's own (evolvable) query prompt, never a hardcoded copy.
- **Retrieval is real and feeds the model.** Search + ``read_page`` run in
  process against the vault fixture, and the retrieved page content (the source
  keys the answer must cite) reaches the model call.
- **Cost accounting is faithful.** ``Prediction.usage`` carries the model's
  exact reported token counts -- never rounded, never hand-converted.
- **Determinism knobs are honoured on every call.** ``temperature`` is ``0`` and
  the model ``snapshot`` is the caller-supplied one -- no model id is baked into
  the runner.
- **Malformed output fails loudly and typed.** A response the runner cannot parse
  raises the house typed error rather than crashing with a raw parser exception.

Zero network throughout: the LLM is a ``FakeLLMClient`` (canned completions,
zero wire), retrieval is filesystem + subprocess (OS pipes, not sockets), and an
autouse guard turns any socket creation into a loud failure.

Written concurrently with the runner implementation (disjoint files), so at the
first run the ``knotica.evals.runner`` import fails until the implementer lands
the module -- the intended RED handshake. The runner *protocol* (``run(store,
topic, question) -> Prediction``), the ``Prediction`` field shape, and the
``MessagesApiRunner(llm_client, worker_snapshot)`` constructor are fixed by
``SYSTEMS_PLAN`` interfaces. The one genuinely implementer-owned surface -- the
*shape* of the structured model output the runner parses into answer+citations
-- is pinned below to the most natural reading (JSON with ``answer`` +
``citations``) in a single builder; a mismatch surfaces as a loud failure at the
integration checkpoint, reconciled there. See ``# PINNED INTERFACE`` below.
"""

import json
import socket

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.prompts import resolve_prompt
from knotica.evals.llm import Completion, FakeCall, FakeLLMClient, TokenUsage
from knotica.evals.runner import MalformedResponseError, MessagesApiRunner, Prediction
from knotica.store import LocalFSStore

#: The template vault's demo topic, with entity pages and a stored source
#: (``sources/agentic-systems/wang2024awm.md``) whose key the answers cite.
TOPIC = "agentic-systems"

#: A distinctive, caller-supplied model id. Asserting the recorded call used
#: exactly this sentinel proves the snapshot flows from the caller and no model
#: string is hardcoded in the runner.
WORKER_SNAPSHOT = "worker-snapshot-sentinel-4-6-20260715"

#: A citation key that resolves to ``sources/agentic-systems/wang2024awm.md`` and
#: appears in the demo pages' claim lines -- present in the vault, absent from
#: ``query.md`` and from the questions below, so finding it in the model call is
#: proof that retrieved page *content* (not just the prompt) reached the model.
RETRIEVED_SOURCE_KEY = "wang2024awm"

#: A question rich in terms distinctive to ``agentic-systems/agent-memory.md``
#: (abstracted, routine, memory, agent, reuse, tasks), so in-process search
#: reliably retrieves that page regardless of how the runner builds its terms.
RETRIEVAL_QUESTION = "How does abstracted routine memory improve an agent's reuse across tasks?"


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket creation in this module fail loudly.

    The runner must reach the network only through its injected LLM client, and
    the fake never does. Retrieval uses the filesystem and (optionally) a
    ripgrep subprocess -- OS pipes, not ``socket.socket`` -- so blocking sockets
    turns any accidental real network attempt into a hard failure without
    touching legitimate retrieval.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the eval runner test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


# ---------------------------------------------------------------------------
# PINNED INTERFACE (documented negotiable -- see module docstring)
#
# `Completion` carries only `text` + `usage`, so the runner parses the answer and
# its citations out of the completion text. The *transport shape* of that
# structured output was the implementer's call; it was pinned here to the most
# natural reading -- a JSON object whose keys mirror `Prediction`'s own fields
# (`answer`, `citations`) -- and the shipped runner converged on exactly that
# shape, so this builder is now a faithful mirror of the contract. The one point
# that diverged was the malformed-output error *type*: the runner raises its own
# `MalformedResponseError(ValueError)` (aligned with the codebase's parse-error
# convention, cf. `page.FrontmatterParseError`), not the house `KnoticaError`;
# the test below asserts that actual typed contract.
# ---------------------------------------------------------------------------


def _usage(*, input_tokens: int = 17, output_tokens: int = 42) -> TokenUsage:
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _structured_completion(
    *, answer: str, citations: list[str], usage: TokenUsage | None = None
) -> Completion:
    payload = json.dumps({"answer": answer, "citations": list(citations)})
    return Completion(text=payload, usage=usage if usage is not None else _usage())


def _runner(fake: FakeLLMClient) -> MessagesApiRunner:
    return MessagesApiRunner(llm_client=fake, worker_snapshot=WORKER_SNAPSHOT)


def _store(vault) -> LocalFSStore:  # noqa: ANN001 -- vault is a pytest tmp Path fixture
    return LocalFSStore(vault)


def _assembled_prompt(call: FakeCall) -> str:
    """Everything the model saw on one call: the system prompt plus every turn.

    Placement-agnostic -- whether the runner puts material in ``system`` or in a
    user message, the delivered text is here.
    """
    return call.system + "\n" + "\n".join(message.content for message in call.messages)


# ---------------------------------------------------------------------------
# The runner returns a Prediction carrying the model's answer and citations
# ---------------------------------------------------------------------------


def test_run_returns_a_prediction_with_the_models_answer_and_citations(
    template_vault,
) -> None:
    canned = _structured_completion(
        answer="Agent memory distils abstracted routines from past experience.",
        citations=[RETRIEVED_SOURCE_KEY],
    )
    runner = _runner(FakeLLMClient(canned))

    prediction = runner.run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)

    assert isinstance(prediction, Prediction), "run() returns the runner's Prediction type"
    assert prediction.answer == "Agent memory distils abstracted routines from past experience."
    assert prediction.citations == [RETRIEVED_SOURCE_KEY]


# ---------------------------------------------------------------------------
# The vault's own query.md prompt is delivered to the model verbatim
# ---------------------------------------------------------------------------


def test_run_delivers_the_vaults_query_prompt_verbatim_to_the_model(
    template_vault,
) -> None:
    # The thing being scored must be the vault's editable query artifact, not a
    # hardcoded copy -- so the resolved query.md body must reach the model as-is.
    store = _store(template_vault)
    expected_prompt = resolve_prompt(store, "query", TOPIC).body
    fake = FakeLLMClient(_structured_completion(answer="a", citations=[]))

    _runner(fake).run(store, TOPIC, RETRIEVAL_QUESTION)

    assert fake.call_count == 1, "the runner drives exactly one model call per question"
    assembled = _assembled_prompt(fake.calls[0])
    assert expected_prompt in assembled, (
        "the resolved query.md body must reach the model verbatim, proving the "
        "scored artifact is the vault's own editable query prompt"
    )


# ---------------------------------------------------------------------------
# Retrieved page content (not just the prompt) reaches the model
# ---------------------------------------------------------------------------


def test_run_feeds_retrieved_page_content_to_the_model(template_vault) -> None:
    # A headless query must retrieve pages in-process and let the model see their
    # content -- the source keys it needs to cite. `wang2024awm` lives only in
    # the retrieved page's body, not in query.md or the question, so its presence
    # in the model call is attributable to real retrieval feeding the model.
    fake = FakeLLMClient(_structured_completion(answer="a", citations=[RETRIEVED_SOURCE_KEY]))

    _runner(fake).run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)

    assembled = _assembled_prompt(fake.calls[0])
    assert RETRIEVED_SOURCE_KEY in assembled, (
        "content retrieved from the vault (the citable source key) must reach the "
        f"model call; {RETRIEVED_SOURCE_KEY!r} was not found in the assembled prompt"
    )


# ---------------------------------------------------------------------------
# Token usage is carried through exactly -- the cost term's ground truth
# ---------------------------------------------------------------------------


def test_prediction_usage_equals_the_models_reported_usage_exactly(
    template_vault,
) -> None:
    canned = _structured_completion(
        answer="a",
        citations=[],
        usage=_usage(input_tokens=17, output_tokens=42),
    )
    fake = FakeLLMClient(canned)

    prediction = _runner(fake).run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)

    # Exactly one call, so usage is faithful to that single reported completion --
    # pinned to exact numbers because the cost term is only trustworthy when usage
    # is carried verbatim, never rounded or hand-converted across models.
    assert fake.call_count == 1
    assert prediction.usage.input_tokens == 17
    assert prediction.usage.output_tokens == 42
    assert prediction.usage.total_tokens == 59


# ---------------------------------------------------------------------------
# Determinism knobs on every model call: temperature 0, caller's snapshot
# ---------------------------------------------------------------------------


def test_every_model_call_uses_temperature_zero(template_vault) -> None:
    fake = FakeLLMClient(_structured_completion(answer="a", citations=[]))

    _runner(fake).run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)

    assert fake.calls, "the runner made at least one model call"
    assert all(call.temperature == 0.0 for call in fake.calls), (
        "every model call must use temperature 0 for determinism; "
        f"recorded temperatures were {[call.temperature for call in fake.calls]}"
    )


def test_every_model_call_uses_the_caller_supplied_snapshot(template_vault) -> None:
    # The snapshot must be the one handed to the constructor -- a hardcoded model
    # id in the runner would not equal this distinctive sentinel.
    fake = FakeLLMClient(_structured_completion(answer="a", citations=[]))

    _runner(fake).run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)

    assert fake.calls, "the runner made at least one model call"
    assert all(call.snapshot == WORKER_SNAPSHOT for call in fake.calls), (
        "every model call must use the caller-supplied snapshot (no hardcoded "
        f"model string); recorded snapshots were {[call.snapshot for call in fake.calls]}"
    )


# ---------------------------------------------------------------------------
# Malformed model output raises the house typed error, never a raw crash
# ---------------------------------------------------------------------------


def test_malformed_model_output_raises_a_typed_error_not_a_crash(
    template_vault,
) -> None:
    # A response the runner cannot parse into an answer+citations structure must
    # surface as a typed, named error (never a silent empty answer, never a raw
    # JSONDecodeError bubbling up). `MalformedResponseError` is the runner's
    # public typed contract for exactly this case.
    fake = FakeLLMClient(
        Completion(text="not the structured output the runner expects", usage=_usage())
    )

    with pytest.raises(MalformedResponseError):
        _runner(fake).run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)


# ---------------------------------------------------------------------------
# A typed trust-boundary error from the LLM layer propagates uncaught
# ---------------------------------------------------------------------------


class _RaisingLLMClient:
    """An ``LLMClient`` whose ``complete`` raises the house not-configured error.

    Models the trust-boundary failure (e.g. an unusable credential) surfacing
    from the LLM layer while the runner is mid-run, to prove the runner does not
    swallow it into a generic crash.
    """

    def complete(self, **_kwargs: object) -> Completion:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            "eval is not configured to reach the model",
            fix="set ANTHROPIC_API_KEY",
        )


def test_a_typed_llm_error_propagates_uncaught(template_vault) -> None:
    runner = _runner(_RaisingLLMClient())  # type: ignore[arg-type]  -- structural LLMClient

    with pytest.raises(KnoticaError) as excinfo:
        runner.run(_store(template_vault), TOPIC, RETRIEVAL_QUESTION)

    assert excinfo.value.code is ErrorCode.NOT_CONFIGURED, (
        "the runner must let the typed trust-boundary error propagate unchanged, "
        "not mask it as a generic mid-run failure"
    )
