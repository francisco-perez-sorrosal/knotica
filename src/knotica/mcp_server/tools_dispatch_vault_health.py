"""Operator dispatcher ``vault_health`` — action-parameterized routing over
``doctor_run``, ``doctor_repair``, ``okf_check``, ``okf_repair``,
``vault_lint``, and ``vault_metadata_tree`` in
:mod:`knotica.mcp_server.tools_vault`.

Pure routing: every action calls the same payload builder the replaced thin
tool called, with the same arguments and the same dry-run/apply semantics.
Not yet registered on ``server.py`` — see
``dec-draft-ac2898b1``/``dec-draft-1785275a``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.config import ResolvedVault
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.vault_metadata_tree import gather_vault_metadata_tree
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_vault import (
    _doctor_payload,
    _doctor_repair_payload,
    _okf_check_payload,
    _okf_repair_payload,
)
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_vault_health_tools"]

ToolResult = CallToolResult

_DISPATCHER = "vault_health"
_ACTIONS = ("doctor", "repair", "okf_check", "okf_repair", "lint", "metadata_tree")

_VAULT_HEALTH_DISPATCH_DESCRIPTION = (
    "Operator vault-health control (rarely conversational; the dashboard/CLI "
    "reach this directly). action=doctor runs the deterministic health checks "
    "(same as doctor_run, read-only; pass quick for the SessionStart subset, "
    "fix for scoped repair guidance). action=repair restores dirty paths to "
    "HEAD (same as doctor_repair; mode=dry-run lists dirty paths, mode=apply "
    "restores paths_json/all_tracked, delete_untracked removes untracked "
    "paths). action=okf_check runs the OKF compatibility check (same as "
    "okf_check, read-only; pass strict). action=okf_repair runs OKF repair "
    "(same as okf_repair; mode=dry-run previews, mode=apply writes + commits, "
    "force required on a dirty tree). action=lint runs mechanical lint for a "
    "topic or the whole vault (same as vault_lint, read-only). "
    "action=metadata_tree lists the vault's Knotica metadata substrate (same "
    "as vault_metadata_tree, read-only). Pass vault to select a configured "
    "vault."
)


def register_dispatch_vault_health_tools(mcp: FastMCP) -> None:
    """Register the ``vault_health`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="vault_health", description=_VAULT_HEALTH_DISPATCH_DESCRIPTION)
    def vault_health(
        action: str,
        quick: bool = False,
        fix: bool = False,
        mode: str = "dry-run",
        paths_json: str = "[]",
        all_tracked: bool = False,
        delete_untracked: bool = False,
        strict: bool = False,
        force: bool = False,
        topic: str = "",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _dispatch_payload(
                store,
                resolved,
                action=action,
                quick=quick,
                fix=fix,
                mode=mode,
                paths_json=paths_json,
                all_tracked=all_tracked,
                delete_untracked=delete_untracked,
                strict=strict,
                force=force,
                topic=topic,
            ),
        )


def _dispatch_payload(
    store: VaultStore,
    resolved: ResolvedVault,
    *,
    action: str,
    quick: bool,
    fix: bool,
    mode: str,
    paths_json: str,
    all_tracked: bool,
    delete_untracked: bool,
    strict: bool,
    force: bool,
    topic: str,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, topic)
    if cleaned_action == "doctor":
        return _doctor_payload(store, resolved, quick=quick, include_fix=fix)
    if cleaned_action == "repair":
        return _doctor_repair_payload(
            store,
            resolved.path,
            mode=mode,
            paths_json=paths_json,
            all_tracked=all_tracked,
            delete_untracked=delete_untracked,
        )
    if cleaned_action == "okf_check":
        return _okf_check_payload(store, strict=strict)
    if cleaned_action == "okf_repair":
        return _okf_repair_payload(store, mode=mode, force=force)
    if cleaned_action == "lint":
        return _lint_payload(store, topic)
    return envelope.read_ok(gather_vault_metadata_tree(store, resolved.path, topic=topic))


def _lint_payload(store: VaultStore, topic: str) -> dict[str, Any]:
    from knotica.core.lint import lint_vault

    return envelope.read_ok(
        {
            "topic": topic.strip().strip("/"),
            "violations": [violation.render() for violation in lint_vault(store, topic)],
        }
    )


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"vault_health action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned
