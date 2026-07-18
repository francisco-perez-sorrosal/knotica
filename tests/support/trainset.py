"""Test factory: populate a topic's trainset (and golden set) with synthetic records.

Replaces the removed demo-era ``core.seed_train`` for test setup: content is
generated, generic, and clearly fixture-shaped — no real-world Q&A baked in.
Records use the production ``curate_example`` source and land through
``VaultTransaction`` exactly like the flywheel would write them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord, body_sha256, parse_qa_jsonl
from knotica.core.scrub import scrub
from knotica.core.transaction import VaultTransaction
from knotica.evals.golden import (
    GOLDEN_SPLIT,
    golden_dataset_path,
    golden_manifest_path,
)
from knotica.store import VaultStore

__all__ = ["populate_query_trainset"]

_DEFAULT_TRAIN_N = 30
_DEFAULT_GOLDEN_N = 20


def _record(topic: str, kind: str, index: int, created: str) -> QARecord:
    return QARecord(
        id=f"fixture-{kind}-{index:04d}",
        topic=topic,
        created=created,
        query=f"Fixture {kind} question {index:04d} about {topic}?",
        pages_used=(),
        answer=f"Fixture {kind} answer {index:04d} for {topic}.",
        citations=(),
        verdict="good",
        corrected_answer=None,
        source="curate_example",
        model="fixture",
    )


def populate_query_trainset(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    *,
    train_n: int = _DEFAULT_TRAIN_N,
    golden_if_missing: bool = True,
    golden_n: int = _DEFAULT_GOLDEN_N,
) -> dict[str, int]:
    """Append ``train_n`` synthetic query-train records; seed golden when absent.

    Train and golden questions are disjoint by construction (distinct ``kind``
    slots in the generated text). Existing ``qa.jsonl`` rows are preserved.
    """
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    dataset_path = qa_dataset_path(topic)
    existing = parse_qa_jsonl(store.read_text(dataset_path)) if store.exists(dataset_path) else []
    train_records = [_record(topic, "train", i, created) for i in range(1, train_n + 1)]
    train_body = "".join(record.to_json_line() + "\n" for record in existing + train_records)
    writes: list[tuple[str, str]] = [(dataset_path, train_body)]

    seeded_golden = 0
    if golden_if_missing and not store.exists(golden_dataset_path(topic)):
        golden_records = [_record(topic, "golden", i, created) for i in range(1, golden_n + 1)]
        golden_text = "".join(record.to_json_line() + "\n" for record in golden_records)
        scrubbed, _spans = scrub(golden_text)
        manifest = {
            "sha256": body_sha256(scrubbed),
            "version": datetime.now(UTC).strftime("%Y-%m-%d"),
            "source": "curate_example",
            "split": GOLDEN_SPLIT,
            "size": len(golden_records),
        }
        writes.append((golden_dataset_path(topic), golden_text))
        writes.append(
            (golden_manifest_path(topic), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        )
        seeded_golden = len(golden_records)

    with VaultTransaction(
        store, Path(vault_root), "curate_example", topic, "fixture trainset"
    ) as txn:
        for path, content in writes:
            txn.write(path, content)

    return {"train_appended": len(train_records), "golden_seeded": seeded_golden}
