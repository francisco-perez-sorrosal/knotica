"""Read helpers for ``<topic>/.knotica/metrics.jsonl``.

The eval harness *writes* metrics on a clone via :class:`~knotica.core.transaction.VaultTransaction`
(dec-015). This module is the shared *read* path for the MCP dashboard tools,
the CLI, and (later) the loop runner — always through :class:`~knotica.store.VaultStore`,
never a hardcoded vault path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from knotica.core.records import MetricsRecord, RecordParseError
from knotica.store import VaultStore

__all__ = [
    "METRICS_FILENAME",
    "metrics_path",
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
