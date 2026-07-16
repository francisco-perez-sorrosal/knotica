"""Behavioral spec for the DSPy program adapter (``BaselineProgram``).

``BaselineProgram`` is the seam that lets ``dspy.Evaluate`` drive the vault's own
headless query baseline: a ``dspy.Module`` that binds ``(store, topic, runner)``
at construction and, on each call, delegates to the injected runner and wraps its
answer as a ``dspy.Prediction``. dspy is the *runner only* here -- there is no
``dspy.LM``, no ``dspy.Predict``/``ChainOfThought`` -- so ``dspy`` never touches
an LLM and the whole leg runs offline. These tests pin that behaviour from the
acceptance criteria, exercising *real* dspy (installed) rather than mocking it:

- **Faithful wrapping.** ``forward`` returns a ``dspy.Prediction`` carrying the
  runner's ``answer`` / ``citations`` / ``usage`` verbatim -- the adapter adds no
  interpretation, it only re-shapes.
- **Binding at construction.** ``(store, topic, runner)`` are captured up front;
  each call delegates ``runner.run(store, topic, question)`` with exactly those
  bound collaborators and the call-time question.
- **The dspy calling convention.** ``program(**example.inputs())`` -- the exact
  invocation ``dspy.Evaluate.process_item`` uses over a
  ``dspy.Example(question=...).with_inputs("question")`` -- reaches the adapter
  and returns the runner's answer.
- **No language model configured.** The adapter works with ``dspy.settings``
  untouched (``dspy.settings.lm is None``): the design's load-bearing claim that
  dspy never needs an LM on this path.
- **A genuine dspy.Module.** The adapter *is* a ``dspy.Module`` and is runnable by
  ``dspy.Evaluate`` end to end, so a compiled dspy program is a drop-in behind the
  same seam later.
- **Cold-start isolation.** Importing ``knotica.evals`` (and even
  ``knotica.evals.program`` itself) pulls in no ``dspy`` -- the heavy dependency is
  imported only when a program is actually constructed (the module's documented
  lazy-import discipline).

Zero network throughout: the LLM is a ``FakeLLMClient`` (canned completions, zero
wire) and an autouse guard turns any socket creation into a loud failure. ``dspy``
is imported at module load (before the guard), so a later ``dspy.Evaluate`` run --
which does no network of its own here -- is unaffected.

Written concurrently with ``evals/program.py`` (disjoint files): at the first run
the top-level ``from knotica.evals.program import BaselineProgram`` fails until the
implementer lands the module -- the intended RED handshake. The
``BaselineProgram(store, topic, runner)`` construction shape and the
``dspy.Prediction(answer, citations, usage)`` result are fixed by ``SYSTEMS_PLAN``
interfaces; a mismatch surfaces as a loud failure at the integration checkpoint.

Scope note: this file exercises the DSPy leg at the *program-seam* granularity --
the full metric-driven ``dspy.Evaluate(devset, metric=build_metric(...))(program)``
end-to-end (real judge + citation scoring) is the harness's exercise, not the
program adapter's. The mini ``dspy.Evaluate`` probe below uses a trivial constant
metric to prove the adapter is a genuine ``Evaluate``-compatible program without
coupling to the scorer's internals.
"""

import socket
import subprocess
import sys
from dataclasses import dataclass, field

import dspy
import pytest

from knotica.evals.llm import Completion, FakeLLMClient, TokenUsage
from knotica.evals.program import BaselineProgram
from knotica.evals.runner import MessagesApiRunner, Prediction
from knotica.store import LocalFSStore

#: The template vault's demo topic -- has entity pages and a stored source whose
#: key the real runner's retrieval feeds to the model.
TOPIC = "agentic-systems"

#: A caller-supplied model id for the real runner. Never asserted on here (the
#: runner's own suite pins snapshot flow-through); present so the real
#: ``MessagesApiRunner`` is constructed exactly as production does.
WORKER_SNAPSHOT = "worker-snapshot-sentinel-4-6-20260715"

