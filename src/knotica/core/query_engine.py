"""Unified wiki query facade — one answer path for MCP, Ask pane, and Arena.

Public consumers call :func:`answer_question`. When a healthy compiled artifact
is present, :class:`~knotica.evals.compiled_runner.CompiledRunner` serves;
otherwise :class:`~knotica.evals.runner.MessagesApiRunner`. Engine identity is
never part of the public envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knotica.core.compiled import is_compiled_healthy, load_compiled
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import TopicNotFoundError
from knotica.evals.cache import ResponseCache
from knotica.evals.compiled_runner import CompiledRunner
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.llm import AnthropicClient, LLMClient
from knotica.evals.runner import DEFAULT_MAX_PAGES, BaselineRunner, MessagesApiRunner, Prediction
from knotica.search import RipgrepBackend
from knotica.search.retrieval import retrieve_search_results
from knotica.store import VaultStore

__all__ = ["QueryResult", "answer_question", "select_runner"]


@dataclass(frozen=True, slots=True)
class QueryResult:
    """User-visible answer payload (no engine / compile fields)."""

    answer: str
    citations: list[str]
    pages_used: list[str]
    topic: str
    question: str

    def render(self) -> dict[str, Any]:
        """JSON object for MCP / dashboard consumers."""
        return {
            "topic": self.topic,
            "question": self.question,
            "answer": self.answer,
            "citations": list(self.citations),
            "pages_used": list(self.pages_used),
        }


def select_runner(
    store: VaultStore,
    topic: str,
    *,
    llm_client: LLMClient | None = None,
    worker_snapshot: str = WORKER_SNAPSHOT,
    cache: ResponseCache | None = None,
) -> BaselineRunner:
    """Prefer a healthy compiled artifact; otherwise the baseline runner."""
    client = llm_client if llm_client is not None else AnthropicClient()
    artifact = load_compiled(store, topic)
    if is_compiled_healthy(artifact) and artifact is not None:
        return CompiledRunner(artifact, client, worker_snapshot=worker_snapshot, cache=cache)
    return MessagesApiRunner(client, worker_snapshot, cache=cache)


def answer_question(
    store: VaultStore,
    topic: str,
    question: str,
    *,
    llm_client: LLMClient | None = None,
    worker_snapshot: str = WORKER_SNAPSHOT,
    cache: ResponseCache | None = None,
    runner: BaselineRunner | None = None,
) -> QueryResult:
    """Answer ``question`` for ``topic`` via the unified query facade.

    Raises:
        TopicNotFoundError: When ``topic`` is missing or malformed.
        KnoticaError: ``LLM_API_ERROR`` when synthesis fails.
        ValueError: When ``question`` is empty.
    """
    cleaned_topic = topic.strip().strip("/")
    cleaned_question = question.strip()
    if not cleaned_question:
        raise ValueError("question must be a non-empty string")
    if not cleaned_topic or "/" in cleaned_topic:
        raise TopicNotFoundError(topic or "(empty)")
    if not store.exists(cleaned_topic):
        raise TopicNotFoundError(cleaned_topic)

    active = runner or select_runner(
        store,
        cleaned_topic,
        llm_client=llm_client,
        worker_snapshot=worker_snapshot,
        cache=cache,
    )
    try:
        prediction: Prediction = active.run(store, cleaned_topic, cleaned_question)
    except KnoticaError:
        raise
    except Exception as exc:  # noqa: BLE001 — map transport/model failures
        raise KnoticaError(
            ErrorCode.LLM_API_ERROR,
            f"query failed because the model call did not complete: {exc}",
            fix="Check credentials / rate limits, then retry the same question.",
        ) from exc

    pages = _retrieve_page_paths(store, cleaned_topic, cleaned_question)
    return QueryResult(
        answer=prediction.answer,
        citations=list(prediction.citations),
        pages_used=pages,
        topic=cleaned_topic,
        question=cleaned_question,
    )


def _retrieve_page_paths(store: VaultStore, topic: str, question: str) -> list[str]:
    """Deterministic top-K page paths (same retrieve as the baseline runner)."""
    root = getattr(store, "root", None)
    if root is None:
        return []
    backend = RipgrepBackend(Path(root))
    results = retrieve_search_results(backend, topic, question, limit=DEFAULT_MAX_PAGES)
    return [result.path for result in results]
