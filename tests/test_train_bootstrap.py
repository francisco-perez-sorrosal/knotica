"""Trainset cold-start: page-grounded seeding, contamination guard, curation ratchet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knotica.core.errors import KnoticaError
from knotica.core.operations.create_topic import qa_dataset_path
from knotica.core.records import QARecord
from knotica.core.trainset import count_query_train_examples, load_query_train_examples
from knotica.evals.golden import entity_pages
from knotica.evals.llm import Completion, FakeLLMClient, Message, TokenUsage
from knotica.evals.train_bootstrap import SEED_SOURCE, bootstrap_trainset
from knotica.programs.query import _demos_from_train
from knotica.store import LocalFSStore
from support.vault import git_commit_count

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
    assert all(isinstance(message, Message) for call in client.calls for message in call.messages)
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


# --------------------------------------------------------------------------- #
# page-subset filter -- restricting which entity pages seed new records
# --------------------------------------------------------------------------- #


def test_bootstrap_with_explicit_pages_none_covers_every_entity_page(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    all_pages = entity_pages(store, TOPIC)

    client = FakeLLMClient([_pairs_completion(f"page{i}", 2) for i in range(len(all_pages))])
    bootstrap_trainset(
        store, template_vault, TOPIC, client, SNAPSHOT, target_n=50, per_page=2, pages=None
    )

    seeded_pages = {
        page for record in load_query_train_examples(store, TOPIC) for page in record.pages_used
    }
    assert seeded_pages == {page.path for page in all_pages}, (
        "an explicit pages=None still reaches every entity page, the same as omitting the "
        "argument entirely"
    )


def test_bootstrap_restricts_new_records_to_the_given_page_subset(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    all_pages = entity_pages(store, TOPIC)
    assert len(all_pages) >= 2, "the fixture vault must offer at least two entity pages"
    target_page = all_pages[0].path
    excluded_pages = {page.path for page in all_pages[1:]}

    client = FakeLLMClient(_pairs_completion("subset", 3))
    bootstrap_trainset(
        store,
        template_vault,
        TOPIC,
        client,
        SNAPSHOT,
        target_n=3,
        per_page=3,
        pages=[target_page],
    )

    seeded = [
        record for record in load_query_train_examples(store, TOPIC) if record.source == SEED_SOURCE
    ]
    assert seeded, "the targeted page still produces new seed records"
    assert all(record.pages_used == (target_page,) for record in seeded), (
        "every new record is grounded in the single page named by the subset"
    )
    assert not any(page in record.pages_used for record in seeded for page in excluded_pages), (
        "pages outside the subset contribute no new records"
    )


def test_bootstrap_with_a_page_subset_leaves_curated_records_untouched(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    all_pages = entity_pages(store, TOPIC)
    assert len(all_pages) >= 2, "the fixture vault must offer at least two entity pages"
    target_page, other_page = all_pages[0].path, all_pages[1].path

    curated = QARecord(
        id="curated-0001",
        topic=TOPIC,
        created="2026-01-01T00:00:00Z",
        query="What does a human curator already know about this topic?",
        pages_used=(other_page,),
        answer="A human-curated answer, never touched by cold-start seeding.",
        citations=(),
        verdict="good",
        corrected_answer=None,
        source="curate_example",
        model="human",
    )
    dataset_file = template_vault / qa_dataset_path(TOPIC)
    dataset_file.parent.mkdir(parents=True, exist_ok=True)
    dataset_file.write_text(curated.to_json_line() + "\n", encoding="utf-8")

    client = FakeLLMClient(_pairs_completion("restricted", 3))
    bootstrap_trainset(
        store,
        template_vault,
        TOPIC,
        client,
        SNAPSHOT,
        target_n=3,
        per_page=3,
        pages=[target_page],
    )

    records = load_query_train_examples(store, TOPIC)
    assert curated in records, (
        "the pre-existing curated record survives a page-restricted bootstrap unchanged"
    )
    new_seeds = [record for record in records if record.source == SEED_SOURCE]
    assert new_seeds, "the targeted page still produces new seed records alongside the curated one"
    assert all(record.pages_used == (target_page,) for record in new_seeds)


def test_bootstrap_with_an_empty_page_list_appends_nothing_without_raising(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    seed_client = FakeLLMClient(_pairs_completion("baseline", 3))
    bootstrap_trainset(store, template_vault, TOPIC, seed_client, SNAPSHOT, target_n=3, per_page=3)
    before = load_query_train_examples(store, TOPIC)
    commits_before = git_commit_count(template_vault)

    client = FakeLLMClient(_pairs_completion("unused", 1))
    result = bootstrap_trainset(store, template_vault, TOPIC, client, SNAPSHOT, pages=[])

    assert result["appended"] == 0, (
        "requesting an empty page subset is a deliberate no-op, not a duplicate-content failure"
    )
    assert client.calls == [], "no pages means the worker model is never called"
    assert load_query_train_examples(store, TOPIC) == before, (
        "the existing trainset is left exactly as it was"
    )
    assert git_commit_count(template_vault) == commits_before, (
        "a no-op page subset must not create an empty vault commit"
    )
