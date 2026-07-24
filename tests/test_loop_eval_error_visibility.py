"""Behavioral spec for the single-writer eval-outcome aggregator in
:mod:`knotica.core.loop` (``harness_evaluate``) and the ``wiki_status``
passthrough of the accumulating ``examples`` list.

``loop_progress.write_progress``/``read_progress`` already accept and round-trip
an ``examples`` list (td-013's primitive fix), and ``run_eval``/``build_metric``
already accept an ``on_outcome`` callback (the runner/judge capture seams).
What is still missing -- and what this suite pins RED for -- is ``loop.py``'s
own aggregation layer: an in-memory ``outcomes`` list plus one lock in
``harness_evaluate``, threaded into ``run_eval`` as ``on_outcome``, so every
progress write (whether triggered by an outcome, an example-start, or a
substage heartbeat) carries the FULL accumulated list, coherently, across
dspy's concurrent scoring threads.

RED signal: ``harness_evaluate`` today calls ``run_eval(...)`` without an
``on_outcome=`` keyword at all, so a fake ``run_eval`` that receives the
callback ``run_eval`` was actually called with sees ``on_outcome=None`` and a
call like ``on_outcome(id, status, error_class, detail)`` raises ``TypeError:
'NoneType' object is not callable`` -- the natural signal that the aggregator
closure does not exist yet. The one exception is the wiki_status-passthrough
test below, which pins already-existing behavior (component 6 needs no new
code -- see its own docstring) and is expected to pass today.

No live evals, no model calls anywhere in this file -- every ``run_eval`` call
is monkeypatched to a fake that only exercises the callback contract.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from knotica.core.loop import harness_evaluate
from knotica.core.loop_progress import read_progress, write_progress
from knotica.core.status import gather_wiki_status
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"

#: More writers than the 4 dspy scoring threads td-013's coherence half was
#: observed under -- mirrors tests/test_loop_progress.py's own CONCURRENT_WRITERS
#: rationale so a same-instant burst stresses the aggregator's lock, not just
#: the file-write primitive underneath it.
CONCURRENT_OUTCOMES = 16


def _fake_result(source_root: Path) -> SimpleNamespace:
    """Minimal stand-in for a harness ``EvalRunResult`` -- only the attributes
    :func:`knotica.core.loop.wrap_harness_result` reads via ``getattr``."""
    return SimpleNamespace(
        record=SimpleNamespace(
            scalar=0.9,
            generation=1,
            harness_version="test-harness",
            corpus_ref="corpus-1",
        ),
        clone_root=source_root,
    )


def test_concurrent_outcomes_from_many_threads_all_land_with_no_lost_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coherence half of td-013: a same-instant burst of ``on_outcome`` calls
    (mirrors dspy's scoring threads, at 4x their count) must all survive into
    the final written ``examples`` list. A lock that only guards the write
    itself (not the read-append-write as one unit) would silently drop entries
    under contention -- this is a direct regression proof, not an approximation.
    """
    captured: dict[str, list[dict[str, str]]] = {}

    def _fake_run_eval(
        topic: str,
        *,
        source_root: Path,
        ref: str | None,
        on_example=None,
        on_substage=None,
        on_outcome=None,
        **overrides: object,
    ) -> SimpleNamespace:
        barrier = threading.Barrier(CONCURRENT_OUTCOMES)

        def _emit(i: int) -> None:
            barrier.wait(timeout=10)
            on_outcome(f"q{i}", "ok", "", "")

        with ThreadPoolExecutor(max_workers=CONCURRENT_OUTCOMES) as pool:
            futures = [pool.submit(_emit, i) for i in range(CONCURRENT_OUTCOMES)]
            for future in futures:
                future.result(timeout=15)  # re-raises here if any writer thread raised

        # Snapshot on-disk state here: harness_evaluate's `finally` clears the
        # progress entry the instant this fake returns.
        payload = read_progress(source_root, topic)
        captured["examples"] = list(payload["examples"]) if payload else []
        return _fake_result(source_root)

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)

    harness_evaluate(TOPIC, tmp_path, None)

    ids = [entry["id"] for entry in captured["examples"]]
    assert len(ids) == CONCURRENT_OUTCOMES, (
        f"expected all {CONCURRENT_OUTCOMES} concurrently-recorded outcomes to land in the "
        f"final snapshot, got {len(ids)}: {sorted(ids)}"
    )
    assert len(set(ids)) == CONCURRENT_OUTCOMES, (
        f"accumulated examples must not contain duplicate ids: {sorted(ids)}"
    )


