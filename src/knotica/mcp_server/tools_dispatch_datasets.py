"""Operator dispatcher ``datasets`` — action-parameterized routing over
``datasets_inventory``, ``datasets_records``, ``datasets_bootstrap``,
``datasets_bootstrap_train``, and ``datasets_freeze`` in
:mod:`knotica.mcp_server.tools_datasets`.

Pure routing: every action calls the same payload builder / core function the
replaced thin tool called, with the same arguments and the same semantics.
Registered on ``server.py``;
the governing two-tier tool-surface ADRs live in ``.ai-state/decisions/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

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
from knotica.mcp_server.dispatch_telemetry import record_rejected_action
from knotica.mcp_server.tools_datasets import (
    _EXCEPTIONS,
    _bootstrap_payload,
    _bootstrap_train_payload,
    _map_exception,
)
from knotica.mcp_server import tool_params
from knotica.mcp_server.vault_ctx import vault_arg
from knotica.store import LocalFSStore, VaultStore

__all__ = ["register_dispatch_datasets_tools"]

ToolResult = CallToolResult

_DISPATCHER = "datasets"
_ACTIONS = ("inventory", "records", "bootstrap", "bootstrap_train", "freeze")

#: The dataset roles `datasets action=records` reads. Named here so the
#: published enum and `_require_role`'s fix text share one declaration.
_ROLES: tuple[str, ...] = ("trainset", "held_out", "seal", "candidates", "reviewed")

_DatasetsAction = Annotated[
    str,
    tool_params.grounded(
        "Which dataset operation to run; see this tool's description for each.",
        _ACTIONS,
    ),
]

_Role = Annotated[
    str,
    tool_params.grounded(
        "Which dataset to read records from. Required by datasets_action=records; "
        "ignored by the others.",
        _ROLES,
    ),
]

_Target = Annotated[
    int,
    tool_params.grounded(
        "How many examples a bootstrap should aim to produce; 30 is the default and "
        "is the compile-readiness floor.",
    ),
]

_DATASETS_DISPATCH_DESCRIPTION = (
    "Inventory, bootstrap and freeze the sets a topic's bar is measured on. "
    "Operator-tier and rarely conversational; on the published surface this is "
    "reached as `improve action=datasets` with `datasets_action` selecting the "
    "operation. `datasets_action=inventory` summarizes all topic datasets under "
    ".knotica/datasets/ (read-only). `datasets_action=records` loads capped "
    "records for one dataset `role` (trainset|held_out|seal|candidates|reviewed; "
    "read-only; pass limit, default 200). `datasets_action=bootstrap` "
    "synthesizes golden candidates via the worker LLM and does not freeze them. "
    "`datasets_action=bootstrap_train` cold-starts the trainset from `target` "
    "synthesized QA pairs (default 30). `datasets_action=freeze` commits "
    "reviewed candidates into held-out golden.jsonl. "
    "Does NOT: review the synthesized candidates — that is the golden review "
    "board (`improve action=golden`) — and does NOT score anything. "
    "Requires: an explicit topic. `datasets_action=bootstrap` and "
    "`datasets_action=bootstrap_train` SPEND MONEY. Pass vault to select a "
    "configured vault. Bootstrapping and freezing never fire from detection "
    "alone -- only after the user has explicitly confirmed the mutation; an "
    "unconfirmed detection routes to `datasets_action=inventory` or "
    "`datasets_action=records` instead. "
    "Returns: counts and records as data — an empty dataset is an empty "
    "result, never an error."
)


def register_dispatch_datasets_tools(mcp: FastMCP) -> None:
    """Register the ``datasets`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="datasets", description=_DATASETS_DISPATCH_DESCRIPTION)
    def datasets(
        action: _DatasetsAction,
        topic: tool_params.Topic,
        role: _Role = "",
        limit: tool_params.Limit = 200,
        target: _Target = 30,
        vault: tool_params.Vault = "",
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
            fix=f"Pass role as one of: {', '.join(_ROLES)}.",
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
