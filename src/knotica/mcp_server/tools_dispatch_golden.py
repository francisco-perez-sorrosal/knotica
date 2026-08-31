"""Operator dispatcher ``golden`` — action-parameterized routing over
``golden_review_load`` and ``golden_review_save`` in
:mod:`knotica.mcp_server.tools_golden`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same semantics. Registered on ``server.py``;
the governing two-tier tool-surface ADRs live in ``.ai-state/decisions/``.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault, resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.golden_review import load_golden_review, save_golden_review
from knotica.mcp_server import envelope, tool_params
from knotica.mcp_server.dispatch_telemetry import record_rejected_action
from knotica.mcp_server.tools_golden import _EXCEPTIONS, _parse_accepted
from knotica.mcp_server.vault_ctx import vault_arg
from knotica.store import LocalFSStore

__all__ = ["register_dispatch_golden_tools"]

ToolResult = CallToolResult

_DISPATCHER = "golden"
_ACTIONS = ("load", "save")

_GoldenAction = Annotated[
    str,
    tool_params.grounded(
        "'load' reads the sealed golden set; 'save' writes the accepted examples back to it.",
        _ACTIONS,
    ),
]

_AcceptedJson = Annotated[
    str,
    tool_params.grounded(
        "JSON array of accepted golden examples, as a string. Required by "
        "golden_action=save; ignored by 'load'.",
    ),
]

_GOLDEN_DISPATCH_DESCRIPTION = (
    "Load and save the sealed golden set's review board. Operator-tier and "
    "rarely conversational; on the published surface this is reached as "
    "`improve action=golden` with `golden_action` selecting the operation. "
    "`golden_action=load` reads the review board for one topic — staging "
    "candidates, citation resolution, page deep links, qa.jsonl duplicate flags "
    "(read-only). `golden_action=save` commits the kept candidates as "
    "golden.staging.reviewed.jsonl; pass accepted_json as a JSON array of "
    "candidate objects (question, reference_answer, citations, pages_used; "
    "optional support). "
    "Does NOT: synthesize the candidates it reviews (`improve action=datasets` "
    "does), and does NOT freeze the reviewed set into golden.jsonl — freezing "
    "is a separate, explicit step. "
    "Requires: an explicit topic and a bootstrapped staging file. Pass vault to "
    "select a configured vault. `golden_action=save` never fires from detection "
    "alone -- only after the user has explicitly confirmed the review; an "
    "unconfirmed detection routes to `golden_action=load` or an offer instead. "
    "Returns: the board as data, including which candidates already duplicate a "
    "trainset entry — the two sets must stay disjoint."
)


def register_dispatch_golden_tools(mcp: FastMCP) -> None:
    """Register the ``golden`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="golden", description=_GOLDEN_DISPATCH_DESCRIPTION)
    def golden(
        action: _GoldenAction,
        topic: tool_params.Topic,
        accepted_json: _AcceptedJson = "",
        vault: tool_params.Vault = "",
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
