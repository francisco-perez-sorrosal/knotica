"""Read helpers for ``<topic>/.knotica/metrics.jsonl``.

The eval harness *writes* metrics on a clone via :class:`~knotica.core.transaction.VaultTransaction`
(dec-015). This module is the shared *read* path for the MCP dashboard tools,
the CLI, and (later) the loop runner — always through :class:`~knotica.store.VaultStore`,
never a hardcoded vault path.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from knotica.core.records import MetricsComponents, MetricsRecord, RecordParseError
from knotica.core.transaction import VaultTransaction
from knotica.store import VaultStore

__all__ = [
    "BASELINE_PROBE_HARNESS_VERSION",
    "COMPILE_METRICS_HARNESS_VERSION",
    "LEGACY_BASELINE_PROBE_HARNESS_VERSION",
    "LEGACY_BASELINE_PROBE_HARNESS_VERSIONS",
    "METRICS_FILENAME",
    "append_metrics_record",
    "build_baseline_probe_record",
    "build_compile_metrics_record",
    "metrics_path",
    "next_metrics_generation",
    "read_last_metrics",
    "read_metrics_window",
    "render_metrics_record",
]

#: Directory name under each topic that owns loop/eval artifacts.
_KNOTICA_DIR = ".knotica"

#: Basename of the per-topic eval-history file (frozen by dec-006).
METRICS_FILENAME = "metrics.jsonl"

#: Default window size for charting reads.
DEFAULT_METRICS_LIMIT = 100

#: Hard ceiling so a runaway client cannot ask for the whole history at once.
MAX_METRICS_LIMIT = 1000

#: Harness label for compile post-eval scalars promoted onto the live vault.
COMPILE_METRICS_HARNESS_VERSION = "compile-post-eval"

#: Harness label for naive zero cold-start probes (``baseline_probe`` tool).
#: Fixed scalar 0.0 — no golden, no train Q&A, no LLM, no retrieval scoring.
BASELINE_PROBE_HARNESS_VERSION = "naive-cold-start"

#: Legacy harness tags (measured probes); display-only, not gate quality.
LEGACY_BASELINE_PROBE_HARNESS_VERSION = "lexical-cold-start"
LEGACY_BASELINE_PROBE_HARNESS_VERSIONS = frozenset(
    {
        "lexical-cold-start",
        "lexical-cold-start-train",
        "retrieval-cold-start",
    }
)


def metrics_path(topic: str) -> str:
    """Vault-relative path of a topic's ``metrics.jsonl``."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."}:
        raise ValueError(f"topic must be a single path segment, got {topic!r}")
    return f"{cleaned}/{_KNOTICA_DIR}/{METRICS_FILENAME}"


def render_metrics_record(record: MetricsRecord) -> dict[str, Any]:
    """Render one :class:`MetricsRecord` as a JSON-object (schema field order)."""
    return {
        "schema_version": record.schema_version,
        "topic": record.topic,
        "timestamp": record.timestamp,
        "generation": record.generation,
        "harness_version": record.harness_version,
        "scalar": record.scalar,
        "components": {
            "qa_accuracy": record.components.qa_accuracy,
            "citation_validity": record.components.citation_validity,
            "lint_violations": record.components.lint_violations,
            "token_cost": record.components.token_cost,
        },
        "n_examples": record.n_examples,
        "corpus_ref": record.corpus_ref,
        "artifact_ref": record.artifact_ref,
    }


def next_metrics_generation(store: VaultStore, topic: str) -> int:
    """Return the next 1-based generation number for ``topic``."""
    window = read_metrics_window(store, topic, limit=MAX_METRICS_LIMIT)
    records = window["records"]
    if not records:
        return 1
    return max(record.generation for record in records) + 1


def build_baseline_probe_record(
    topic: str,
    scalar: float,
    *,
    generation: int,
    n_examples: int,
    corpus_ref: str,
    runner_mode: str,
    harness_version: str = BASELINE_PROBE_HARNESS_VERSION,
) -> MetricsRecord:
    """Build one metrics line for a naive zero cold-start probe."""
    return MetricsRecord(
        topic=topic.strip().strip("/"),
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        generation=generation,
        harness_version=harness_version,
        scalar=float(scalar),
        components=MetricsComponents(
            qa_accuracy=float(scalar),
            citation_validity=0.0,
            lint_violations=0.0,
            token_cost=0.0,
        ),
        n_examples=n_examples,
        corpus_ref=corpus_ref,
        artifact_ref=f"baseline-probe:{runner_mode}",
    )


