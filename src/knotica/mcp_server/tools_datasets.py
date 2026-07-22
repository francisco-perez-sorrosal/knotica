"""Dataset bootstrap payload helpers for the ``datasets`` action dispatcher.

These functions have no MCP tool registrations of their own — they are
imported directly by ``tools_dispatch_datasets.py``, the sole entry point
into this logic (which also imports the inventory/records/freeze functions
directly from ``knotica.core.datasets_inventory``).
"""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult

from knotica.core.datasets_inventory import bootstrap_dataset_candidates
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.golden import GoldenCandidateError, GoldenSetContaminationError
from knotica.mcp_server import envelope

ToolResult = CallToolResult

_EXCEPTIONS = (
    KnoticaError,
    TopicNotFoundError,
    PageNotFoundError,
    GoldenSetContaminationError,
    GoldenCandidateError,
)


def _bootstrap_payload(store: Any, topic: str) -> dict[str, Any]:
    from knotica.evals.llm import AnthropicClient

    client = AnthropicClient()
    return bootstrap_dataset_candidates(
        store,
        topic,
        llm_client=client,
        snapshot=WORKER_SNAPSHOT,
    )


def _bootstrap_train_payload(
    store: Any, vault_path: Any, topic: str, target: int
) -> dict[str, Any]:
    from pathlib import Path

    from knotica.core.loop_progress import clear_progress, write_progress
    from knotica.evals.llm import AnthropicClient
    from knotica.evals.train_bootstrap import bootstrap_trainset

    root = Path(vault_path)
    cleaned = topic.strip().strip("/")

    def _on_page(current: int, total: int, page_path: str) -> None:
        # Same runtime progress channel the loop eval uses; the dashboard polls
        # it via wiki_status and renders "synthesizing page k/M".
        write_progress(
            root,
            cleaned,
            phase="bootstrap-train",
            current=current,
            total=total,
            detail=page_path,
        )

    try:
        return bootstrap_trainset(
            store,
            vault_path,
            topic,
            AnthropicClient(),
            WORKER_SNAPSHOT,
            target_n=max(1, int(target)),
            on_page=_on_page,
        )
    finally:
        clear_progress(root, cleaned)


def _map_exception(exc: BaseException) -> ToolResult:
    if isinstance(exc, KnoticaError):
        return envelope.error_envelope(exc)
    if isinstance(exc, GoldenSetContaminationError):
        return envelope.error_envelope(exc)
    if isinstance(exc, GoldenCandidateError):
        return envelope.error_envelope(
            KnoticaError(
                ErrorCode.INVALID_FRONTMATTER,
                str(exc),
                fix="Re-run bootstrap; if it persists, check entity pages and LLM output.",
            )
        )
    if isinstance(exc, (TopicNotFoundError, PageNotFoundError)):
        return envelope.map_read_exception(exc)
    return envelope.map_read_exception(exc)
