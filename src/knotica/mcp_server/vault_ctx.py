"""Shared per-call vault resolution for MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault, resolve
from knotica.core.errors import KnoticaError
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.mcp_server import envelope
from knotica.search import InvalidCursorError
from knotica.store import LocalFSStore, VaultStore

__all__ = ["vault_arg", "with_resolved_vault"]

ToolResult = CallToolResult

_MAPPED = (KnoticaError, TopicNotFoundError, PageNotFoundError, InvalidCursorError)


def vault_arg(vault_name: str) -> str | None:
    """Normalize an optional vault tool argument to ``resolve``'s ``vault`` kwarg."""
    cleaned = vault_name.strip()
    return cleaned or None


def with_resolved_vault(
    vault_name: str,
    operation: Callable[[VaultStore, ResolvedVault], dict[str, Any]],
) -> ToolResult:
    """Resolve vault, run ``operation``, map house errors into MCP envelopes."""
    try:
        vault = resolve(vault=vault_arg(vault_name))
    except KnoticaError as error:
        return envelope.error_envelope(error)
    store = LocalFSStore(vault.path)
    try:
        payload = operation(store, vault)
    except _MAPPED as exc:
        return envelope.map_read_exception(exc)
    return envelope.success_result(payload)