#: A question the demo vault can retrieve pages for. The canned completion makes
#: the answer deterministic regardless of what is retrieved, so the exact wording
#: is not load-bearing -- it only needs to drive retrieval without crashing.
QUESTION = "How does abstracted routine memory improve an agent's reuse across tasks?"

#: A distinctive object stood in for the store on the stub-runner tests. The
#: adapter must bind and pass it through untouched; identity (``is``) proves it.
_SENTINEL_STORE = object()


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any socket creation in this module fail loudly.

    The adapter reaches the network only through the runner's injected LLM
    client, and the fake never does; ``dspy`` itself makes no LLM call on this
    path. Replacing ``socket.socket`` turns any accidental real network attempt
    into a hard failure. ``dspy`` is imported at module load (before this fixture
    runs), so ``ssl``/``dspy`` are already initialised -- blocking sockets now
    cannot break their import, only a live connection attempt. ``subprocess``
    uses OS pipes, not ``socket.socket``, so the import-isolation child is
    unaffected.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the eval program test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


# ---------------------------------------------------------------------------
# Test doubles and builders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RecordedRun:
    """One recorded ``runner.run`` invocation -- the bound collaborators + question."""

    store: object
    topic: str
    question: str


@dataclass
class _StubRunner:
    """A ``BaselineRunner`` stand-in: returns a canned ``Prediction``, records its args.

    Structurally satisfies the ``run(store, topic, question) -> Prediction`` seam
    without any LLM or retrieval, so the adapter's own behaviour (bind, delegate,
    wrap) is tested in isolation from the runner's machinery.
    """

    prediction: Prediction
    calls: list[_RecordedRun] = field(default_factory=list)

    def run(self, store: object, topic: str, question: str) -> Prediction:
        self.calls.append(_RecordedRun(store=store, topic=topic, question=question))
        return self.prediction


def _usage(*, input_tokens: int = 17, output_tokens: int = 42) -> TokenUsage:
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _prediction(
    *, answer: str, citations: list[str], usage: TokenUsage | None = None
) -> Prediction:
    """The runner's ``Prediction`` the stub replays (the adapter's input)."""
    return Prediction(
        answer=answer, citations=citations, usage=usage if usage is not None else _usage()
    )


def _structured_completion(
    *, answer: str, citations: list[str], usage: TokenUsage | None = None
) -> Completion:
    """A canned completion in the runner's parsed JSON shape, for real-runner tests."""
    import json

    payload = json.dumps({"answer": answer, "citations": list(citations)})
    return Completion(text=payload, usage=usage if usage is not None else _usage())


def _real_program(vault, completion: Completion) -> BaselineProgram:
    """A ``BaselineProgram`` over the production ``MessagesApiRunner`` + a fake LLM."""
    runner = MessagesApiRunner(
        llm_client=FakeLLMClient(completion), worker_snapshot=WORKER_SNAPSHOT
    )
    return BaselineProgram(LocalFSStore(vault), TOPIC, runner)


def _example(question: str) -> dspy.Example:
    """A golden-shaped ``dspy.Example`` with ``question`` as the sole input field."""
    return dspy.Example(question=question, reference_answer="ref", citations=["k1"]).with_inputs(
        "question"
    )


# ---------------------------------------------------------------------------
# forward wraps the runner's Prediction verbatim into a dspy.Prediction
# ---------------------------------------------------------------------------


def test_forward_returns_a_dspy_prediction_carrying_the_runners_answer_citations_and_usage() -> (
    None
):
    canned = _prediction(
        answer="Agent memory distils abstracted routines from past experience.",
        citations=["wang2024awm", "park2023generative"],
        usage=_usage(input_tokens=17, output_tokens=42),
    )
    program = BaselineProgram(_SENTINEL_STORE, TOPIC, _StubRunner(canned))

    result = program(question=QUESTION)

    assert isinstance(result, dspy.Prediction), "the adapter returns a dspy.Prediction"
    assert result.answer == canned.answer, "the answer is carried through verbatim"
    assert result.citations == ["wang2024awm", "park2023generative"], (
        "the citation keys are carried through verbatim, not re-derived"
    )
    assert result.usage == canned.usage, "the exact token usage is carried through untouched"
    assert result.usage.total_tokens == 59, "usage arithmetic survives the wrapping"


