"""Golden-set review tools — ``golden_review_load`` / ``golden_review_save``.

Thin MCP adapters over :mod:`knotica.core.golden_review`. Load is read-only;
save commits ``golden.staging.reviewed.jsonl`` through ``VaultTransaction``.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.golden_review import load_golden_review, save_golden_review
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.mcp_server import envelope
from knotica.mcp_server.vault_ctx import vault_arg
from knotica.store import LocalFSStore

__all__ = ["register_golden_tools"]

ToolResult = CallToolResult

_LOAD_DESCRIPTION = (
    "Load the golden-set review board for one topic: staging (or previously "
    "reviewed) candidates, citation resolution against sources/, page deep links, "
    "and qa.jsonl duplicate flags. Read-only. Pass vault to select a configured "
    "vault name. Run after `knotica eval --bootstrap`."
)

_SAVE_DESCRIPTION = (
    "Save the kept golden-set candidates as golden.staging.reviewed.jsonl for "
    "one topic (one git commit). Pass accepted_json as a JSON array of candidate "
    "objects (question, reference_answer, citations, pages_used; optional support). "
    "Pass vault to select a configured vault name."
)

_EXCEPTIONS = (KnoticaError, TopicNotFoundError, PageNotFoundError)


def register_golden_tools(mcp: FastMCP) -> None:
    """Register golden-review tools on ``mcp``."""

    @mcp.tool(name="golden_review_load", description=_LOAD_DESCRIPTION)
    def golden_review_load(topic: str, vault: str = "") -> ToolResult:
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = load_golden_review(store, resolved.path, topic, vault_name=resolved.name)
        except _EXCEPTIONS as exc:
            return envelope.map_read_exception(exc)
        return envelope.success_result(payload)

    @mcp.tool(name="golden_review_save", description=_SAVE_DESCRIPTION)
    def golden_review_save(topic: str, accepted_json: str, vault: str = "") -> ToolResult:
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        try:
            accepted = _parse_accepted(accepted_json)
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = save_golden_review(store, resolved.path, topic, accepted)
        except _EXCEPTIONS as exc:
            return envelope.map_read_exception(exc)
        return envelope.success_result(payload)


def _parse_accepted(accepted_json: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(accepted_json)
    except json.JSONDecodeError as exc:
        raise KnoticaError(
            code=ErrorCode.INVALID_FRONTMATTER,
            message=f"accepted_json is not valid JSON: {exc}",
            fix="Pass a JSON array of candidate objects.",
        ) from exc
    if not isinstance(payload, list):
        raise KnoticaError(
            code=ErrorCode.INVALID_FRONTMATTER,
            message="accepted_json must be a JSON array of candidates",
            fix="Pass a JSON array of candidate objects.",
        )
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise KnoticaError(
                code=ErrorCode.INVALID_FRONTMATTER,
                message=f"accepted_json[{index}] is not an object",
                fix="Each candidate must be a JSON object.",
            )
        rows.append(item)
    return rows
