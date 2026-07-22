"""Operator dispatcher ``loop`` — action-parameterized routing over the four
``loop_*`` tools in :mod:`knotica.mcp_server.tools_vault`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same semantics. No new
behavior, no dry-run preview invented where the wrapped tool has none — see
``dec-draft-ac2898b1``/``dec-draft-1785275a`` for why creation and wiring into
``server.py`` are split (this module is not yet registered).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_vault import (
    _loop_once_payload,
    _loop_policy_payload,
    _loop_rebaseline_payload,
    _loop_set_baseline_payload,
)
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_loop_tools"]

ToolResult = CallToolResult

_DISPATCHER = "loop"
_ACTIONS = ("run_once", "set_baseline", "baseline_policy", "rebaseline")

_LOOP_DISPATCH_DESCRIPTION = (
    "Operator loop control (rarely conversational; the dashboard/CLI reach this "
    "directly). action=run_once drives one gate cycle (same as loop_run_once); "
    "action=set_baseline freezes the gate baseline at `scalar` (same as "
    "loop_set_baseline); action=baseline_policy switches the gate policy to "
    "`policy` ('latest'|'best', same as loop_baseline_policy); "
    "action=rebaseline re-freezes from metrics history using `mode` "
    "('best'|'latest', default 'best', same as loop_rebaseline). Pass vault to "
    "select a configured vault."
)


def register_dispatch_loop_tools(mcp: FastMCP) -> None:
    """Register the ``loop`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="loop", description=_LOOP_DISPATCH_DESCRIPTION)
    def loop(
        action: str,
        topic: str,
        scalar: float | None = None,
        policy: str = "",
        mode: str = "best",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _dispatch_payload(
                store,
                resolved.path,
                action=action,
                topic=topic,
                scalar=scalar,
                policy=policy,
                mode=mode,
            ),
        )


def _dispatch_payload(
    store: VaultStore,
    vault_path: Path,
    *,
    action: str,
    topic: str,
    scalar: float | None,
    policy: str,
    mode: str,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "run_once":
        return _loop_once_payload(store, vault_path, topic)
    if cleaned_action == "set_baseline":
        return _loop_set_baseline_payload(store, vault_path, topic, _require_scalar(scalar))
    if cleaned_action == "baseline_policy":
        return _loop_policy_payload(store, vault_path, topic, _require_policy(policy))
    return _loop_rebaseline_payload(store, vault_path, topic, mode)


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"loop action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned


def _require_scalar(scalar: float | None) -> float:
    if scalar is None:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "loop action=set_baseline requires `scalar`",
            fix="Pass scalar=<float> (the baseline value to freeze).",
        )
    return scalar


def _require_policy(policy: str) -> str:
    if not policy.strip():
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "loop action=baseline_policy requires `policy`",
            fix="Pass policy='latest' or policy='best'.",
        )
    return policy
