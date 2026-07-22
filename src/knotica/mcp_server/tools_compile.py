"""MCP tools ``compile_run``, ``compile_status``, and ``compile_promote`` for Phase 3a."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.compile_promote import compile_promote
from knotica.core.compile_run import compile_status_payload, run_compile
from knotica.core.config import ResolvedVault
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import deprecation_suffix, record_deprecated_alias
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_compile_tools"]

ToolResult = CallToolResult

_RUN_DESCRIPTION = (
    "Compile the query program for a topic (doctor gate → clone → MIPROv2/bootstrap → "
    "branch). May take a long time; poll compile_status while running. Pass vault to "
    "select a configured vault. Returns branch name and baseline/compiled scalars; "
    "does not merge to main — promote with compile_promote after review."
)

_STATUS_DESCRIPTION = (
    "Poll compile progress for a topic (stage, trial, branch, message). "
    "Pass vault to select a configured vault. Read-only."
)

_PROMOTE_DESCRIPTION = (
    "Merge a reviewed compile branch into the vault default branch (human gate after "
    "compile_run). branch must match compile/<topic>/… — arbitrary branches are refused. "
    "mode=dry-run (default) previews the merge; mode=apply performs a --no-ff merge under "
    "the vault lock. Refuses dirty worktrees. Pass vault to select a configured vault."
)


def register_compile_tools(mcp: FastMCP) -> None:
    """Register compile_run, compile_status, and compile_promote on ``mcp``."""

    @mcp.tool(
        name="compile_run",
        description=_RUN_DESCRIPTION + deprecation_suffix("compile_run"),
    )
    def compile_run(topic: str, vault: str = "", use_mipro: bool = True) -> ToolResult:
        record_deprecated_alias("compile_run")
        return with_resolved_vault(
            vault,
            lambda store, resolved: _run_payload(store, resolved, topic, use_mipro=use_mipro),
        )

    @mcp.tool(
        name="compile_status",
        description=_STATUS_DESCRIPTION + deprecation_suffix("compile_status"),
    )
    def compile_status(topic: str, vault: str = "") -> ToolResult:
        record_deprecated_alias("compile_status")
        return with_resolved_vault(
            vault,
            lambda store, _resolved: envelope.read_ok(compile_status_payload(store, topic)),
        )

    @mcp.tool(
        name="compile_promote",
        description=_PROMOTE_DESCRIPTION + deprecation_suffix("compile_promote"),
    )
    def compile_promote_tool(
        topic: str,
        branch: str,
        mode: str = "dry-run",
        vault: str = "",
    ) -> ToolResult:
        record_deprecated_alias("compile_promote")
        return with_resolved_vault(
            vault,
            lambda store, resolved: _promote_payload(store, resolved.path, topic, branch, mode),
        )


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
