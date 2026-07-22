"""Compile run/promote payload helpers for the ``compile`` action dispatcher.

These functions have no MCP tool registrations of their own — they are
imported directly by ``tools_dispatch_compile.py``, the sole entry point
into this logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core.compile_promote import compile_promote
from knotica.core.compile_run import run_compile
from knotica.core.config import ResolvedVault
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.store import VaultStore


def _run_payload(
    store: VaultStore,
    resolved: ResolvedVault,
    topic: str,
    *,
    use_mipro: bool,
) -> dict[str, Any]:
    result = run_compile(
        store,
        resolved.path,
        topic,
        config_detail=f"Vault '{resolved.name}' ready",
        use_mipro=use_mipro,
    )
    return envelope.read_ok(result.render())


def _promote_payload(
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
            message=f"compile_promote mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' to preview or mode='apply' to merge after review.",
        )
    result = compile_promote(
        store,
        vault_path,
        topic,
        branch,
        apply=cleaned == "apply",
    )
    error = result.get("error")
    if isinstance(error, dict):
        raise KnoticaError(
            code=ErrorCode(error.get("code", ErrorCode.GIT_ERROR.value)),
            message=str(error.get("message", "compile promote failed")),
            fix=error.get("fix"),
            retryable=bool(error.get("retryable", False)),
        )
    return envelope.read_ok(result)
