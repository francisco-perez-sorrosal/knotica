"""Headless query retrieval must surface concept pages, not only sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.search import RipgrepBackend
from knotica.search.retrieval import question_to_search_query, retrieve_search_results

HUXLEY_QUESTION = (
    "What is clade-level metaproductivity in the Huxley-Gödel Machine vs Darwin Gödel Machine?"
)
HUXLEY_PAGE = "agentic-systems/huxley-godel-machine.md"
DARWIN_PAGE = "agentic-systems/darwin-godel-machine.md"


@pytest.fixture
def cmp_vault(tmp_path: Path) -> Path:
    """Minimal topic vault: two concept pages plus one term-noisy source."""
    topic_dir = tmp_path / "agentic-systems"
    source_dir = tmp_path / "sources" / "agentic-systems"
    topic_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)

    (topic_dir / "huxley-godel-machine.md").write_text(
        "\n".join(
            [
                "---",
                "title: Huxley-Gödel Machine",
                "confidence: high",
                "status: current",
                "---",
                "",
                "Clade-level metaproductivity (CMP) scores descendant potential.",
                "The Huxley-Gödel Machine uses CMP instead of immediate benchmark gain.",
            ]
        ),
        encoding="utf-8",
    )
    (topic_dir / "darwin-godel-machine.md").write_text(
        "\n".join(
            [
                "---",
                "title: Darwin Gödel Machine",
                "confidence: high",
                "status: current",
                "---",
                "",
                "The Darwin Gödel Machine archives self-modifications validated on benchmarks.",
                "It selects by immediate benchmark score rather than clade-level metaproductivity.",
            ]
        ),
        encoding="utf-8",
    )
    # Large source dominated by generic terms from the question — mimics live vault noise.
    filler = " ".join(["Machine", "Gödel", "Darwin", "benchmark"] * 400)
    (source_dir / "noisy-dgm-blob.md").write_text(
        "\n".join(
            [
                "---",
                "origin: test",
                "retrieved: 2026-07-17T00:00:00Z",
                "---",
                "",
                filler,
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_question_to_search_query_strips_glue_words() -> None:
    assert question_to_search_query(HUXLEY_QUESTION) == (
        "clade-level metaproductivity Huxley-Gödel Machine Darwin Gödel Machine"
    )


def test_retrieve_includes_concept_pages_for_huxley_question(cmp_vault: Path) -> None:
    backend = RipgrepBackend(cmp_vault)
    results = retrieve_search_results(backend, "agentic-systems", HUXLEY_QUESTION, limit=5)
    paths = [result.path for result in results]
    assert HUXLEY_PAGE in paths
    assert DARWIN_PAGE in paths
    assert any(result.kind == "page" for result in results)


def test_retrieve_prefers_pages_when_sources_score_higher(cmp_vault: Path) -> None:
    backend = RipgrepBackend(cmp_vault)
    results = retrieve_search_results(backend, "agentic-systems", HUXLEY_QUESTION, limit=5)
    page_paths = [result.path for result in results if result.kind == "page"]
    assert len(page_paths) >= 2
    assert HUXLEY_PAGE in page_paths
    assert DARWIN_PAGE in page_paths
