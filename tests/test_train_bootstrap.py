"""Trainset cold-start: page-grounded seeding, contamination guard, curation ratchet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knotica.core.errors import KnoticaError
from knotica.core.records import QARecord
from knotica.core.trainset import count_query_train_examples, load_query_train_examples
from knotica.evals.llm import Completion, FakeLLMClient, Message, TokenUsage
from knotica.evals.train_bootstrap import SEED_SOURCE, bootstrap_trainset
from knotica.programs.query import _demos_from_train
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"
SNAPSHOT = "test-worker-snapshot"


def _pairs_completion(prefix: str, count: int) -> Completion:
    payload = [
        {"question": f"{prefix} question {i}?", "answer": f"{prefix} answer {i}."}
        for i in range(1, count + 1)
    ]
    return Completion(text=json.dumps(payload), usage=TokenUsage(input_tokens=10, output_tokens=50))


def test_bootstrap_seeds_from_pages_with_seed_source(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    client = FakeLLMClient([_pairs_completion("alpha", 5), _pairs_completion("beta", 5)])

    pages_seen: list[tuple[int, int, str]] = []
    result = bootstrap_trainset(
        store,
        template_vault,
        TOPIC,
        client,
        SNAPSHOT,
        target_n=8,
        per_page=5,
        on_page=lambda i, total, page: pages_seen.append((i, total, page)),
    )

    assert result["appended"] == 8
    records = load_query_train_examples(store, TOPIC)
    seeded = [record for record in records if record.source == SEED_SOURCE]
    assert len(seeded) == 8
    assert all(record.model == SNAPSHOT for record in seeded)
    assert all(record.pages_used for record in seeded), "each seed cites its source page"
    assert count_query_train_examples(store, TOPIC) >= 8
    # The real AnthropicClient reads .role off each message — typed Message,
    # never a raw dict (a dict passes the fake but crashes production).
    assert all(
        isinstance(message, Message) for call in client.calls for message in call.messages
    )
    assert pages_seen, "per-page progress callback must fire"
    assert [i for i, _, _ in pages_seen] == list(range(1, len(pages_seen) + 1))
    assert all(page.endswith(".md") for _, _, page in pages_seen)


def test_bootstrap_excludes_questions_already_in_trainset(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    first = FakeLLMClient(_pairs_completion("gamma", 5))
    bootstrap_trainset(store, template_vault, TOPIC, first, SNAPSHOT, target_n=5, per_page=5)

    # Same candidates again: every question now duplicates the trainset.
    second = FakeLLMClient(_pairs_completion("gamma", 5))
    with pytest.raises(KnoticaError, match="no new records"):
        bootstrap_trainset(store, template_vault, TOPIC, second, SNAPSHOT, target_n=5, per_page=5)


def test_demo_selection_prefers_curated_over_seeded() -> None:
    def _record(index: int, source: str) -> QARecord:
        return QARecord(
            id=f"r{index}",
            topic=TOPIC,
            created="2026-07-18T00:00:00Z",
            query=f"Ratchet question {index} ({source})?",
            pages_used=(),
            answer=f"Answer {index}.",
            citations=(),
            verdict="good",
            corrected_answer=None,
            source=source,
            model="test",
        )

    seeded_first = [
        _record(1, SEED_SOURCE),
        _record(2, SEED_SOURCE),
        _record(3, "curate_example"),
        _record(4, "curate_example"),
    ]
    demos = _demos_from_train(seeded_first)
    assert [demo.question for demo in demos[:2]] == [
        "Ratchet question 3 (curate_example)?",
        "Ratchet question 4 (curate_example)?",
    ], "curated records fill demo slots before cold-start seeds"
