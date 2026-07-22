"""Branch scoreboard / promote / delete payload helpers for the ``branches`` dispatcher.

These functions have no MCP tool registrations of their own — they are
imported directly by ``tools_dispatch_branches.py``, the sole entry point
into this logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core.branch_delete import branch_delete
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.store import VaultStore


def _promote_payload(
    promote_fn: Any,
    store: VaultStore,
    vault_path: str | Path,
    topic: str,
    branch: str,
    mode: str,
) -> dict[str, Any]:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in {"dry-run", "apply"}:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"promote mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' to preview or mode='apply' to merge after review.",
        )
    result = promote_fn(store, vault_path, topic, branch, apply=cleaned == "apply")
    error = result.get("error")
    if isinstance(error, dict):
        raise KnoticaError(
            code=ErrorCode(error.get("code", ErrorCode.GIT_ERROR.value)),
            message=str(error.get("message", "promote failed")),
            fix=error.get("fix"),
            retryable=bool(error.get("retryable", False)),
        )
    return envelope.read_ok(result)


def _delete_payload(
    store: VaultStore,
    vault_path: str | Path,
    topic: str,
    branch: str,
    mode: str,
) -> dict[str, Any]:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in {"dry-run", "apply"}:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"branch_delete mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' to preview or mode='apply' to delete after review.",
        )
    result = branch_delete(store, vault_path, topic, branch, apply=cleaned == "apply")
    error = result.get("error")
    if isinstance(error, dict):
        raise KnoticaError(
            code=ErrorCode(error.get("code", ErrorCode.GIT_ERROR.value)),
            message=str(error.get("message", "branch delete failed")),
            fix=error.get("fix"),
            retryable=bool(error.get("retryable", False)),
        )
    return envelope.read_ok(result)