# ---------------------------------------------------------------------------
# Construction binds (store, topic, runner); the call delegates with them
# ---------------------------------------------------------------------------


def test_construction_binds_the_store_topic_and_runner() -> None:
    # The adapter must capture store + topic at construction and hand exactly them
    # (plus the call-time question) to the runner -- so a single call proves all
    # three bindings at once via the recorded delegation.
    stub = _StubRunner(_prediction(answer="a", citations=[]))
    program = BaselineProgram(_SENTINEL_STORE, TOPIC, stub)

    program(question=QUESTION)

    assert len(stub.calls) == 1, "one call delegates exactly once to the bound runner"
    recorded = stub.calls[0]
    assert recorded.store is _SENTINEL_STORE, "the bound store is passed through by identity"
    assert recorded.topic == TOPIC, "the bound topic is passed through unchanged"
    assert recorded.question == QUESTION, "the call-time question reaches the runner"


# ---------------------------------------------------------------------------
# The dspy calling convention: program(**example.inputs())
# ---------------------------------------------------------------------------


def test_program_answers_via_the_dspy_example_inputs_calling_convention() -> None:
    # This is the exact invocation dspy.Evaluate.process_item performs:
    # `program(**example.inputs())`. `inputs()` yields only the declared input
    # field, so it must unpack to `question=...` and reach the runner as such.
    stub = _StubRunner(
        _prediction(answer="Reuse improves as routines are distilled.", citations=[])
    )
    program = BaselineProgram(_SENTINEL_STORE, TOPIC, stub)
    example = _example(QUESTION)

    result = program(**example.inputs())

    assert isinstance(result, dspy.Prediction), (
        "the Evaluate calling convention yields a Prediction"
    )
    assert result.answer == "Reuse improves as routines are distilled.", (
        "the runner's answer is returned through the dspy inputs() call path"
    )
    assert stub.calls[0].question == QUESTION, (
        "example.inputs() unpacked to question=..., the sole declared input field"
    )


# ---------------------------------------------------------------------------
# The adapter is a genuine dspy.Module (the Phase-3a drop-in seam)
# ---------------------------------------------------------------------------


def test_the_adapter_is_a_dspy_module_so_a_compiled_program_is_a_drop_in() -> None:
    # Being a dspy.Module is what lets a compiled dspy program replace this
    # adapter behind the same `program` seam, driven by the same devset + metric.
    program = BaselineProgram(
        _SENTINEL_STORE, TOPIC, _StubRunner(_prediction(answer="a", citations=[]))
    )

    assert isinstance(program, dspy.Module), (
        "BaselineProgram must be a dspy.Module so dspy.Evaluate can run it and a "
        "compiled program is a drop-in replacement behind the same seam"
    )


# ---------------------------------------------------------------------------
# No dspy.LM configured: dspy never touches an LLM on this path
# ---------------------------------------------------------------------------


def test_the_adapter_needs_no_language_model_configured(template_vault) -> None:
    # The design's load-bearing claim: BaselineProgram calls only our own runner,
    # never dspy.Predict/ChainOfThought, so dspy.settings.lm may stay unset. Run a
    # full call over the real runner with settings.lm deliberately None and prove
    # it succeeds -- if dspy needed an LM, this call would raise.
    assert dspy.settings.lm is None, "precondition: no dspy LM is configured"
    program = _real_program(
        template_vault, _structured_completion(answer="answered with no LM", citations=[])
    )

    result = program(**_example(QUESTION).inputs())

    assert isinstance(result, dspy.Prediction), "the call succeeds with no dspy LM configured"
    assert result.answer == "answered with no LM", "the runner's answer flows through"
    assert dspy.settings.lm is None, "the adapter never configures a dspy LM as a side effect"


# ---------------------------------------------------------------------------
# Mini end-to-end: dspy.Evaluate runs the real adapter over a devset, offline
# ---------------------------------------------------------------------------


