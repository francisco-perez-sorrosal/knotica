"""Trainset helpers for Phase 3a — query-style curated counts and compile gates."""

from __future__ import annotations

from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord, RecordParseError, parse_qa_jsonl
from knotica.store import VaultStore

__all__ = [
    "count_query_train_examples",
    "is_query_train_example",
    "load_query_train_examples",
]


def is_query_train_example(record: QARecord) -> bool:
    """Whether ``record`` counts toward compile-ready (query flywheel, not ingest)."""
    if record.verdict not in {"good", "corrected"}:
        return False
    query = record.query.strip().lower()
    if query.startswith("ingest ") or query.startswith("ingest\t"):
        return False
    return True


def load_query_train_examples(store: VaultStore, topic: str) -> list[QARecord]:
    """Load ``qa.jsonl`` rows that are valid query-train examples."""
    path = qa_dataset_path(topic)
    if not store.exists(path):
        return []
    try:
        records = parse_qa_jsonl(store.read_text(path))
    except RecordParseError:
        return []
    return [record for record in records if is_query_train_example(record)]


def count_query_train_examples(store: VaultStore, topic: str) -> int:
    """Count query-style train examples for compile gating / status."""
    return len(load_query_train_examples(store, topic))
