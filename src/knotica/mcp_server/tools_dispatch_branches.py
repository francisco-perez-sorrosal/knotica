"""Operator dispatcher ``branches`` — action-parameterized routing over
``branch_scoreboard``, ``loop_promote``, ``branch_promote``, and
``branch_delete`` in :mod:`knotica.mcp_server.tools_scoreboard`.

Pure routing: every action calls the same payload builder / core function the
replaced thin tool called, with the same arguments and the same
dry-run/apply semantics. Not yet registered on ``server.py`` — see
``dec-draft-ac2898b1``/``dec-draft-1785275a``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.branch_scoreboard import gather_branch_scoreboard
from knotica.core.compile_promote import compile_promote
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop_promote import loop_promote
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_scoreboard import _delete_payload, _promote_payload
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_branches_tools"]

ToolResult = CallToolResult

_DISPATCHER = "branches"
_ACTIONS = ("scoreboard", "promote_loop", "promote", "delete")
_PROMOTE_KINDS = ("compile", "loop")

_BRANCHES_DISPATCH_DESCRIPTION = (
    "Operator branch control (rarely conversational; the dashboard/CLI reach "
    "this directly). action=scoreboard returns the deterministic per-topic "
    "scoreboard (same as branch_scoreboard, read-only). action=promote_loop "
    "merges a loop/r/<shortsha> or loop/c/* branch after human review (same as "
    "the old loop_promote tool). action=promote is the unified gate: pass "
    "kind=compile to merge compile/<topic>/… branches or kind=loop to merge "
    "loop/r branches (same as branch_promote). action=delete removes a local "
    "compile/<topic>/… branch (same as branch_delete). mode=dry-run previews, "
    "mode=apply commits, for every mutating action. Pass vault to select a "
    "configured vault."
)


def register_dispatch_branches_tools(mcp: FastMCP) -> None:
    """Register the ``branches`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="branches", description=_BRANCHES_DISPATCH_DESCRIPTION)
    def branches(
        action: str,
        topic: str,
        branch: str = "",
        kind: str = "",
        mode: str = "dry-run",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _dispatch_payload(
                store,
                resolved.path,
                action=action,
                topic=topic,
                branch=branch,
                kind=kind,
                mode=mode,
            ),
        )


def _dispatch_payload(
    store: VaultStore,
    vault_path: Path,
    *,
    action: str,
    topic: str,
    branch: str,
    kind: str,
    mode: str,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "scoreboard":
        return envelope.read_ok(gather_branch_scoreboard(store, vault_path, topic))
    if cleaned_action == "promote_loop":
        return _promote_payload(
            loop_promote, store, vault_path, topic, _require_branch(branch), mode
        )
    if cleaned_action == "delete":
        return _delete_payload(store, vault_path, topic, _require_branch(branch), mode)
    promote_fn = compile_promote if _validate_kind(kind) == "compile" else loop_promote
    return _promote_payload(promote_fn, store, vault_path, topic, _require_branch(branch), mode)


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"branches action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned


def _validate_kind(kind: str) -> str:
    cleaned = kind.strip().lower()
    if cleaned not in _PROMOTE_KINDS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"branches action=promote requires kind to be one of "
            f"{'|'.join(_PROMOTE_KINDS)}, got {kind!r}",
            fix="Pass kind='compile' for compile/<topic>/ branches or kind='loop' for loop/r branches.",
        )
    return cleaned


def _require_branch(branch: str) -> str:
    cleaned = branch.strip()
    if not cleaned:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "this branches action requires `branch`",
            fix="Pass branch=<branch-name> (see action=scoreboard for candidates).",
        )
    return cleaned