def test_a_substage_heartbeat_write_carries_the_full_accumulated_examples_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every writer -- not just ``on_outcome`` itself -- must compose the FULL
    snapshot. A substage heartbeat fired after two outcomes are already
    recorded must still carry both of them in its own write, proving the
    snapshot is read fresh from the shared accumulator under the lock rather
    than being scoped to the triggering event's own single-outcome payload.
    """
    captured: dict[str, list[dict[str, str]] | None] = {"substage_examples": None}

    def _fake_run_eval(
        topic: str,
        *,
        source_root: Path,
        ref: str | None,
        on_example=None,
        on_substage=None,
        on_outcome=None,
        **overrides: object,
    ) -> SimpleNamespace:
        on_outcome("q1", "ok", "", "")
        on_outcome("q2", "error", "rate_limit_429", "HTTP 429")
        on_substage("judging", 1, 1)
        payload = read_progress(source_root, topic)
        captured["substage_examples"] = list(payload["examples"]) if payload else None
        return _fake_result(source_root)

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)

    harness_evaluate(TOPIC, tmp_path, None)

    examples = captured["substage_examples"]
    assert examples is not None, "the substage heartbeat write left no progress entry to read"
    ids = {entry["id"] for entry in examples}
    assert ids == {"q1", "q2"}, (
        "a substage heartbeat write must carry every outcome recorded so far, not just its own "
        f"triggering event's data; got ids={ids}"
    )


def test_progress_entry_is_cleared_after_a_successful_run_with_recorded_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_run_eval(
        topic: str,
        *,
        source_root: Path,
        ref: str | None,
        on_example=None,
        on_substage=None,
        on_outcome=None,
        **overrides: object,
    ) -> SimpleNamespace:
        on_outcome("q1", "ok", "", "")
        return _fake_result(source_root)

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)

    harness_evaluate(TOPIC, tmp_path, None)

    assert read_progress(tmp_path, TOPIC) is None, (
        "a completed run must clear its progress entry even though outcomes were recorded"
    )


def test_progress_entry_is_still_cleared_when_the_run_raises_after_recording_an_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SimulatedAbort(RuntimeError):
        pass

    def _fake_run_eval(
        topic: str,
        *,
        source_root: Path,
        ref: str | None,
        on_example=None,
        on_substage=None,
        on_outcome=None,
        **overrides: object,
    ) -> SimpleNamespace:
        on_outcome("q1", "error", "rate_limit_429", "HTTP 429")
        raise _SimulatedAbort("simulated run abort")

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)

    with pytest.raises(_SimulatedAbort):
        harness_evaluate(TOPIC, tmp_path, None)

    assert read_progress(tmp_path, TOPIC) is None, (
        "the progress entry must still be cleared on the failure path (existing `finally` "
        "behavior), even once outcomes are being recorded"
    )


def test_a_failing_and_succeeding_mixed_run_records_distinct_outcomes_per_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof: a fake ``run_eval`` simulating one rate-limited example
    among several successes leaves the correct per-example classification in
    the progress file before ``clear_progress`` removes it. Zero live model
    calls anywhere in this test.
    """
    captured: dict[str, list[dict[str, str]]] = {}

    def _fake_run_eval(
        topic: str,
        *,
        source_root: Path,
        ref: str | None,
        on_example=None,
        on_substage=None,
        on_outcome=None,
        **overrides: object,
    ) -> SimpleNamespace:
        on_outcome("q1", "ok", "", "")
        on_outcome("q2", "error", "rate_limit_429", "HTTP 429: rate limited")
        on_outcome("q3", "ok", "", "")
        payload = read_progress(source_root, topic)
        captured["examples"] = list(payload["examples"]) if payload else []
        return _fake_result(source_root)

    monkeypatch.setattr("knotica.evals.harness.run_eval", _fake_run_eval)

    harness_evaluate(TOPIC, tmp_path, None)

    by_id = {entry["id"]: entry for entry in captured["examples"]}
    assert by_id["q1"]["status"] == "ok"
    assert by_id["q3"]["status"] == "ok"
    assert by_id["q2"]["status"] == "error", "the rate-limited example must be recorded as error"
    assert by_id["q2"]["error_class"] == "rate_limit_429"


def test_wiki_status_progress_examples_reflects_a_written_progress_file(
    template_vault: Path,
) -> None:
    """``gather_wiki_status``'s ``loop.progress`` passthrough (component 6) needs
    no new code -- ``read_progress`` already flows into the payload wholesale
    (see ``status.py``'s ``_gate_and_loop``). This pins that claim directly
    instead of assuming it, and is expected to pass today (not RED) -- unlike
    every other test in this file, it exercises no part of the not-yet-built
    aggregator.
    """
    store = LocalFSStore(template_vault)
    outcomes = [
        {"id": "q1", "status": "ok", "error_class": "", "detail": ""},
        {"id": "q2", "status": "error", "error_class": "rate_limit_429", "detail": "HTTP 429"},
    ]
    write_progress(template_vault, TOPIC, phase="evaluating", current=2, total=5, examples=outcomes)

    payload = gather_wiki_status(store, template_vault, topic=TOPIC)

    assert payload["loop"]["progress"]["examples"] == outcomes
