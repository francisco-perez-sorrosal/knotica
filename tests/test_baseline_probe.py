"""Naive zero cold-start probe — persist and status surfacing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from knotica.core.baseline_probe import (
    NAIVE_COLD_START_SCALAR,
    baseline_probe_eligible,
    maybe_auto_baseline_probe,
    run_baseline_probe,
    topic_exists_for_probe,
)
from knotica.core.errors import KnoticaError
from knotica.core.metrics import (
    BASELINE_PROBE_HARNESS_VERSION,
    LEGACY_BASELINE_PROBE_HARNESS_VERSION,
    LEGACY_BASELINE_PROBE_HARNESS_VERSIONS,
    append_metrics_record,
    build_baseline_probe_record,
    read_last_metrics,
)
from support.trainset import populate_query_trainset
from knotica.core.status import gather_wiki_status
from knotica.evals.golden import load as load_golden
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def test_naive_cold_start_scalar_is_zero() -> None:
    assert NAIVE_COLD_START_SCALAR == 0.0


def test_run_baseline_probe_persists_zero(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    assert topic_exists_for_probe(store, TOPIC)
    assert read_last_metrics(store, TOPIC) is None

    result = run_baseline_probe(store, template_vault, TOPIC)
    assert result.persisted is True
    assert result.harness_version == BASELINE_PROBE_HARNESS_VERSION
    assert result.runner_mode == "zero_anchor"
    assert result.scalar == pytest.approx(0.0)
    assert result.n_examples == 0

    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.harness_version == BASELINE_PROBE_HARNESS_VERSION
    assert last.scalar == pytest.approx(0.0)
    assert last.artifact_ref == "baseline-probe:zero_anchor"


def test_wiki_status_surfaces_probe_scalar(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    run_baseline_probe(store, template_vault, TOPIC)

    body = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert body["topics"][0]["last_eval"] is not None
    assert body["topics"][0]["last_eval"]["harness_version"] == BASELINE_PROBE_HARNESS_VERSION
    assert body["gate"]["last_scalar"] == pytest.approx(0.0)


def test_baseline_probe_ignores_golden_and_train(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)
    assert len(load_golden(store, TOPIC)) >= 1

    result = run_baseline_probe(store, template_vault, TOPIC)
    assert result.scalar == pytest.approx(0.0)


def test_run_baseline_probe_rejects_missing_topic(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with pytest.raises(KnoticaError):
        run_baseline_probe(store, template_vault, "no-such-topic")


def test_baseline_probe_eligible_false_when_current_metrics_exist(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    assert baseline_probe_eligible(store, TOPIC) is True

    run_baseline_probe(store, template_vault, TOPIC)
    assert baseline_probe_eligible(store, TOPIC) is False


def test_baseline_probe_eligible_without_golden_or_train(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    for relative in (
        f"{TOPIC}/.knotica/datasets/golden.jsonl",
        f"{TOPIC}/.knotica/datasets/qa.jsonl",
    ):
        absolute = template_vault / relative
        if absolute.exists():
            absolute.unlink()
    assert baseline_probe_eligible(store, TOPIC) is True


def test_maybe_auto_baseline_probe_skips_when_metrics_exist(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    run_baseline_probe(store, template_vault, TOPIC)

    skipped = maybe_auto_baseline_probe(store, template_vault, TOPIC)
    assert skipped is None
    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.generation == 1


def test_maybe_auto_baseline_probe_runs_once_topic_has_datasets(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    assert read_last_metrics(store, TOPIC) is None

    populate_query_trainset(store, template_vault, TOPIC)
    probe = maybe_auto_baseline_probe(store, template_vault, TOPIC)

    assert probe is not None
    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.harness_version == BASELINE_PROBE_HARNESS_VERSION
    assert last.scalar == pytest.approx(0.0)


def test_baseline_probe_eligible_when_only_legacy_probe(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    for harness in (
        LEGACY_BASELINE_PROBE_HARNESS_VERSION,
        "lexical-cold-start-train",
        "retrieval-cold-start",
    ):
        assert harness in LEGACY_BASELINE_PROBE_HARNESS_VERSIONS

    legacy = build_baseline_probe_record(
        TOPIC,
        0.71875,
        generation=1,
        n_examples=32,
        corpus_ref="git:legacy",
        runner_mode="retrieval_hit",
        harness_version="retrieval-cold-start",
    )
    append_metrics_record(
        store,
        template_vault,
        TOPIC,
        legacy,
        operation="baseline_probe",
        title="legacy retrieval probe",
    )
    assert baseline_probe_eligible(store, TOPIC) is True


def test_maybe_auto_baseline_probe_runs_after_freeze(template_vault: Path) -> None:
    from knotica.evals.golden import freeze

    store = LocalFSStore(template_vault)
    with patch("knotica.core.baseline_probe.maybe_auto_baseline_probe", return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)
    metrics = template_vault / TOPIC / ".knotica" / "metrics.jsonl"
    if metrics.exists():
        metrics.unlink()

    golden = load_golden(store, TOPIC)
    accepted = [
        {
            "question": record.query,
            "reference_answer": record.answer,
            "citations": list(record.citations),
            "pages_used": list(record.pages_used),
        }
        for record in golden
    ]

    freeze(store, template_vault, TOPIC, accepted)
    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.harness_version == BASELINE_PROBE_HARNESS_VERSION
    assert last.scalar == pytest.approx(0.0)
