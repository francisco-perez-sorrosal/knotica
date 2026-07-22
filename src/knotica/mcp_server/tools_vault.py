"""Vault health + remediation tools for the dashboard Vault pane.

Thin adapters over existing CLI/core paths — same semantics as
``knotica doctor``, ``knotica okf check|repair``, and ``loop_runner --once``.
No new repair algorithms; the UI only triggers and watches what already exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.arena import heuristic_arena_score
from knotica.core.config import ResolvedVault, diagnose
from knotica.core.doctor import build_doctor_payload, run_doctor_checks
from knotica.core.vault_metadata_tree import gather_vault_metadata_tree
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import LoopRunner, build_loop_runner, harness_evaluate
from knotica.core.operations.doctor_repair import doctor_repair
from knotica.core.page import TopicNotFoundError
from knotica.mcp_server import envelope
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.okf.check import check_vault
from knotica.okf.repair import RepairOptions, repair_vault
from knotica.store import VaultStore

__all__ = ["register_vault_tools"]

ToolResult = CallToolResult

_DOCTOR_DESCRIPTION = (
    "Run the same deterministic health checks as `knotica doctor` (mechanical, "
    "no LLM). Returns the doctor --json checklist (PASS/WARN/FAIL rows + "
    "remediation text). Pass quick=true for the SessionStart subset. Pass "
    "fix=true for the same scoped repair guidance as `knotica doctor --fix` "
    "(read-only command list). To actually restore paths use doctor_repair. "
    "Pass vault to select a configured vault name. Read-only."
)

_DOCTOR_REPAIR_DESCRIPTION = (
    "Path-scoped dirty-tree repair — same as `knotica doctor repair`. "
    "mode=dry-run lists dirty paths; mode=apply restores selected paths to HEAD "
    "under the vault lock (never `git restore .`). For apply, pass paths as a "
    "JSON array string, or all_tracked=true. delete_untracked=true allows "
    "removing selected untracked paths. Pass vault to select a configured vault."
)

_OKF_CHECK_DESCRIPTION = (
    "Run the same native OKF compatibility check as `knotica okf check`. "
    "Reports concept/reserved-file findings and unresolved internal links. "
    "Pass vault to select a configured vault. Read-only."
)

_OKF_REPAIR_DESCRIPTION = (
    "Run the same OKF repair as `knotica okf repair`. mode=dry-run previews "
    "files that would change (default); mode=apply writes + one git commit "
    "(requires force=true when the work tree is dirty, matching the CLI). "
    "Pass vault to select a configured vault."
)

_LOOP_ONCE_DESCRIPTION = (
    "Run one self-improvement loop tick for a topic — same as `knotica loop "
    "--topic … --once`: first observe the default branch (new content is "
    "evaluated on a clone; the first observation auto-freezes the gate "
    "baseline), then gate at most one pending `loop/c/*` candidate. Updates "
    "`<topic>/.knotica/loop-state.json` so wiki_status shows stage progress "
    "(evaluating → arena race or merge/revert → passed/failed). On regression, "
    "runs the prompt-variant arena heal. Runs a real eval (may take minutes). "
    "Pass vault to select a configured vault."
)

_LOOP_POLICY_DESCRIPTION = (
    "Set the topic's gate policy: 'latest' (baseline tracks reality — auto-freeze "
    "and instrument re-freeze only) or 'best' (high-water mark — better "
    "observations ratchet the baseline up; anything below it is a regression the "
    "arena fights). Persists in loop-state (one git commit). Current policy is "
    "readable via wiki_status.loop.baseline_policy."
)

_LOOP_REBASELINE_DESCRIPTION = (
    "Re-freeze the gate baseline from metrics history — no eval. mode='best' "
    "freezes the high-water mark, mode='latest' the most recent scalar, both "
    "restricted to records from the current instrument (cross-instrument scalars "
    "are never comparable). One git commit. Use after deciding the loop should "
    "defend a previous quality level."
)

_LOOP_SET_BASELINE_DESCRIPTION = (
    "Freeze the gate baseline scalar for a topic — same as "
    "`python scripts/loop_runner.py --topic … --set-baseline SCALAR`. "
    "Does not run eval. Pass vault to select a configured vault."
)

_LINT_DESCRIPTION = (
    "Run mechanical lint (same as `lint_check`) for a topic or the whole vault. "
    "Returns violation objects with path, check, message, and fix. Pass vault "
    "to select a configured vault. Read-only."
)

_METADATA_TREE_DESCRIPTION = (
    "List the vault's Knotica metadata substrate as a nested tree: root `.knotica/` "
    "(prompts, locks, ingest-activity when present), optional root SCHEMA.md/log.md, "
    "and per-topic `{topic}/.knotica/` state (loop-state, compile-state, metrics, "
    "datasets, compiled, prompts, …). Only existing paths are returned — not the "
    "full wiki page tree. Pass topic to scope to one topic branch plus root metadata. "
    "Pass vault to select a configured vault. Read-only."
)


def register_vault_tools(mcp: FastMCP) -> None:
    """Register vault health / remediation tools on ``mcp``."""

    @mcp.tool(name="doctor_run", description=_DOCTOR_DESCRIPTION)
    def doctor_run(quick: bool = False, fix: bool = False, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _doctor_payload(store, resolved, quick=quick, include_fix=fix),
        )

    @mcp.tool(name="doctor_repair", description=_DOCTOR_REPAIR_DESCRIPTION)
    def doctor_repair_tool(
        mode: str = "dry-run",
        paths_json: str = "[]",
        all_tracked: bool = False,
        delete_untracked: bool = False,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _doctor_repair_payload(
                store,
                resolved.path,
                mode=mode,
                paths_json=paths_json,
                all_tracked=all_tracked,
                delete_untracked=delete_untracked,
            ),
        )

    @mcp.tool(name="okf_check", description=_OKF_CHECK_DESCRIPTION)
    def okf_check(strict: bool = False, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: _okf_check_payload(store, strict=strict),
        )

    @mcp.tool(name="okf_repair", description=_OKF_REPAIR_DESCRIPTION)
    def okf_repair(mode: str = "dry-run", force: bool = False, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: _okf_repair_payload(store, mode=mode, force=force),
        )

    @mcp.tool(name="loop_run_once", description=_LOOP_ONCE_DESCRIPTION)
    def loop_run_once(topic: str, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _loop_once_payload(store, resolved.path, topic),
        )

    @mcp.tool(name="loop_set_baseline", description=_LOOP_SET_BASELINE_DESCRIPTION)
    def loop_set_baseline(topic: str, scalar: float, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _loop_set_baseline_payload(store, resolved.path, topic, scalar),
        )

    @mcp.tool(name="loop_baseline_policy", description=_LOOP_POLICY_DESCRIPTION)
    def loop_baseline_policy(topic: str, policy: str, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _loop_policy_payload(store, resolved.path, topic, policy),
        )

    @mcp.tool(name="loop_rebaseline", description=_LOOP_REBASELINE_DESCRIPTION)
    def loop_rebaseline(topic: str, mode: str = "best", vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _loop_rebaseline_payload(store, resolved.path, topic, mode),
        )

    @mcp.tool(name="vault_lint", description=_LINT_DESCRIPTION)
    def vault_lint(topic: str = "", vault: str = "") -> ToolResult:
        from knotica.core.lint import lint_vault

        return with_resolved_vault(
            vault,
            lambda store, _resolved: envelope.read_ok(
                {
                    "topic": topic.strip().strip("/"),
                    "violations": [violation.render() for violation in lint_vault(store, topic)],
                }
            ),
        )

    @mcp.tool(name="vault_metadata_tree", description=_METADATA_TREE_DESCRIPTION)
    def vault_metadata_tree(topic: str = "", vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: envelope.read_ok(
                gather_vault_metadata_tree(store, resolved.path, topic=topic)
            ),
        )


def _doctor_payload(
    store: VaultStore,
    resolved: ResolvedVault,
    *,
    quick: bool,
    include_fix: bool,
) -> dict[str, Any]:
    diagnosis = diagnose()
    detail = diagnosis.detail or f"vault ready ({resolved.name})"
    rows = run_doctor_checks(store, resolved.path, config_detail=detail, quick=quick)
    return envelope.read_ok(
        build_doctor_payload(resolved.path, rows, quick=quick, include_fix=include_fix)
    )


def _doctor_repair_payload(
    store: VaultStore,
    vault_path: Path,
    *,
    mode: str,
    paths_json: str,
    all_tracked: bool,
    delete_untracked: bool,
) -> dict[str, Any]:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in {"dry-run", "apply"}:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"doctor_repair mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' or mode='apply'.",
        )
    try:
        parsed = json.loads(paths_json) if paths_json.strip() else []
    except json.JSONDecodeError as exc:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="doctor_repair failed because paths_json is not valid JSON",
            fix="Pass paths_json as a JSON array of strings, e.g. '[\"index.md\"]'.",
        ) from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="doctor_repair failed because paths_json must be a JSON array of strings",
            fix="Pass paths_json like '[\"topic/page.md\"]'.",
        )
    result = doctor_repair(
        store,
        vault_path,
        apply=cleaned == "apply",
        paths=tuple(parsed),
        all_tracked=all_tracked,
        delete_untracked=delete_untracked,
    )
    # Operations return ok()/err() envelopes; surface failures as KnoticaError
    # so with_resolved_vault emits isError=True (same pattern as write tools).
    error = result.get("error")
    if isinstance(error, dict):
        raise KnoticaError(
            ErrorCode(error["code"]),
            error["message"],
            fix=error.get("fix"),
            retryable=error.get("retryable"),
        )
    return result


def _okf_check_payload(store: VaultStore, *, strict: bool) -> dict[str, Any]:
    result = check_vault(store, strict=strict)
    return envelope.read_ok(
        {
            "status": result.status,
            "failed": result.failed,
            "bundle_root": result.bundle_root,
            "concept_files_checked": result.concept_files_checked,
            "reserved_files_checked": result.reserved_files_checked,
            "errors": [
                {
                    "path": err.path,
                    "code": err.code,
                    "message": err.message,
                    "severity": err.severity,
                }
                for err in result.errors
            ],
            # Avoid envelope-reserved key ``warnings``.
            "notes": list(result.warnings),
            "strict_failures": list(result.strict_failures),
        }
    )


def _okf_repair_payload(store: VaultStore, *, mode: str, force: bool) -> dict[str, Any]:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in {"dry-run", "apply"}:
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"okf_repair mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' to preview, or mode='apply' to commit repairs.",
        )
    apply = cleaned == "apply"
    try:
        result = repair_vault(store, RepairOptions(apply=apply, force=force))
    except ValueError as exc:
        raise KnoticaError(
            code=ErrorCode.GIT_ERROR,
            message=str(exc),
            fix="Commit or stash changes, or pass force=true (same as CLI --force).",
        ) from exc
    return envelope.read_ok(
        {
            "status": result.status,
            "dry_run": result.dry_run,
            "files_changed": list(result.files_changed),
            # Avoid envelope-reserved key ``warnings``.
            "notes": list(result.warnings),
            "report_path": result.report_path,
            "commit_sha": result.commit_sha,
            "mode": cleaned,
        }
    )


def _loop_once_payload(store: VaultStore, vault_path: Path, topic: str) -> dict[str, Any]:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    runner = build_loop_runner(
        vault_path,
        cleaned,
        evaluate=harness_evaluate,
        store=store,
        arena_enabled=True,
        arena_score=heuristic_arena_score,
        # Pass this module's own ``LoopRunner`` binding so a test that substitutes it
        # still intercepts construction routed through the shared factory.
        runner_cls=LoopRunner,
    )
    # Mirror one `knotica loop` watch tick: observe the default branch first
    # (new content → eval, first observation auto-freezes the baseline), then
    # gate at most one pending candidate. The observation result wins the
    # payload when it acted — it is the newer information.
    observed = runner.observe_default()
    candidate = runner.poll_once()
    result = candidate if candidate.acted or not observed.acted else observed
    return envelope.read_ok(
        {
            "topic": cleaned,
            "acted": result.acted,
            "branch": result.branch,
            "sha": result.sha,
            "decision": result.decision.value if result.decision else "none",
            "scalar": result.scalar,
            "message": result.message,
            "observed": {
                "acted": observed.acted,
                "decision": observed.decision.value if observed.decision else "none",
                "scalar": observed.scalar,
                "message": observed.message,
            },
        }
    )


def _loop_set_baseline_payload(
    store: VaultStore, vault_path: Path, topic: str, scalar: float
) -> dict[str, Any]:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    runner = build_loop_runner(
        vault_path, cleaned, evaluate=harness_evaluate, store=store, runner_cls=LoopRunner
    )
    state = runner.set_baseline(float(scalar))
    baseline = state.baseline_scalar
    assert baseline is not None
    return envelope.read_ok(
        {
            "topic": cleaned,
            "baseline_scalar": baseline,
            "harness_version": state.baseline_harness_version,
            "stage": state.stage.value,
            "message": f"baseline frozen at {baseline:.4f}",
        }
    )


def _loop_policy_payload(
    store: VaultStore, vault_path: Path, topic: str, policy: str
) -> dict[str, Any]:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    runner = build_loop_runner(
        vault_path, cleaned, evaluate=harness_evaluate, store=store, runner_cls=LoopRunner
    )
    try:
        state = runner.set_baseline_policy(policy)
    except ValueError as error:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED, str(error), fix="Pass policy 'latest' or 'best'."
        ) from error
    return envelope.read_ok(
        {
            "topic": cleaned,
            "baseline_policy": state.baseline_policy,
            "baseline_scalar": state.baseline_scalar,
            "message": f"gate policy set to {state.baseline_policy}",
        }
    )


def _loop_rebaseline_payload(
    store: VaultStore, vault_path: Path, topic: str, mode: str
) -> dict[str, Any]:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    runner = build_loop_runner(
        vault_path, cleaned, evaluate=harness_evaluate, store=store, runner_cls=LoopRunner
    )
    try:
        state = runner.rebaseline(mode)
    except ValueError as error:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            str(error),
            fix="Pass mode 'best' or 'latest'; the topic needs at least one metrics record.",
        ) from error
    baseline = state.baseline_scalar
    assert baseline is not None
    return envelope.read_ok(
        {
            "topic": cleaned,
            "baseline_scalar": baseline,
            "harness_version": state.baseline_harness_version,
            "baseline_policy": state.baseline_policy,
            "message": f"baseline re-frozen ({mode}) at {baseline:.4f}",
        }
    )
