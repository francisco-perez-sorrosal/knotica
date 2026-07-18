"""Unified QueryEngine — facade over MessagesApiRunner with no engine disclosure."""

from __future__ import annotations

import json

import pytest

from knotica.core.page import TopicNotFoundError
from knotica.core.query_engine import answer_question
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.llm import Completion, FakeLLMClient, TokenUsage
from knotica.evals.runner import MessagesApiRunner
from knotica.store import LocalFSStore

RETRIEVED_SOURCE_KEY = "wang2024awm"


def _completion(answer: str, citations: list[str] | None = None) -> Completion:
    return Completion(
        text=json.dumps({"answer": answer, "citations": citations or [RETRIEVED_SOURCE_KEY]}),
        usage=TokenUsage(input_tokens=10, output_tokens=20),
    )


def test_answer_question_returns_user_visible_fields_only(template_vault) -> None:
    store = LocalFSStore(template_vault)
    fake = FakeLLMClient([_completion("Workflow memory abstracts reusable routines.")])
    runner = MessagesApiRunner(llm_client=fake, worker_snapshot=WORKER_SNAPSHOT)
    result = answer_question(
        store,
        "agentic-systems",
        "What is agent workflow memory?",
        runner=runner,
    )
    payload = result.render()
    assert payload["answer"].startswith("Workflow memory")
    assert payload["citations"] == [RETRIEVED_SOURCE_KEY]
    assert payload["topic"] == "agentic-systems"
    assert "engine" not in payload
    assert "dspy" not in payload
    assert "compiled" not in payload
    assert isinstance(payload["pages_used"], list)


def test_answer_question_rejects_unknown_topic(template_vault) -> None:
    store = LocalFSStore(template_vault)
    fake = FakeLLMClient([_completion("unused")])
    runner = MessagesApiRunner(llm_client=fake, worker_snapshot=WORKER_SNAPSHOT)
    with pytest.raises(TopicNotFoundError):
        answer_question(store, "no-such-topic", "Anything?", runner=runner)


def test_answer_question_rejects_empty_question(template_vault) -> None:
    store = LocalFSStore(template_vault)
    with pytest.raises(ValueError, match="non-empty"):
        answer_question(store, "agentic-systems", "   ")
