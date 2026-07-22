"""Operator dispatcher ``datasets`` — action-parameterized routing over
``datasets_inventory``, ``datasets_records``, ``datasets_bootstrap``,
``datasets_bootstrap_train``, and ``datasets_freeze`` in
:mod:`knotica.mcp_server.tools_datasets`.

Pure routing: every action calls the same payload builder / core function the
replaced thin tool called, with the same arguments and the same semantics.
Not yet registered on ``server.py`` — see
``dec-draft-ac2898b1``/``dec-draft-1785275a``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import resolve
from knotica.core.datasets_inventory import (
    freeze_reviewed_dataset,
    gather_datasets_inventory,
    load_dataset_records,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_datasets import (
    _EXCEPTIONS,
    _bootstrap_payload,
    _bootstrap_train_payload,
    _map_exception,
)
from knotica.mcp_server.vault_ctx import vault_arg
from knotica.store import LocalFSStore, VaultStore

__all__ = ["register_dispatch_datasets_tools"]

ToolResult = CallToolResult

_DISPATCHER = "datasets"
_ACTIONS = ("inventory", "records", "bootstrap", "bootstrap_train", "freeze")

_DATASETS_DISPATCH_DESCRIPTION = (
    "Operator dataset control (rarely conversational; the dashboard/CLI reach "
    "this directly). action=inventory summarizes all topic datasets under "
    ".knotica/datasets/ (same as datasets_inventory, read-only). "
    "action=records loads capped records for one dataset `role` "
    "(trainset|held_out|seal|candidates|reviewed; same as datasets_records, "
    "read-only; pass limit, default 200). action=bootstrap synthesizes golden "
    "Candidates via the worker LLM (same as datasets_bootstrap; does not "
    "freeze). action=bootstrap_train cold-starts the trainset from `target` "
    "synthesized QA pairs (same as datasets_bootstrap_train, default 30). "
    "action=freeze commits Reviewed candidates into held-out golden.jsonl "
    "(same as datasets_freeze). Pass vault to select a configured vault. "
    "action=bootstrap, action=bootstrap_train, and action=freeze never fire "
    "from detection alone -- only after the user has explicitly confirmed the "
    "mutation; an unconfirmed detection routes to action=inventory or "
    "action=records instead."
)


def register_dispatch_datasets_tools(mcp: FastMCP) -> None:
    """Register the ``datasets`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="datasets", description=_DATASETS_DISPATCH_DESCRIPTION)
    def datasets(
        action: str,
        topic: str,
        role: str = "",
        limit: int = 200,
        target: int = 30,
        vault: str = "",
    ) -> ToolResult:
        try:
            resolved = resolve(vault=vault_arg(vault))
        except KnoticaError as error:
            return envelope.error_envelope(error)
        store = LocalFSStore(resolved.path)
        try:
            payload = _dispatch_payload(
                store, resolved.path, action, topic, role=role, limit=limit, target=target
            )
        except _EXCEPTIONS as exc:
            return _map_exception(exc)
        return envelope.success_result(payload)


def _dispatch_payload(
    store: VaultStore,
    vault_path: Path,
    action: str,
    topic: str,
    *,
    role: str,
    limit: int,
    target: int,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "inventory":
        return gather_datasets_inventory(store, topic)
    if cleaned_action == "records":
        return load_dataset_records(store, topic, _require_role(role), limit=limit)
    if cleaned_action == "bootstrap":
        return _bootstrap_payload(store, topic)
    if cleaned_action == "bootstrap_train":
        return _bootstrap_train_payload(store, vault_path, topic, target)
    return freeze_reviewed_dataset(store, vault_path, topic)


def _require_role(role: str) -> str:
    cleaned = role.strip()
    if not cleaned:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "datasets action=records requires `role`",
            fix="Pass role as one of: trainset, held_out, seal, candidates, reviewed.",
        )
    return cleaned


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"datasets action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned
