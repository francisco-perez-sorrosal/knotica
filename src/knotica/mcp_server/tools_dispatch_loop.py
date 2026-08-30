"""Operator dispatcher ``loop`` — action-parameterized routing over the four
``loop_*`` tools in :mod:`knotica.mcp_server.tools_vault`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same semantics. No new
behavior, no dry-run preview invented where the wrapped tool has none.
Registered on ``server.py``;
the governing two-tier tool-surface ADRs live in ``.ai-state/decisions/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server.dispatch_telemetry import record_rejected_action
from knotica.mcp_server.tools_vault import (
    _loop_cadence_payload,
    _loop_once_payload,
    _loop_policy_payload,
    _loop_rebaseline_payload,
    _loop_run_eval_payload,
    _loop_set_baseline_payload,
)
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_loop_tools"]

ToolResult = CallToolResult

_DISPATCHER = "loop"
_ACTIONS = (
    "run_once",
    "set_baseline",
    "baseline_policy",
    "rebaseline",
    "cadence",
    "run_eval",
)

_LOOP_DISPATCH_DESCRIPTION = (
    "Run and steer the self-improvement gate: evaluate a topic, freeze the bar "
    "it is judged against, and set the cadence it runs at. Operator-tier and "
    "rarely conversational; on the published surface this is reached as "
    "`improve action=loop` (or `fill action=loop` for the gap-fill cycle) with "
    "`loop_action` selecting the operation. `loop_action=run_once` drives one "
    "gate cycle. `loop_action=run_eval` runs one eval cycle. "
    "`loop_action=set_baseline` freezes the gate baseline at `scalar`. "
    "`loop_action=baseline_policy` switches the gate policy to `policy` "
    "('latest'|'best'). `loop_action=rebaseline` re-freezes from metrics "
    "history using `mode` ('best'|'latest', default 'best'). "
    "`loop_action=cadence` reads (no params) or additively writes (any of "
    "`eval_min_interval_hours`, `eval_window`, `eval_num_threads`, "
    "`arena_scorer`) the `[loop]` config. `arena_scorer` ('heuristic'|'eval') "
    "picks what the prompt arena races with; 'eval' is gate-comparable and "
    "bills one golden-set eval per variant on every future race. "
    "Does NOT: compile a new prompt when the gate refuses (`improve "
    "action=compile` does), and does NOT touch the live vault — the cycle works "
    "on a clone and returns branches for review. "
    "Requires: an explicit topic and a frozen held-out golden set. "
    "`loop_action=run_once` and `loop_action=run_eval` SPEND MONEY and are "
    "two-phase: call once with no `confirm` for a preview envelope "
    "(worker/judge/thread count/estimated cost plus a short-lived "
    "`confirm_nonce`), then call again passing that nonce as `confirm` to "
    "actually bill and run. A single call never bills. Pass vault to select a "
    "configured vault. Every action here mutates: never called from detection "
    "alone -- only the dashboard/CLI operator invokes it, or the user has "
    "explicitly confirmed the change; an unconfirmed detection routes to "
    "`wiki_status` instead. "
    "Returns: the preview and nonce on a bare billed call; otherwise the "
    "cycle's result, with any candidate branch named for review."
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
        eval_min_interval_hours: float | None = None,
        eval_window: str | None = None,
        eval_num_threads: int | None = None,
        arena_scorer: str | None = None,
        confirm: str = "",
        num_threads: int | None = None,
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
                eval_min_interval_hours=eval_min_interval_hours,
                eval_window=eval_window,
                eval_num_threads=eval_num_threads,
                arena_scorer=arena_scorer,
                confirm=confirm,
                num_threads=num_threads,
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
    eval_min_interval_hours: float | None,
    eval_window: str | None,
    eval_num_threads: int | None,
    arena_scorer: str | None,
    confirm: str,
    num_threads: int | None,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    if cleaned_action == "run_once":
        return _loop_once_payload(store, vault_path, topic, confirm=confirm)
    if cleaned_action == "set_baseline":
        return _loop_set_baseline_payload(store, vault_path, topic, _require_scalar(scalar))
    if cleaned_action == "baseline_policy":
        return _loop_policy_payload(store, vault_path, topic, _require_policy(policy))
    if cleaned_action == "rebaseline":
        return _loop_rebaseline_payload(store, vault_path, topic, mode)
    if cleaned_action == "cadence":
        return _loop_cadence_payload(
            topic,
            eval_min_interval_hours=eval_min_interval_hours,
            eval_window=eval_window,
            eval_num_threads=eval_num_threads,
            arena_scorer=arena_scorer,
        )
    return _loop_run_eval_payload(
        store, vault_path, topic, confirm=confirm, num_threads=num_threads
    )


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