def build_compile_metrics_record(
    topic: str,
    scalar: float,
    *,
    merge_sha: str,
    generation: int,
    n_examples: int = 0,
    harness_version: str = COMPILE_METRICS_HARNESS_VERSION,
) -> MetricsRecord:
    """Build one metrics line for a promoted compile generation."""
    return MetricsRecord(
        topic=topic.strip().strip("/"),
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        generation=generation,
        harness_version=harness_version,
        scalar=float(scalar),
        components=MetricsComponents(
            qa_accuracy=float(scalar),
            citation_validity=1.0,
            lint_violations=0.0,
            token_cost=0.0,
        ),
        n_examples=n_examples,
        corpus_ref=f"git:{merge_sha}",
        artifact_ref=None,
    )


def append_metrics_record(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    record: MetricsRecord,
    *,
    operation: str,
    title: str,
) -> MetricsRecord:
    """Append one metrics line under the vault lock (one commit)."""
    path = metrics_path(topic)
    existing = store.read_text(path) if store.exists(path) else ""
    body = _append_jsonl_line(existing, record.to_json_line())
    with VaultTransaction(store, Path(vault_root), operation, topic, title) as txn:
        txn.write(path, body)
    return record


def read_last_metrics(store: VaultStore, topic: str) -> MetricsRecord | None:
    """Return the newest parseable metrics record for ``topic``, or ``None``.

    Absent file, empty file, and all-malformed content all yield ``None`` —
    "not yet evaluated" is data, not an error.
    """
    window = read_metrics_window(store, topic, limit=1)
    records = window["records"]
    return records[-1] if records else None


def read_metrics_window(
    store: VaultStore,
    topic: str,
    *,
    limit: int = DEFAULT_METRICS_LIMIT,
    before_generation: int | None = None,
) -> dict[str, Any]:
    """Return a window of metrics records for charting.

    Windowing is from the newest end of history: take up to ``limit`` records
    with ``generation < before_generation`` (when set), then return them in
    **ascending** generation order so a line chart can plot left→right.

    Returns a dict with ``records`` (:class:`MetricsRecord` instances),
    ``has_more``, ``next_before_generation``, and ``skipped_malformed``.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    if limit > MAX_METRICS_LIMIT:
        raise ValueError(f"limit must be <= {MAX_METRICS_LIMIT}, got {limit}")
    if before_generation is not None and before_generation < 0:
        raise ValueError(f"before_generation must be >= 0, got {before_generation}")

    path = metrics_path(topic)
    if not store.exists(path):
        return {
            "records": [],
            "has_more": False,
            "next_before_generation": None,
            "skipped_malformed": 0,
        }

    parsed, skipped = _parse_all(store.read_text(path))
    if before_generation is not None:
        parsed = [r for r in parsed if r.generation < before_generation]

    # Newest-first window, then reverse for ascending chart order.
    parsed.sort(key=lambda r: r.generation)
    if len(parsed) > limit:
        window = parsed[-limit:]
        has_more = True
        next_before = window[0].generation
    else:
        window = parsed
        has_more = False
        next_before = None

    return {
        "records": window,
        "has_more": has_more,
        "next_before_generation": next_before,
        "skipped_malformed": skipped,
    }


def render_metrics_window(
    store: VaultStore,
    topic: str,
    *,
    limit: int = DEFAULT_METRICS_LIMIT,
    before_generation: int | None = None,
) -> dict[str, Any]:
    """Like :func:`read_metrics_window` but with records rendered as plain dicts."""
    window = read_metrics_window(store, topic, limit=limit, before_generation=before_generation)
    return {
        "topic": topic.strip().strip("/"),
        "records": [render_metrics_record(r) for r in window["records"]],
        "has_more": window["has_more"],
        "next_before_generation": window["next_before_generation"],
        "skipped_malformed": window["skipped_malformed"],
    }


def _append_jsonl_line(existing_text: str, line: str) -> str:
    """Append one JSONL line, preserving prior records and a single trailing newline."""
    if not existing_text.strip():
        return line + "\n"
    return existing_text.rstrip("\n") + "\n" + line + "\n"


def _parse_all(text: str) -> tuple[list[MetricsRecord], int]:
    """Parse every non-blank line; skip malformed lines and count them."""
    records: list[MetricsRecord] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(MetricsRecord.from_json_line(line))
        except (RecordParseError, ValueError, TypeError):
            skipped += 1
    return records, skipped


def last_eval_summary(record: MetricsRecord | None) -> Mapping[str, Any] | None:
    """Compact ``last_eval`` object for ``wiki_status`` (or ``None``)."""
    if record is None:
        return None
    return render_metrics_record(record)
