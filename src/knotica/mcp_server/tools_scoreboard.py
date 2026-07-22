"""MCP tools ``branch_scoreboard``, ``loop_promote``, ``branch_promote``, and ``branch_delete``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.branch_delete import branch_delete
from knotica.core.branch_scoreboard import gather_branch_scoreboard
from knotica.core.compile_promote import compile_promote
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop_promote import loop_promote
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import deprecation_suffix, record_deprecated_alias
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

_SCOREBOARD_DESCRIPTION = (
    "Deterministic scoreboard for one topic: per-topic gate baseline "
    "(from <topic>/.knotica/loop-state.json), default branch scalar, compile branches "
    "(compile/<topic>/… with open vs history slots), loop candidates (loop/c/*), "
    "loop result branches (loop/r/*), and arena variants. Sorted with the open compile "
    "branch first. Read-only — no commits."
)

_LOOP_PROMOTE_DESCRIPTION = (
    "Merge a loop eval result into the default branch after human review. Pass "
    "loop/r/<shortsha> or loop/c/* when loop/r exists locally. mode=dry-run plans; "
    "mode=apply merges under the vault flock. Does not run eval — use loop_run_once first."
)

_BRANCH_PROMOTE_DESCRIPTION = (
    "Unified promote gate: kind=compile merges compile/<topic>/… branches; "
    "kind=loop merges loop/r/<shortsha> (or loop/c/* with a fetched loop/r tip). "
    "Always dry-run first, then apply. Never auto-promotes."
)

_BRANCH_DELETE_DESCRIPTION = (
    "Delete a local compile result branch (compile/<topic>/…) after promote or when it "
    "did not beat the per-topic baseline. mode=dry-run previews; mode=apply runs "
    "git branch -D. Preserves compile-state.json history — only clears the active branch "
    "pointer when it pointed at the deleted tip. Refuses default branch and current HEAD."
)

ToolResult = CallToolResult


def register_scoreboard_tools(mcp: FastMCP) -> None:
    """Register branch scoreboard and promote tools."""

    @mcp.tool(
        name="branch_scoreboard",
        description=_SCOREBOARD_DESCRIPTION + deprecation_suffix("branch_scoreboard"),
    )
    def branch_scoreboard(topic: str, vault: str = "") -> ToolResult:
        record_deprecated_alias("branch_scoreboard")
        return with_resolved_vault(
            vault,
            lambda store, resolved: envelope.read_ok(
                gather_branch_scoreboard(store, resolved.path, topic)
            ),
        )

    @mcp.tool(
        name="loop_promote",
        description=_LOOP_PROMOTE_DESCRIPTION + deprecation_suffix("loop_promote"),
    )
    def loop_promote_tool(
        topic: str,
        branch: str,
        mode: str = "dry-run",
        vault: str = "",
    ) -> ToolResult:
        record_deprecated_alias("loop_promote")
        return with_resolved_vault(
            vault,
            lambda store, resolved: _promote_payload(
                loop_promote,
                store,
                resolved.path,
                topic,
                branch,
                mode,
            ),
        )

    @mcp.tool(
        name="branch_promote",
        description=_BRANCH_PROMOTE_DESCRIPTION + deprecation_suffix("branch_promote"),
    )
    def branch_promote_tool(
        kind: str,
        topic: str,
        branch: str,
        mode: str = "dry-run",
        vault: str = "",
    ) -> ToolResult:
        record_deprecated_alias("branch_promote")
        cleaned_kind = kind.strip().lower()
        if cleaned_kind not in {"compile", "loop"}:
            raise KnoticaError(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f"branch_promote kind must be 'compile' or 'loop', got {kind!r}",
                fix="Pass kind=compile for compile/<topic>/ branches or kind=loop for loop/r branches.",
            )
        promote_fn = compile_promote if cleaned_kind == "compile" else loop_promote
        return with_resolved_vault(
            vault,
            lambda store, resolved: _promote_payload(
                promote_fn,
                store,
                resolved.path,
                topic,
                branch,
                mode,
            ),
        )

    @mcp.tool(
        name="branch_delete",
        description=_BRANCH_DELETE_DESCRIPTION + deprecation_suffix("branch_delete"),
    )
    def branch_delete_tool(
        topic: str,
        branch: str,
        mode: str = "dry-run",
        vault: str = "",
    ) -> ToolResult:
        record_deprecated_alias("branch_delete")
        return with_resolved_vault(
            vault,
            lambda store, resolved: _delete_payload(
                store,
                resolved.path,
                topic,
                branch,
                mode,
            ),
        )


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
