"""``LoopRunner.rebaseline`` -- re-freezing the gate baseline from history.

Split from ``tests/test_loop_runner.py`` when the freeze-time guard landed (the
size ratchet holds both files under the ceiling): rebaseline is its own concern
-- no eval, no candidate, pure metrics-history selection -- and its behavioral
turn is worth a file-level record. ``mode=best`` re-picks the high-water mark
*among reachable bars*: a field report showed a 0.9581 bar frozen over a corpus
measuring 0.8923, failing every candidate and arena variant by construction,
so both freeze-time entry points that could create that state now refuse
(``rebaseline`` and ``set_baseline`` share one refusal).

Zero network: no evaluate is ever called; history is seeded directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from knotica.core.loop import LoopRunner, wrap_harness_result
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.vcs import VaultVcs
from knotica.evals.harness import EvalRunResult
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"


def _fake_evaluate(scalar: float):
    """Constructor-parity stand-in; rebaseline never calls it."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        dest = Path(tempfile.mkdtemp(prefix="knotica-rebaseline-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-17T00:00:00Z",
            generation=1,
            harness_version="fake-m2",
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate


def _seed_metrics_history(vault: Path, scalars: list[float], harness: str = "fake-m2") -> None:
    """Write a metrics.jsonl history directly (generation = list order)."""
    path = vault / TOPIC / ".knotica" / "metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for generation, scalar in enumerate(scalars, start=1):
        record = MetricsRecord(
            topic=TOPIC,
            timestamp=f"2026-07-18T00:0{generation}:00Z",
            generation=generation,
            harness_version=harness,
            scalar=scalar,
            components=MetricsComponents(
                qa_accuracy=scalar, citation_validity=1.0, lint_violations=0.0, token_cost=0.0
            ),
            n_examples=1,
            corpus_ref="git:seeded",
            artifact_ref=None,
        )
        lines.append(record.to_json_line())
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: seed metrics history")


def test_rebaseline_freezes_high_water_mark_from_history(template_vault: Path) -> None:
    _seed_metrics_history(template_vault, [0.60, 0.70, 0.90])
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    state = runner.rebaseline("best")
    assert state.baseline_scalar == 0.90, "rebaseline best freezes the high-water mark"


def test_rebaseline_latest_freezes_the_newest_record(template_vault: Path) -> None:
    _seed_metrics_history(template_vault, [0.60, 0.90, 0.70])
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    state = runner.rebaseline("latest")
    assert state.baseline_scalar == 0.70, "rebaseline latest freezes the newest record"


def test_rebaseline_refuses_a_high_water_mark_above_the_newest_measurement(
    template_vault: Path,
) -> None:
    """A bar above what the branch currently measures fails every candidate by
    construction -- the exact `baseline_unreachable` misconfiguration a field
    report showed jamming a whole topic. `best` used to freeze it anyway
    ("re-freezes the value already in place"); it now refuses at the one
    freeze-time entry points that can create the state, naming both scalars and
    the reachable alternative."""
    from knotica.core.errors import ErrorCode, KnoticaError

    _seed_metrics_history(template_vault, [0.60, 0.90, 0.70])
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    with pytest.raises(KnoticaError) as caught:
        runner.rebaseline("best")

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert "0.9000" in str(caught.value) and "0.7000" in str(caught.value)
    assert "latest" in caught.value.fix, "the refusal must name the reachable mode"


def test_rebaseline_ignores_records_from_previous_instruments(template_vault: Path) -> None:
    # A stale 0.99 under an old instrument must never become the bar.
    path = template_vault / TOPIC / ".knotica" / "metrics.jsonl"
    _seed_metrics_history(template_vault, [0.99], harness="old-instrument")
    old_line = path.read_text(encoding="utf-8")
    _seed_metrics_history(template_vault, [0.60, 0.80])
    path.write_text(old_line + path.read_text(encoding="utf-8"), encoding="utf-8")

    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    state = runner.rebaseline("best")
    assert state.baseline_scalar == 0.80, "only current-instrument records are comparable"


