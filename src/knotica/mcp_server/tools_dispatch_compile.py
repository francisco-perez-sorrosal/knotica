"""Operator dispatcher ``compile`` — action-parameterized routing over
``compile_run``, ``compile_status``, and ``compile_promote`` in
:mod:`knotica.mcp_server.tools_compile`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same dry-run/apply semantics.
Registered on ``server.py`` alongside the deprecated thin-tool aliases;
the governing two-tier tool-surface ADRs live in ``.ai-state/decisions/``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.compile_run import compile_status_payload
from knotica.core.config import ResolvedVault
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_compile import _promote_payload, _run_payload
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_compile_tools"]

ToolResult = CallToolResult

_DISPATCHER = "compile"
_ACTIONS = ("run", "status", "promote")

_COMPILE_DISPATCH_DESCRIPTION = (
    "Operator compile control (rarely conversational; the dashboard/CLI reach "
    "this directly). action=run compiles the query program for a topic (doctor "
    "gate -> clone -> MIPROv2/bootstrap -> branch; same as compile_run; may take "
    "a long time). action=status polls compile progress (same as compile_status, "
    "read-only). action=promote merges a reviewed compile/<topic>/… branch into "
    "the vault default branch (same as compile_promote); mode=dry-run previews, "
    "mode=apply performs the merge after review. Pass vault to select a "
    "configured vault. action=run and mode=apply never fire from detection "
    "alone -- only after the user has explicitly confirmed the compile/merge; "
    "an unconfirmed detection routes to action=status, mode=dry-run, or an "
    "offer instead."
)


def register_dispatch_compile_tools(mcp: FastMCP) -> None:
    """Register the ``compile`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="compile", description=_COMPILE_DISPATCH_DESCRIPTION)
    def compile(
        action: str,
        topic: str,
        branch: str = "",
        mode: str = "dry-run",
        use_mipro: bool = True,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _dispatch_payload(
                store,
                resolved,
                action=action,
                topic=topic,
                branch=branch,
                mode=mode,
                use_mipro=use_mipro,
            ),
        )


def _dispatch_payload(
    store: VaultStore,
    resolved: ResolvedVault,
    *,
    action: str,
    topic: str,
    branch: str,
    mode: str,
    use_mipro: bool,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "run":
        return _run_payload(store, resolved, topic, use_mipro=use_mipro)
    if cleaned_action == "status":
        return envelope.read_ok(compile_status_payload(store, topic))
    return _promote_payload(store, resolved.path, topic, _require_branch(branch), mode)


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"compile action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned


def _require_branch(branch: str) -> str:
    cleaned = branch.strip()
    if not cleaned:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "compile action=promote requires `branch`",
            fix="Pass branch=compile/<topic>/… (see compile_run's returned branch name).",
        )
    return cleaned
