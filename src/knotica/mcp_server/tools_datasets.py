"""Dataset inventory / pipeline tools — bootstrap → review → freeze.

Complements ``golden_review_load`` / ``golden_review_save`` with inventory,
capped record loads, bootstrap, and freeze-from-reviewed.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import resolve
from knotica.core.datasets_inventory import (
    bootstrap_dataset_candidates,
    freeze_reviewed_dataset,
    gather_datasets_inventory,
    load_dataset_records,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.golden import GoldenCandidateError, GoldenSetContaminationError
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import deprecation_suffix, record_deprecated_alias
from knotica.mcp_server.vault_ctx import vault_arg, with_resolved_vault
from knotica.store import LocalFSStore

__all__ = ["register_datasets_tools"]

ToolResult = CallToolResult

_INVENTORY_DESCRIPTION = (
    "Summarize all topic datasets under .knotica/datasets/ with UI role labels "
    "(Trainset=qa.jsonl, Held-out=golden.jsonl, Seal=MANIFEST.json, "
    "Candidates=golden.staging.jsonl, Reviewed=golden.staging.reviewed.jsonl), "
    "readiness flags, and train↔held-out/reviewed overlap counts. Read-only."
)

_RECORDS_DESCRIPTION = (
    "Load capped records for one dataset role: trainset | held_out | seal | "
    "candidates | reviewed. Read-only. Pass limit to cap rows (default 200)."
)

_BOOTSTRAP_DESCRIPTION = (
    "Synthesize golden Candidates (golden.staging.jsonl) from topic entity pages "
    "via the worker LLM. Does not freeze. Requires CLAUDE_CODE_OAUTH_TOKEN or "
    "ANTHROPIC_API_KEY. Uncommitted staging write."
)

_BOOTSTRAP_TRAIN_DESCRIPTION = (
    "Cold-start a topic's trainset: synthesize query-style QA pairs grounded in "
    "the topic's own entity pages via the worker LLM and append them to qa.jsonl "
    "with source seed_train (one git commit). Deduplicates against the existing "
    "trainset and refuses questions colliding with the held-out golden set. "
    "Curated examples displace seeds in compile demo selection as the flywheel "
    "fills. Requires CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY."
)

_FREEZE_DESCRIPTION = (
    "Freeze Reviewed candidates (golden.staging.reviewed.jsonl) into held-out "
    "golden.jsonl + MANIFEST.json (one git commit). Refuses trainset contamination. "
    "Prefer Reviewed count ≥ 20 before calling."
)

_EXCEPTIONS = (
    KnoticaError,
    TopicNotFoundError,
    PageNotFoundError,
    GoldenSetContaminationError,
    GoldenCandidateError,
)


def register_datasets_tools(mcp: FastMCP) -> None:
    """Register dataset inventory / bootstrap / freeze tools on ``mcp``."""

    @mcp.tool(
        name="datasets_inventory",
        description=_INVENTORY_DESCRIPTION + deprecation_suffix("datasets_inventory"),
    )
    def datasets_inventory(topic: str, vault: str = "") -> ToolResult:
        record_deprecated_alias("datasets_inventory")
        return with_resolved_vault(
            vault,
            lambda store, _resolved: gather_datasets_inventory(store, topic),
        )

    @mcp.tool(
        name="datasets_records",
        description=_RECORDS_DESCRIPTION + deprecation_suffix("datasets_records"),
    )
    def datasets_records(topic: str, role: str, limit: int = 200, vault: str = "") -> ToolResult:
        record_deprecated_alias("datasets_records")
        return with_resolved_vault(
            vault,
            lambda store, _resolved: load_dataset_records(store, topic, role, limit=limit),
        )

    @mcp.tool(
        name="datasets_bootstrap",
        description=_BOOTSTRAP_DESCRIPTION + deprecation_suffix("datasets_bootstrap"),
    )
    def datasets_bootstrap(topic: str, vault: str = "") -> ToolResult:
        record_deprecated_alias("datasets_bootstrap")
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = _bootstrap_payload(store, topic)
        except _EXCEPTIONS as exc:
            return _map_exception(exc)
        return envelope.success_result(payload)

    @mcp.tool(
        name="datasets_bootstrap_train",
        description=_BOOTSTRAP_TRAIN_DESCRIPTION + deprecation_suffix("datasets_bootstrap_train"),
    )
    def datasets_bootstrap_train(topic: str, target: int = 30, vault: str = "") -> ToolResult:
        record_deprecated_alias("datasets_bootstrap_train")
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = _bootstrap_train_payload(store, resolved.path, topic, target)
        except _EXCEPTIONS as exc:
            return _map_exception(exc)
        return envelope.success_result(payload)

    @mcp.tool(
        name="datasets_freeze",
        description=_FREEZE_DESCRIPTION + deprecation_suffix("datasets_freeze"),
    )
    def datasets_freeze(topic: str, vault: str = "") -> ToolResult:
        record_deprecated_alias("datasets_freeze")
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = freeze_reviewed_dataset(store, resolved.path, topic)
        except _EXCEPTIONS as exc:
            return _map_exception(exc)
        return envelope.success_result(payload)


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