def test_the_rebaseline_payload_reports_when_the_bar_did_not_move(template_vault: Path) -> None:
    # Without `changed`, re-selecting the record already frozen is
    # indistinguishable from a call that failed -- which is exactly how it got
    # read in the field. (The old variant of this test froze a high-water mark
    # above the newest measurement; that is now refused, see the refusal test.)
    from knotica.core.errors import ErrorCode, KnoticaError
    from knotica.mcp_server.tools_vault import _loop_rebaseline_payload

    _seed_metrics_history(template_vault, [0.65, 0.95])
    store = LocalFSStore(template_vault)

    first = _loop_rebaseline_payload(store, template_vault, TOPIC, "best")
    assert first["baseline_scalar"] == 0.95
    assert first["changed"] is True

    again = _loop_rebaseline_payload(store, template_vault, TOPIC, "best")
    assert again["changed"] is False, "re-selecting the same record is not a change"
    assert "unchanged" in again["message"]

    _seed_metrics_history(template_vault, [0.65, 0.95, 0.80])
    lowered = _loop_rebaseline_payload(store, template_vault, TOPIC, "latest")
    assert lowered["previous_scalar"] == 0.95
    assert lowered["baseline_scalar"] == 0.80, "latest lowers the bar to the newest record"

    with pytest.raises(KnoticaError) as caught:
        _loop_rebaseline_payload(store, template_vault, TOPIC, "best")
    assert caught.value.code is ErrorCode.INVALID_ARGUMENT, (
        "the unreachable high-water pick surfaces as the typed refusal, not NOT_CONFIGURED"
    )


def test_set_baseline_refuses_a_bar_above_reachable_history(template_vault: Path) -> None:
    """The manual freeze jams the queue exactly as `rebaseline best` did.

    `set_baseline` is the *other* freeze-time entry point, and the field report
    that motivated the refusal (`0.9581` over a `0.8923` corpus) is reproducible
    verbatim through it, so both share one refusal.
    """
    from knotica.core.errors import ErrorCode, KnoticaError

    _seed_metrics_history(template_vault, [0.60, 0.80], harness="fake-instrument")
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    with pytest.raises(KnoticaError) as caught:
        runner.set_baseline(0.95, harness_version="fake-instrument")

    assert caught.value.code is ErrorCode.INVALID_ARGUMENT
    assert "0.9500" in str(caught.value) and "0.8000" in str(caught.value)


def test_set_baseline_allows_a_reachable_bar_and_records_the_instrument(
    template_vault: Path,
) -> None:
    _seed_metrics_history(template_vault, [0.60, 0.80], harness="fake-instrument")
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    state = runner.set_baseline(0.75, harness_version="fake-instrument")
    assert state.baseline_scalar == 0.75
    assert state.baseline_harness_version == "fake-instrument"


def test_set_baseline_skips_the_refusal_without_comparable_history(template_vault: Path) -> None:
    """No same-instrument history means no reachable bar to measure against --
    an unknowable bar is not a refusable one."""
    _seed_metrics_history(template_vault, [0.60], harness="some-other-instrument")
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    state = runner.set_baseline(0.99, harness_version="fake-instrument")
    assert state.baseline_scalar == 0.99


def test_set_baseline_no_longer_nulls_out_the_instrument(template_vault: Path) -> None:
    """A `None` fingerprint disarms both guards that key on it (`compute_gate`'s
    mismatch branch and `observe_default`'s re-freeze), so a manual freeze with
    no explicit instrument records the current one instead."""
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    from knotica.core.gate_inputs import current_harness_version

    state = runner.set_baseline(0.42)
    assert state.baseline_harness_version == current_harness_version()


def test_rebaseline_never_freezes_a_cold_start_probe_as_the_instrument(
    template_vault: Path,
) -> None:
    """A probe anchor is a fixed 0.0 under a synthetic label with no examples.
    Freezing at it would set a bar everything clears -- the ratchet silently
    gone -- while every read surface reports `unknown`."""
    from knotica.core.metrics import BASELINE_PROBE_HARNESS_VERSION

    path = template_vault / TOPIC / ".knotica" / "metrics.jsonl"
    _seed_metrics_history(template_vault, [0.80], harness="fake-instrument")
    real_line = path.read_text(encoding="utf-8")
    _seed_metrics_history(template_vault, [0.0], harness=BASELINE_PROBE_HARNESS_VERSION)
    path.write_text(real_line + path.read_text(encoding="utf-8"), encoding="utf-8")

    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)
    state = runner.rebaseline("best")
    assert state.baseline_scalar == 0.80, "the probe anchor is not an eval instrument"
    assert state.baseline_harness_version == "fake-instrument"


def test_rebaseline_raises_when_only_probe_anchors_exist(template_vault: Path) -> None:
    from knotica.core.metrics import BASELINE_PROBE_HARNESS_VERSION

    _seed_metrics_history(template_vault, [0.0], harness=BASELINE_PROBE_HARNESS_VERSION)
    runner = LoopRunner(template_vault, TOPIC, evaluate=_fake_evaluate(0.50), arena_enabled=False)

    with pytest.raises(ValueError, match="no metrics history"):
        runner.rebaseline("best")
