"""Operator dispatcher ``golden`` — action-parameterized routing over
``golden_review_load`` and ``golden_review_save`` in
:mod:`knotica.mcp_server.tools_golden`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same semantics. Not yet
registered on ``server.py`` — see ``dec-draft-ac2898b1``/``dec-draft-1785275a``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault, resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.golden_review import load_golden_review, save_golden_review
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_golden import _EXCEPTIONS, _parse_accepted
from knotica.mcp_server.vault_ctx import vault_arg
from knotica.store import LocalFSStore

__all__ = ["register_dispatch_golden_tools"]

ToolResult = CallToolResult

_DISPATCHER = "golden"
_ACTIONS = ("load", "save")

_GOLDEN_DISPATCH_DESCRIPTION = (
    "Operator golden-set review control (rarely conversational; the "
    "dashboard/CLI reach this directly). action=load reads the golden-set "
    "review board for one topic — staging candidates, citation resolution, "
    "page deep links, qa.jsonl duplicate flags (same as golden_review_load, "
    "read-only). action=save commits the kept candidates as "
    "golden.staging.reviewed.jsonl (same as golden_review_save); pass "
    "accepted_json as a JSON array of candidate objects (question, "
    "reference_answer, citations, pages_used; optional support). Pass vault "
    "to select a configured vault. action=save never fires from detection "
    "alone -- only after the user has explicitly confirmed the review; an "
    "unconfirmed detection routes to action=load or an offer instead."
)


def register_dispatch_golden_tools(mcp: FastMCP) -> None:
    """Register the ``golden`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="golden", description=_GOLDEN_DISPATCH_DESCRIPTION)
    def golden(
        action: str,
        topic: str,
        accepted_json: str = "",
        vault: str = "",
    ) -> ToolResult:
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = _dispatch_payload(store, resolved, action, topic, accepted_json)
        except _EXCEPTIONS as exc:
            return envelope.map_read_exception(exc)
        return envelope.success_result(payload)


def _dispatch_payload(
    store: LocalFSStore, resolved: ResolvedVault, action: str, topic: str, accepted_json: str
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "load":
        return load_golden_review(store, resolved.path, topic, vault_name=resolved.name)
    accepted = _parse_accepted(_require_accepted_json(accepted_json))
    return save_golden_review(store, resolved.path, topic, accepted)


def _require_accepted_json(accepted_json: str) -> str:
    if not accepted_json.strip():
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "golden action=save requires `accepted_json`",
            fix="Pass accepted_json as a JSON array of candidate objects.",
        )
    return accepted_json


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"golden action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned
