"""Operator dispatcher ``vault`` — inspect and switch the active knowledge base.

Config-level only: every action reads or writes
``~/.config/knotica/config.toml`` (never vault contents, never a git commit).
Because the config is resolved *fresh on every tool call*, ``action=use`` takes
effect immediately for every subsequent call — no server restart.

The honest "which KB am I on, and is anything wrong?" surface lives in
``action=status``: it reports the *live* resolved active vault, all configured
vaults, headless-LLM readiness (deps + credential mode), and a misconfig list —
so the model answers from ground truth rather than assuming.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core import config_write, vault_scaffold
from knotica.core.config import ConfigState, config_file_path, diagnose, list_vaults
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.llm import API_KEY_ENV_VAR, OAUTH_TOKEN_ENV_VAR
from knotica.mcp_server import envelope
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action

__all__ = ["register_dispatch_vault_tools"]

ToolResult = CallToolResult

_DISPATCHER = "vault"
_ACTIONS = ("list", "status", "use", "add", "create")

_VAULT_DISPATCH_DESCRIPTION = (
    "Inspect and switch the active knowledge base (vault). "
    "action=status reports the LIVE active vault (name, path, readiness), all "
    "configured vaults, headless-LLM readiness (deps installed + credential "
    "mode), and a misconfig list — call this to honestly answer 'which KB am I "
    "on, and is anything wrong?'; never assume the active vault, it is resolved "
    "per call and can be switched at any time. action=list returns the "
    "configured vaults and which is default. action=use switches the active "
    "vault by flipping default_vault (name required; effective immediately, no "
    "restart). action=add registers an EXISTING vault path (name + path "
    "required; pass make_default to also switch to it). action=create scaffolds "
    "a NEW, BARE vault (constitution + optional first topic only — no demo "
    "content) and registers it. Before calling create, ASK the user for the same "
    "fields the dashboard's New KB form collects: path (required — where the "
    "vault lives on disk), name (optional — default to the path's last segment), "
    "and topic (optional first topic); pass make_default to switch to it. To "
    "REGISTER an already-existing vault instead use action=add. Config-level "
    "only: reads/writes "
    "~/.config/knotica/config.toml, never vault contents, never a git commit "
    "(action=create's one-time vault git-init/commit is the sole exception, "
    "documented in knotica.core.vault_scaffold)."
)


def register_dispatch_vault_tools(mcp: FastMCP) -> None:
    """Register the ``vault`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="vault", description=_VAULT_DISPATCH_DESCRIPTION)
    def vault(
        action: str,
        name: str = "",
        path: str = "",
        make_default: bool = False,
        topic: str = "",
    ) -> ToolResult:
        try:
            payload = _dispatch(
                action, name=name, path=path, make_default=make_default, topic=topic
            )
        except KnoticaError as error:
            return envelope.error_envelope(error)
        return envelope.success_result(payload)


def _dispatch(
    action: str, *, name: str, path: str, make_default: bool, topic: str
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    record_dispatch(_DISPATCHER, cleaned_action, "")
    if cleaned_action == "list":
        return _list_payload()
    if cleaned_action == "status":
        return _status_payload()
    if cleaned_action == "use":
        return _use_payload(name)
    if cleaned_action == "create":
        return _create_payload(name, path, topic, make_default)
    return _add_payload(name, path, make_default)


def _list_payload() -> dict[str, Any]:
    catalog = list_vaults()
    return envelope.read_ok(
        {"default_vault": catalog["default_vault"], "vaults": catalog["vaults"]}
    )


def _status_payload() -> dict[str, Any]:
    diagnosis = diagnose()
    catalog = list_vaults()
    if diagnosis.vault is not None:
        active = {"name": diagnosis.vault.name, "path": str(diagnosis.vault.path), "ready": True}
    else:
        active = {"name": catalog["default_vault"], "path": "", "ready": False}
    headless = _headless_status()
    return envelope.read_ok(
        {
            "active_vault": active,
            "config_path": str(config_file_path()),
            "config_state": diagnosis.state.value,
            "config_detail": diagnosis.detail,
            "default_vault": catalog["default_vault"],
            "vaults": catalog["vaults"],
            "headless": headless,
            "misconfig": _collect_misconfig(diagnosis, catalog, headless),
        }
    )


def _use_payload(name: str) -> dict[str, Any]:
    cleaned = name.strip()
    if not cleaned:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="vault action=use requires a name.",
            fix="Pass name=<configured vault>; call action=list to see them.",
        )
    config_write.set_default_vault(config_file_path(), cleaned)
    diagnosis = diagnose(vault=cleaned)
    return envelope.read_ok(
        {
            "active_vault": cleaned,
            "path": str(diagnosis.vault.path) if diagnosis.vault else "",
            "ready": diagnosis.vault is not None,
            "detail": diagnosis.detail,
            "message": f"active vault switched to '{cleaned}'",
        }
    )


def _add_payload(name: str, path: str, make_default: bool) -> dict[str, Any]:
    cleaned = name.strip()
    if not cleaned:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="vault action=add requires a name.",
            fix="Pass name=<vault name> and path=<filesystem path>.",
        )
    cleaned_path = path.strip()
    if not cleaned_path:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="vault action=add requires a path.",
            fix="Pass path=<filesystem path> of an existing knotica vault.",
        )
    expanded = Path(os.path.expandvars(cleaned_path)).expanduser()
    config_write.upsert_vault(config_file_path(), cleaned, expanded, make_default=make_default)
    diagnosis = diagnose(vault=cleaned)
    return envelope.read_ok(
        {
            "name": cleaned,
            "path": str(expanded),
            "made_default": make_default,
            "ready": diagnosis.vault is not None,
            "detail": diagnosis.detail,
            "message": (f"vault '{cleaned}' added" + (" and set active" if make_default else "")),
        }
    )


def _create_payload(name: str, path: str, topic: str, make_default: bool) -> dict[str, Any]:
    cleaned = name.strip()
    if not cleaned:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="vault action=create requires a name.",
            fix="Pass name=<vault name> and path=<filesystem path>.",
        )
    cleaned_path = path.strip()
    if not cleaned_path:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="vault action=create requires a path.",
            fix="Pass path=<filesystem path> for the new vault.",
        )
    expanded = Path(os.path.expandvars(cleaned_path)).expanduser()
    result = vault_scaffold.scaffold_vault(expanded, topic=topic.strip() or None)
    config_write.upsert_vault(config_file_path(), cleaned, expanded, make_default=make_default)
    diagnosis = diagnose(vault=cleaned)
    return envelope.read_ok(
        {
            "name": cleaned,
            "path": str(expanded),
            "created": result.created,
            "made_default": make_default,
            "ready": diagnosis.vault is not None,
            "detail": diagnosis.detail,
            "message": (
                f"vault '{cleaned}' "
                + ("scaffolded" if result.created else "already existed — registered")
                + (" and set active" if make_default else "")
            ),
        }
    )


def _headless_status() -> dict[str, Any]:
    """Report headless-LLM readiness: deps importable + which credential mode.

    Deps are probed with ``find_spec`` (no heavy import) so this is safe on a
    lean install; the credential env var *names* are the single source of truth
    from :mod:`knotica.evals.llm`. The credential value is never returned — only
    the mode marker (``oauth`` / ``api_key`` / ``none``).
    """
    deps_installed = (
        importlib.util.find_spec("anthropic") is not None
        and importlib.util.find_spec("dspy") is not None
    )
    if os.environ.get(OAUTH_TOKEN_ENV_VAR):
        credential_mode = "oauth"
    elif os.environ.get(API_KEY_ENV_VAR):
        credential_mode = "api_key"
    else:
        credential_mode = "none"
    ready = deps_installed and credential_mode != "none"
    if ready:
        detail = f"headless ready ({credential_mode}; anthropic+dspy installed)"
    elif not deps_installed and credential_mode == "none":
        detail = "lean mode — no headless deps or credential (ingest / client-as-brain only)"
    elif not deps_installed:
        detail = "credential set but anthropic/dspy not installed — enable the evals extra"
    else:
        detail = "anthropic/dspy installed but no credential (set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)"
    return {
        "deps_installed": deps_installed,
        "credential_mode": credential_mode,
        "ready": ready,
        "detail": detail,
    }


def _collect_misconfig(
    diagnosis: Any, catalog: dict[str, Any], headless: dict[str, Any]
) -> list[str]:
    """Human-readable problems worth surfacing; empty list == all good.

    Pure lean mode (no headless deps/credential) is NOT a misconfig — only an
    *inconsistent* headless setup (credential present but deps missing) is.
    """
    issues: list[str] = []
    if diagnosis.state is ConfigState.UNCONFIGURED:
        issues.append(f"{diagnosis.detail} {diagnosis.remediation}".strip())
    for entry in catalog["vaults"]:
        if not entry.get("ready"):
            where = entry.get("path") or "(no path)"
            why = entry.get("detail") or "not ready"
            issues.append(f"vault '{entry['name']}' at {where} is not usable: {why}")
    if headless["credential_mode"] != "none" and not headless["deps_installed"]:
        issues.append(
            # Both halves of the previous text were wrong: KNOTICA_EXTRAS is read
            # nowhere in the tree, and hand-listing the packages drops the
            # `litellm<1.92` bound that pyproject.toml exists to hold, which is a
            # macOS build failure. Always name the extra.
            "headless credential is set but anthropic/dspy are not installed — reinstall "
            "requesting the evals extra, e.g. uvx --from '<source>[evals]' knotica mcp, "
            "then reconnect the MCP client"
        )
    return issues


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"vault action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned
