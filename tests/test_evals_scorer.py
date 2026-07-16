"""Behavioral spec for the triple-consumer scorer -- the per-example metric.

``evals.scorer`` composes one bounded quality number per golden example from two
legs: the judge's reference-based QA accuracy and the deterministic citation
integrity. It is the *same callable* three consumers share -- ``knotica eval``,
``dspy.Evaluate`` now, and the Phase-3a optimizer / Phase-3b SIA shim later.

The metric contract (per the architecture's ``score`` interface)::

    score(gold, prediction, trace=None) -> float | bool
        trace is None      -> float   the per-example quality q_i in [0,1]
        trace is not None  -> bool    q_i >= threshold  (the DSPy-bootstrap leg)

Per-example quality is a convex split of the two legs::

    q_i = W_QA * qa_accuracy + W_CITE * citation_validity      # W_QA=0.7, W_CITE=0.3

``qa_accuracy`` is the judge's median score (here driven by a ``FakeLLMClient``
so no network is touched); ``citation_validity`` is ``citations.integrity`` over
the candidate's citations against the frozen clone -- *except* for the
reference-aware guard below.

**Reference-aware citation guard (binding).** ``citations.integrity`` scores an
answer that cites nothing as a vacuous ``1.0``. Left unguarded that rewards a
candidate for citing NOTHING over citing imperfectly. The scorer -- the one place
both the golden reference and the candidate are in scope -- closes that gap: when
the golden reference itself carries citations but the candidate cites nothing,
the citation term is forced to ``0.0``. When the reference also cites nothing, the
vacuous ``1.0`` stands (there was nothing to cite). A candidate that cites real,
resolvable sources always flows through the integrity fraction untouched.

--------------------------------------------------------------------------------
INTERFACE NOTE -- the binding shape

The architecture fixes the metric's *signature* -- ``score(gold, prediction,
trace=None)`` -- and the two dependency calls it makes (``judge.grade(llm_client,
judge_snapshot, gold.question, prediction.answer, gold.reference_answer)`` and
``citations.integrity(store, topic, prediction)``). It does **not** spell out how
``store`` / ``topic`` / ``llm_client`` / ``judge_snapshot`` / ``threshold`` reach
the callable, since ``dspy.Evaluate`` invokes it with only ``(example,
prediction)``. The shipped scorer resolves this with a **factory**:
``build_metric(llm_client, judge_snapshot, store, topic, *, cache, w_qa, w_cite,
threshold, n_judge_samples)`` closes over the collaborators and returns the 2-arg
``score(gold, prediction, trace=None)`` callable ``dspy.Evaluate`` runs. Every
test reaches the metric through the single ``_bind_metric`` helper, so the whole
suite pivots on one line if the binding ever changes -- the behavioral assertions
below stay fixed.

``gold`` and ``prediction`` are duck-typed: the scorer reads ``gold.question`` /
``gold.reference_answer`` / ``gold.citations`` and ``prediction.answer`` /
``prediction.citations`` -- nothing more. A ``SimpleNamespace`` and (for the
prediction, which needs only ``answer`` + ``citations``) a real ``QARecord`` both
satisfy the seam. Written concurrently with the implementation (disjoint files);
RED until ``evals/scorer.py`` lands.
--------------------------------------------------------------------------------
"""

import socket
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from knotica.core.records import QARecord
from knotica.evals import scorer
from knotica.evals.judge import JudgeParseError
from knotica.evals.llm import Completion, FakeLLMClient, TokenUsage
from knotica.store import LocalFSStore

#: The topic whose ``sources/`` tree the fixtures resolve citations against.
TOPIC = "agentic-systems"

#: A citation key the base ``template_vault`` already stores a source for, so a
#: candidate citing it resolves without planting.
STORED_SOURCE_KEY = "wang2024awm"

#: A clearly-synthetic judge snapshot id (never a real dated model string -- this
#: is a test double). Its whole point is to be distinctive enough that the
#: snapshot-flow test can observe it arriving unchanged at the fake client.
JUDGE_SNAPSHOT = "test-judge-snapshot-00000000"

