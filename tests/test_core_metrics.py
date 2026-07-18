"""Unit tests for :mod:`knotica.core.metrics` — windowed metrics.jsonl reads."""

from pathlib import Path

from knotica.core.metrics import (
    append_metrics_record,
    build_compile_metrics_record,
    metrics_path,
    next_metrics_generation,
    read_last_metrics,
    read_metrics_window,
    render_metrics_window,
)
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _record(generation: int, scalar: float = 0.5) -> MetricsRecord:
    return MetricsRecord(
        topic=TOPIC,
        timestamp=f"2026-07-17T12:00:{generation:02d}Z",
        generation=generation,
        harness_version="test-harness",
        scalar=scalar,
        components=MetricsComponents(
            qa_accuracy=0.7,
            citation_validity=1.0,
            lint_violations=0.0,
            token_cost=0.1,
        ),
        n_examples=10,
        corpus_ref="git:" + "a" * 40,
        artifact_ref=None,
    )


def _write_metrics(vault: Path, lines: list[str]) -> None:
    path = vault / TOPIC / ".knotica" / "metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_metrics_path_is_topic_relative() -> None:
    assert metrics_path(TOPIC) == f"{TOPIC}/.knotica/metrics.jsonl"


def test_absent_metrics_file_yields_empty_window(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    window = read_metrics_window(store, TOPIC, limit=10)
    assert window["records"] == []
    assert window["has_more"] is False
    assert window["next_before_generation"] is None
    assert window["skipped_malformed"] == 0
    assert read_last_metrics(store, TOPIC) is None


def test_window_returns_newest_limit_ascending(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _write_metrics(
        template_vault,
        [_record(g, scalar=0.5 + g * 0.01).to_json_line() for g in range(5)],
    )
    window = read_metrics_window(store, TOPIC, limit=3)
    gens = [r.generation for r in window["records"]]
    assert gens == [2, 3, 4]
    assert window["has_more"] is True
    assert window["next_before_generation"] == 2

    older = read_metrics_window(store, TOPIC, limit=3, before_generation=2)
    assert [r.generation for r in older["records"]] == [0, 1]
    assert older["has_more"] is False


def test_malformed_lines_are_skipped_and_counted(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    _write_metrics(
        template_vault,
        [
            _record(0).to_json_line(),
            "not-json",
            _record(1, scalar=0.57).to_json_line(),
            '{"schema_version":1}',  # missing required fields
        ],
    )
    rendered = render_metrics_window(store, TOPIC, limit=10)
    assert rendered["skipped_malformed"] == 2
    assert [r["generation"] for r in rendered["records"]] == [0, 1]
    assert rendered["records"][-1]["scalar"] == 0.57


def test_next_metrics_generation_starts_at_one(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    assert next_metrics_generation(store, TOPIC) == 1


def test_append_metrics_record_creates_file(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    record = build_compile_metrics_record(
        TOPIC,
        0.55,
        merge_sha="a" * 40,
        generation=1,
        n_examples=20,
    )
    append_metrics_record(
        store,
        template_vault,
        TOPIC,
        record,
        operation="compile",
        title="compile generation 1",
    )
    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.scalar == 0.55
    assert last.harness_version == "compile-post-eval"
    assert next_metrics_generation(store, TOPIC) == 2