def _constant_metric(gold: object, prediction: object, trace: object | None = None) -> float:
    """A trivial 2-arg float metric -- exercises the program seam, not the scorer.

    dspy.Evaluate invokes ``metric(example, prediction)`` (no trace) and hits the
    float branch. The full judge/citation-driven ``build_metric`` end-to-end is
    the harness's exercise; here a constant keeps the focus on the adapter.
    """
    return 0.75


def test_dspy_evaluate_runs_the_adapter_over_a_devset_and_returns_scored_results(
    template_vault,
) -> None:
    # The whole DSPy leg, offline: dspy.Evaluate calls program(**example.inputs())
    # per example, scores each with the metric, and returns per-example
    # (example, prediction, score) triples in EvaluationResult.results -- exactly
    # the plumbing the harness recomputes the topic scalar from.
    devset = [
        _example("How does abstracted routine memory improve reuse across tasks?"),
        _example("What role do stored sources play in a cited answer?"),
        _example("Why is retrieval deterministic in the headless runner?"),
    ]
    program = _real_program(
        template_vault,
        _structured_completion(
            answer="A cited answer grounded in the vault.",
            citations=["wang2024awm"],
            usage=_usage(input_tokens=17, output_tokens=42),
        ),
    )

    result = dspy.Evaluate(
        devset=devset, metric=_constant_metric, num_threads=1, display_progress=False
    )(program)

    assert type(result).__name__ == "EvaluationResult", "dspy.Evaluate returns an EvaluationResult"
    assert len(result.results) == len(devset), "one (example, prediction, score) triple per example"
    result_questions = {example.question for example, _prediction_, _score in result.results}
    assert result_questions == {example.question for example in devset}, (
        "every devset example is preserved in the results, matched to its own run"
    )
    predictions = [prediction for _example_, prediction, _score in result.results]
    assert all(isinstance(prediction, dspy.Prediction) for prediction in predictions), (
        "each result carries the adapter's dspy.Prediction"
    )
    assert all(
        prediction.answer == "A cited answer grounded in the vault." for prediction in predictions
    ), "each prediction carries the runner's answer through dspy.Evaluate"
    assert all(prediction.usage.total_tokens == 59 for prediction in predictions), (
        "token usage survives the full Evaluate round-trip -- the cost term's ground truth"
    )
    scores = [score for _example_, _prediction_, score in result.results]
    assert all(isinstance(score, float) for score in scores), "the metric's float branch is scored"
    assert scores == [0.75, 0.75, 0.75], "the 2-arg metric result reaches EvaluationResult.results"


# ---------------------------------------------------------------------------
# Cold-start isolation: importing the package (and the module) pulls no dspy
# ---------------------------------------------------------------------------


def test_importing_the_evals_package_and_program_module_does_not_import_dspy() -> None:
    # A fresh interpreter is required: this test process already imported dspy at
    # module load, so a same-process check would false-positive. dspy IS installed
    # in this environment, so if importing either surface pulled dspy it would land
    # in the child's sys.modules -- making the check non-vacuous. The package must
    # stay off the dspy path (the cold-start isolation guarantee), and program.py itself
    # defers `import dspy` to construction (its documented lazy-factory discipline).
    script = (
        "import sys\n"
        "import knotica.evals\n"
        "assert not any(m == 'dspy' or m.startswith('dspy.') for m in sys.modules), "
        "'import knotica.evals leaked dspy: ' + repr(sorted(m for m in sys.modules if m == 'dspy' or m.startswith('dspy.')))\n"
        "import knotica.evals.program\n"
        "assert not any(m == 'dspy' or m.startswith('dspy.') for m in sys.modules), "
        "'import knotica.evals.program leaked dspy: ' + repr(sorted(m for m in sys.modules if m == 'dspy' or m.startswith('dspy.')))\n"
        "print('IMPORT_ISOLATION_OK')\n"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, (
        "importing the evals package/program module must not import dspy; "
        f"child stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "IMPORT_ISOLATION_OK" in result.stdout