#: Any non-None object selects the bool branch (``trace is not None``); its
#: identity is irrelevant, only its non-None-ness. Shared read-only.
TRACE_SENTINEL = object()


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scorer runs entirely on the fake judge + a filesystem store.

    Replacing ``socket.socket`` turns any accidental network touch into a loud
    failure, actively enforcing the zero-network guarantee for this suite.
    """

    def _blocked(*args: object, **kwargs: object) -> object:
        raise RuntimeError("network access is forbidden in the scorer test suite")

    monkeypatch.setattr(socket, "socket", _blocked)


@pytest.fixture(autouse=True)
def _scrub_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no real key is visible: the scorer must never build a real client."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Builders (the "how" -- kept out of the test bodies)
# --------------------------------------------------------------------------- #


def _usage() -> TokenUsage:
    """A small synthetic token usage for a canned completion."""
    return TokenUsage(input_tokens=11, output_tokens=7)


def _completion(text: str) -> Completion:
    """A canned completion carrying ``text`` verbatim (the judge parses the text)."""
    return Completion(text=text, usage=_usage())


def _judge_fake(score: float) -> FakeLLMClient:
    """A zero-network judge that replays one score-bearing completion for every call.

    A single completion is replayed for all ``n`` judge samples, so the median is
    exactly ``score`` -- the qa leg the composition assertions depend on.
    """
    return FakeLLMClient(_completion(str(score)))


def _garbage_judge() -> FakeLLMClient:
    """A judge whose response carries no parseable score -- an instrument failure."""
    return FakeLLMClient(_completion("the candidate cannot be graded from this"))


def _store(vault_root: Path) -> LocalFSStore:
    return LocalFSStore(vault_root)


def _plant_source(vault_root: Path, key: str) -> None:
    """Store ``sources/<TOPIC>/<key>.md`` so a candidate citing ``key`` resolves."""
    source_path = vault_root / "sources" / TOPIC / f"{key}.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(f"# {key}\n\nStored source body for {key}.\n", encoding="utf-8")


def _plant_sources(vault_root: Path, keys: list[str]) -> None:
    """Store a source for each key -- the planting loop lives here, not in a test body."""
    for key in keys:
        _plant_source(vault_root, key)


def _gold(reference_citations: list[str]) -> SimpleNamespace:
    """A duck-typed golden example: the three attrs the scorer reads from ``gold``."""
    return SimpleNamespace(
        question="What distinguishes an agentic workflow memory?",
        reference_answer="It persists reusable task strategies across episodes.",
        citations=list(reference_citations),
    )


def _prediction(citations: list[str]) -> SimpleNamespace:
    """A duck-typed candidate prediction: the two attrs the scorer reads from ``prediction``."""
    return SimpleNamespace(
        answer="Agentic workflow memory stores reusable strategies for later reuse.",
        citations=list(citations),
        usage=_usage(),
    )


def _qa_record(citations: tuple[str, ...]) -> QARecord:
    """A real ``QARecord`` usable as a ``prediction`` -- it exposes ``answer`` + ``citations``.

    The scorer reads only those two attributes from a prediction, so a curated
    record satisfies the prediction seam structurally (no ``dspy.Prediction`` and
    no runner ``Prediction`` type is required).
    """
    return QARecord(
        id="qa-scorer-duck-0001",
        topic=TOPIC,
        created="2026-07-16",
        query="What distinguishes an agentic workflow memory?",
        pages_used=("agentic-workflow-memory",),
        answer="Agentic workflow memory stores reusable strategies for later reuse.",
        citations=citations,
        verdict="good",
        corrected_answer=None,
        source="curate_example",
        model="test-worker-snapshot-00000000",
    )


def _bind_metric(
    *,
    store: LocalFSStore,
    topic: str,
    llm_client: FakeLLMClient,
    judge_snapshot: str,
    threshold: float | None = None,
) -> Callable[..., float | bool]:
    """Bind the scorer's dependencies into the 2-arg DSPy metric (the single seam point).

    See the module docstring's INTERFACE NOTE: the metric's collaborators are
    bound up front by the ``build_metric`` factory, which returns the closed-over
    ``score(gold, prediction, trace=None)`` callable. Routing every test through
    this one helper means the whole suite is insulated from the binding shape --
    reconcile here, and only here, if it ever changes.
    """
    deps: dict[str, object] = {
        "store": store,
        "topic": topic,
        "llm_client": llm_client,
        "judge_snapshot": judge_snapshot,
    }
    if threshold is not None:
        deps["threshold"] = threshold
    return scorer.build_metric(**deps)


# --------------------------------------------------------------------------- #
# The per-example weights are a convex split summing to one
# --------------------------------------------------------------------------- #


def test_the_per_example_weights_are_a_convex_split_summing_to_one() -> None:
    # The composition q = W_QA*qa + W_CITE*cite is only a proper weighted average
    # when the weights sum to 1; the architecture locks them at 0.7 / 0.3.
    assert scorer.W_QA == pytest.approx(0.7)
    assert scorer.W_CITE == pytest.approx(0.3)
    assert scorer.W_QA + scorer.W_CITE == pytest.approx(1.0), (
        "the qa/citation weights must form a convex split summing to 1.0; "
        f"got W_QA={scorer.W_QA!r} + W_CITE={scorer.W_CITE!r}"
    )


# --------------------------------------------------------------------------- #
# Float branch (trace is None): q = W_QA*qa + W_CITE*cite, bounded to [0,1]
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "judge_score, planted, cited, expected_q",
    [
        # qa=0.8, every candidate citation resolves -> cite=1.0 -> 0.7*0.8 + 0.3*1.0
        pytest.param(0.8, [], [STORED_SOURCE_KEY], 0.86, id="qa0.8-cite1.0"),
        # qa=0.4, one of two candidate citations resolves -> cite=0.5 -> 0.7*0.4 + 0.3*0.5
        pytest.param(
            0.4, ["smith2023alpha"], ["smith2023alpha", "ghost2099none"], 0.43, id="qa0.4-cite0.5"
        ),
    ],
)
def test_float_branch_composes_weighted_qa_and_citation_validity(
    template_vault: Path,
    judge_score: float,
    planted: list[str],
    cited: list[str],
    expected_q: float,
) -> None:
    _plant_sources(template_vault, planted)
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(judge_score),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    # gold cites something and the candidate also cites -> the reference-aware
    # guard stays inert, so the citation term is exactly citations.integrity.
    result = metric(_gold(["someref2020"]), _prediction(cited))

    assert isinstance(result, float), (
        f"trace defaulting to None selects the float branch; got {type(result).__name__}"
    )
    assert result == pytest.approx(expected_q), (
        f"per-example quality must be W_QA*{judge_score} + W_CITE*cite = {expected_q}; got {result!r}"
    )
    assert 0.0 <= result <= 1.0, f"per-example quality must be bounded to [0,1]; got {result!r}"


# --------------------------------------------------------------------------- #
# Bool branch (trace is not None): q_i >= threshold, threshold overridable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "threshold, expected",
    [
        # q(qa=0.8, cite=1.0) ~= 0.86; a lower threshold passes, a higher one fails.
        pytest.param(0.80, True, id="quality-above-threshold-is-true"),
        pytest.param(0.90, False, id="quality-below-threshold-is-false"),
    ],
)
def test_bool_branch_returns_whether_quality_meets_the_threshold(
    template_vault: Path,
    threshold: float,
    expected: bool,
) -> None:
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
        threshold=threshold,
    )
    result = metric(_gold(["someref2020"]), _prediction([STORED_SOURCE_KEY]), TRACE_SENTINEL)

    assert isinstance(result, bool), (
        f"a non-None trace selects the bool branch; got {type(result).__name__}"
    )
    assert result is expected, (
        f"quality ~= 0.86 vs threshold {threshold} must yield {expected}; got {result!r}"
    )


def test_bool_branch_is_inclusive_at_the_threshold_boundary(template_vault: Path) -> None:
    # Boundary semantics are `q >= threshold`, so a candidate exactly at the
    # threshold passes. The threshold is the identical float expression the scorer
    # computes (0.7*0.8 + 0.3*1.0) to avoid a spurious float-rounding miss at the
    # equality edge.
    boundary = 0.7 * 0.8 + 0.3 * 1.0
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
        threshold=boundary,
    )
    result = metric(_gold(["someref2020"]), _prediction([STORED_SOURCE_KEY]), TRACE_SENTINEL)

    assert result is True, (
        f"quality exactly at the threshold must pass (>= is inclusive); got {result!r}"
    )


# --------------------------------------------------------------------------- #
# DSPy calling convention without dspy: metric(example, prediction), duck-typed
# --------------------------------------------------------------------------- #


def test_metric_is_callable_with_two_positional_args_and_trace_defaults(
    template_vault: Path,
) -> None:
    # dspy.Evaluate calls metric(example, prediction) with exactly two positional
    # args; the omitted trace must default to None and select the float branch.
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    result = metric(_gold(["someref2020"]), _prediction([STORED_SOURCE_KEY]))

    assert isinstance(result, float), (
        f"a 2-positional-arg call must default trace to None (float branch); got {type(result).__name__}"
    )
    assert result == pytest.approx(0.86)


def test_metric_accepts_a_real_qa_record_as_the_prediction(template_vault: Path) -> None:
    # The scorer reads only .answer and .citations from a prediction, so a curated
    # QARecord (which exposes both) satisfies the seam -- proving the prediction
    # duck-type is structural, not tied to a dspy.Prediction / runner Prediction.
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    result = metric(_gold(["someref2020"]), _qa_record((STORED_SOURCE_KEY,)))

    assert result == pytest.approx(0.86), (
        "a QARecord with a resolvable citation must score like any citing prediction; "
        f"got {result!r}"
    )


# --------------------------------------------------------------------------- #
# Reference-aware citation guard (binding scorer behavior)
# --------------------------------------------------------------------------- #


def test_citation_term_is_zeroed_when_reference_cites_but_candidate_cites_nothing(
    template_vault: Path,
) -> None:
    # The golden reference carries citations, yet the candidate cites nothing:
    # the vacuous integrity 1.0 is overridden to 0.0, so q = W_QA*qa only. This is
    # the deliberate contrast partner of the empty-reference case below.
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    result = metric(_gold([STORED_SOURCE_KEY]), _prediction([]))

    assert result == pytest.approx(0.56), (
        "a reference that cites while the candidate cites nothing must zero the "
        f"citation term, leaving q = 0.7*0.8 = 0.56; got {result!r}"
    )


def test_empty_candidate_citations_stay_vacuously_valid_when_reference_cites_nothing(
    template_vault: Path,
) -> None:
    # Neither the reference nor the candidate cites: there was nothing to cite, so
    # the guard does not fire and integrity's vacuous 1.0 stands -> q = 0.86.
    # Same qa (0.8) and same empty candidate citations as the case above; only the
    # reference's citations differ -- proving the guard actually reads gold.citations.
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    result = metric(_gold([]), _prediction([]))

    assert result == pytest.approx(0.86), (
        "with no reference citations to demand, an uncited candidate stays "
        f"vacuously valid (cite=1.0) -> q = 0.7*0.8 + 0.3*1.0 = 0.86; got {result!r}"
    )


def test_resolvable_candidate_citations_flow_through_as_the_integrity_fraction(
    template_vault: Path,
) -> None:
    # The candidate cites real sources (one resolvable, one phantom) -> the guard
    # is inert and the integrity fraction (0.5) flows straight into the citation
    # term, against a real citation resolvable in template_vault.
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    result = metric(_gold([STORED_SOURCE_KEY]), _prediction([STORED_SOURCE_KEY, "ghost2099none"]))

    assert result == pytest.approx(0.71), (
        "one of two candidate citations resolves (cite=0.5) -> "
        f"q = 0.7*0.8 + 0.3*0.5 = 0.71; got {result!r}"
    )


# --------------------------------------------------------------------------- #
# Judge integration: qa comes from judge.grade; parse failures are never silent
# --------------------------------------------------------------------------- #


def test_the_qa_leg_is_sourced_from_the_judge_grade_call(template_vault: Path) -> None:
    # A distinctive judge score must show up in the composition (not a hardcoded
    # qa), and the judge must actually be consulted for it.
    fake = _judge_fake(0.2)
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=fake,
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    result = metric(_gold(["someref2020"]), _prediction([STORED_SOURCE_KEY]))

    assert result == pytest.approx(0.44), (
        "the qa leg must be the judge's score (0.2), so q = 0.7*0.2 + 0.3*1.0 = 0.44; "
        f"got {result!r}"
    )
    assert fake.call_count >= 1, "the judge must be consulted to produce the qa leg"


def test_a_judge_parse_failure_propagates_and_is_never_swallowed_to_zero(
    template_vault: Path,
) -> None:
    # An unparseable judge response is an instrument failure, distinct from a
    # legitimate 0.0 grade. The scorer must let JudgeParseError surface rather than
    # quietly composing a 0.0 qa leg and corrupting the scalar.
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_garbage_judge(),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    with pytest.raises(JudgeParseError):
        metric(_gold(["someref2020"]), _prediction([STORED_SOURCE_KEY]))


# --------------------------------------------------------------------------- #
# No hardcoded snapshot: the bound snapshot flows to every judge call
# --------------------------------------------------------------------------- #


def test_the_bound_judge_snapshot_flows_through_to_every_llm_call(template_vault: Path) -> None:
    # The snapshot is a bound argument, never hardcoded in the scorer: whatever
    # snapshot the binding carries must be exactly what reaches the client.
    fake = _judge_fake(0.8)
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=fake,
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    metric(_gold(["someref2020"]), _prediction([STORED_SOURCE_KEY]))

    assert fake.calls, "the judge must be called at least once to grade the candidate"
    assert all(call.snapshot == JUDGE_SNAPSHOT for call in fake.calls), (
        "every judge call must use the bound snapshot, proving it is not hardcoded; "
        f"got {[call.snapshot for call in fake.calls]!r}"
    )


# --------------------------------------------------------------------------- #
# Determinism: identical inputs produce an identical score
# --------------------------------------------------------------------------- #


def test_the_same_inputs_produce_the_same_score(template_vault: Path) -> None:
    metric = _bind_metric(
        store=_store(template_vault),
        topic=TOPIC,
        llm_client=_judge_fake(0.8),
        judge_snapshot=JUDGE_SNAPSHOT,
    )
    gold = _gold(["someref2020"])
    prediction = _prediction([STORED_SOURCE_KEY])

    first = metric(gold, prediction)
    second = metric(gold, prediction)

    assert first == second == pytest.approx(0.86), (
        f"scoring identical inputs twice must be bit-for-bit stable; got {first!r} then {second!r}"
    )
