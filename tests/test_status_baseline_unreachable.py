"""``wiki_status`` must say when the gate baseline outranks the corpus itself.

A baseline above the default branch's own measured scalar is always a
configuration error, and it is a *silent* one: every candidate and every arena
variant is refused, each refusal's diff blames the content under test, and
``gate.state: "fail"`` reads as a verdict on the submission rather than on the
topic. The two numbers that prove it were reported on different surfaces --
``gate.baseline`` from loop-state and the measured scalar from
``metrics.jsonl`` -- so nothing named the condition.

These pin ``loop.baseline_unreachable``: the object that names it, and the four
distinct reasons the finding is deliberately withheld.
"""

from __future__ import annotations

from pathlib import Path

from knotica.core.metrics import append_metrics_record
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.loop_state import empty_loop_state, write_loop_state
from knotica.core.status import gather_wiki_status
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"
HARNESS = "test-harness"


def _record(
    scalar: float,
    *,
    generation: int = 1,
    n_examples: int = 21,
    harness_version: str = HARNESS,
) -> MetricsRecord:
    return MetricsRecord(
        topic=TOPIC,
        timestamp=f"2026-08-08T05:41:{generation:02d}Z",
        generation=generation,
        harness_version=harness_version,
        scalar=scalar,
        components=MetricsComponents(
            qa_accuracy=0.55,
            citation_validity=0.77,
            lint_violations=2.0,
            token_cost=1.0,
        ),
        n_examples=n_examples,
        corpus_ref="git:" + "b" * 40,
        artifact_ref=None,
    )


def _seed(
    vault: Path,
    record: MetricsRecord,
    *,
    baseline: float | None,
    baseline_harness: str | None = HARNESS,
) -> LocalFSStore:
    """Write one metrics record and freeze ``baseline`` into loop-state."""
    store = LocalFSStore(vault)
    append_metrics_record(store, vault, TOPIC, record, operation="loop", title="seed metrics")
    state = empty_loop_state(TOPIC).model_copy(
        update={
            "baseline_scalar": baseline,
            "baseline_harness_version": baseline_harness,
        }
    )
    write_loop_state(store, vault, state, title="seed baseline")
    return store


def _unreachable(vault: Path, store: LocalFSStore) -> dict | None:
    payload = gather_wiki_status(store, vault, topic=TOPIC)
    return payload["loop"]["baseline_unreachable"]


def test_names_both_scalars_when_the_baseline_outranks_the_corpus(
    template_vault: Path,
) -> None:
    """The reported session's exact numbers: a 0.9548 bar over a 0.6562 corpus."""
    store = _seed(template_vault, _record(0.6562261904761905), baseline=0.9548055555555556)

    finding = _unreachable(template_vault, store)

    assert finding is not None, "a bar the corpus cannot clear must be reported"
    assert finding["baseline"] == 0.9548055555555556
    assert finding["last_scalar"] == 0.6562261904761905
    assert "0.9548" in finding["message"] and "0.6562" in finding["message"]
    # The remedy has to name the mode: `rebaseline` defaults to `best`, which on
    # a regressed topic re-freezes the bar already in place.
    assert "rebaseline" in finding["fix"] and "latest" in finding["fix"]


def test_silent_when_the_corpus_clears_its_own_baseline(template_vault: Path) -> None:
    store = _seed(template_vault, _record(0.72), baseline=0.65)

    assert _unreachable(template_vault, store) is None


def test_an_equal_baseline_is_reachable(template_vault: Path) -> None:
    """The gate passes on ``>=``, so an exactly-equal bar is clearable."""
    store = _seed(template_vault, _record(0.65), baseline=0.65)

    assert _unreachable(template_vault, store) is None


def test_withheld_across_a_harness_change(template_vault: Path) -> None:
    """Cross-instrument scalars are not orderable -- unknown, not unreachable."""
    store = _seed(
        template_vault,
        _record(0.30, harness_version="harness-v2"),
        baseline=0.95,
        baseline_harness="harness-v1",
    )

    assert _unreachable(template_vault, store) is None


def test_withheld_against_a_zero_example_probe_anchor(template_vault: Path) -> None:
    """A ``baseline-probe`` record measures nothing; ranking against it is noise."""
    store = _seed(template_vault, _record(0.0, n_examples=0), baseline=0.95)

    assert _unreachable(template_vault, store) is None


def test_withheld_before_any_baseline_is_frozen(template_vault: Path) -> None:
    store = _seed(template_vault, _record(0.42), baseline=None)

    assert _unreachable(template_vault, store) is None


def test_reads_the_corpus_scalar_not_a_refused_candidates_score(
    template_vault: Path,
) -> None:
    """``state.last_scalar`` holds the *candidate's* scalar after a refusal.

    Reading the fallback chain :func:`status._last_known_scalar` walks would
    report a healthy topic as unreachable for as long as its last candidate
    happened to score below the bar. Here the corpus (0.97) clears the bar
    (0.95) while the last refused candidate (0.50) does not -- the finding must
    stay silent.
    """
    store = LocalFSStore(template_vault)
    append_metrics_record(
        store,
        template_vault,
        TOPIC,
        _record(0.97),
        operation="loop",
        title="seed metrics",
    )
    state = empty_loop_state(TOPIC).model_copy(
        update={
            "baseline_scalar": 0.95,
            "baseline_harness_version": HARNESS,
            "last_scalar": 0.50,
            "last_harness_version": HARNESS,
        }
    )
    write_loop_state(store, template_vault, state, title="refused candidate")

    assert _unreachable(template_vault, store) is None
