"""Vault health + remediation payload helpers for the two action dispatchers.

Thin adapters over existing CLI/core paths — same semantics as
``knotica doctor``, ``knotica okf check|repair``, and ``loop_runner --once``.
No new repair algorithms; the UI only triggers and watches what already exists.

These functions have no MCP tool registrations of their own — they are
imported directly by ``tools_dispatch_vault_health.py`` and
``tools_dispatch_loop.py``, the sole entry points into this logic.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePath
from typing import Any

from mcp.types import CallToolResult

from knotica.cli.init import _atomic_write, _dump_config_toml, _read_config
from knotica.core.arena import heuristic_arena_score
from knotica.core.config import ResolvedVault, config_file_path, diagnose
from knotica.core.doctor import build_doctor_payload, run_doctor_checks
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import LoopRunner, build_loop_runner, harness_evaluate
from knotica.core.loop_cadence_config import LOOP_CONFIG_SECTION, resolve_loop_cadence_config
from knotica.core.models_config import resolve_models_config
from knotica.core.operations.doctor_repair import doctor_repair
from knotica.core.page import TopicNotFoundError
from knotica.mcp_server import envelope
from knotica.okf.check import check_vault
from knotica.okf.repair import RepairOptions, repair_vault
from knotica.store import VaultStore

ToolResult = CallToolResult


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


def _loop_once_payload(
    store: VaultStore, vault_path: Path, topic: str, *, confirm: str = ""
) -> dict[str, Any]:
    """Two-phase decision envelope for a billed, human-triggered loop tick.

    Reuses the same nonce mint/consume/TTL mechanism as ``run_eval`` (see
    ``_loop_run_eval_payload``), keyed under a ``run-once``-specific nonce
    file so the two actions never collide. Phase 1 (no ``confirm``, or a
    stale/mismatched/expired nonce): mints a fresh preview envelope and
    returns -- never calls ``observe_default`` or ``poll_once``, never bills.
    Phase 2 (a ``confirm`` matching the unexpired, unconsumed nonce): consumes
    the nonce (single-use) and runs the actual tick (both calls, exactly as
    the unconfirmed legacy behavior did).
    """
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if confirm.strip():
        consumed = _consume_run_once_nonce(vault_path, cleaned, confirm.strip())
        if consumed is not None:
            return _execute_run_once(store, vault_path, cleaned)
    nonce = _mint_run_once_nonce(vault_path, cleaned)
    return envelope.read_ok(
        {
            "action": "run_once",
            "topic": cleaned,
            "estimated_cost": (
                "1 default-branch observation eval (if new content exists) plus "
                "at most one pending candidate-gate eval"
            ),
            "confirm_nonce": nonce,
            "ttl": _RUN_EVAL_NONCE_TTL_SECONDS,
        }
    )


def _execute_run_once(store: VaultStore, vault_path: Path, topic: str) -> dict[str, Any]:
    """Run one actual loop tick -- the billing boundary for ``run_once``."""
    runner = build_loop_runner(
        vault_path,
        topic,
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
            "action": "run_once",
            "topic": topic,
            "billed": True,
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


#: Single-use nonce lifetime for the ``run_eval`` two-phase decision envelope.
_RUN_EVAL_NONCE_TTL_SECONDS = 300.0

#: Runtime (gitignored) directory the nonce file lives in -- same home as the
#: loop heartbeat and vault mutation lock. Never vault content, never a
#: ``VaultStore`` write, never a git commit.
_LOOP_LOCKS_DIR = PurePath(".knotica/locks")


def _loop_cadence_payload(
    topic: str,
    *,
    eval_min_interval_hours: float | None,
    eval_window: str | None,
    eval_num_threads: int | None,
) -> dict[str, Any]:
    """Read (no params) or additively write (any param) the ``[loop]`` cadence config."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if (
        eval_min_interval_hours is not None
        or eval_window is not None
        or eval_num_threads is not None
    ):
        _write_loop_cadence_config(
            eval_min_interval_hours=eval_min_interval_hours,
            eval_window=eval_window,
            eval_num_threads=eval_num_threads,
        )
    resolved = resolve_loop_cadence_config()
    return envelope.read_ok(
        {
            "topic": cleaned,
            "eval_min_interval_hours": resolved.eval_min_interval_hours,
            "eval_window": resolved.eval_window,
            "eval_num_threads": resolved.eval_num_threads,
        }
    )


def _write_loop_cadence_config(
    *,
    eval_min_interval_hours: float | None,
    eval_window: str | None,
    eval_num_threads: int | None,
) -> None:
    """Additively merge cadence keys into ``config.toml``'s ``[loop]`` table.

    Reuses ``cli.init``'s read/dump/atomic-write primitives verbatim (no
    bespoke TOML-dump logic here) -- every sibling top-level key and every
    other table (``[models]``, ``[gapfill]``, ``[vaults.*]``, ...) round-trips
    untouched because only the ``loop`` dict key is mutated before the
    re-serialize.
    """
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_config(path)
    section = dict(data.get(LOOP_CONFIG_SECTION, {}))
    if eval_min_interval_hours is not None:
        section["eval_min_interval_hours"] = eval_min_interval_hours
    if eval_window is not None:
        section["eval_window"] = eval_window
    if eval_num_threads is not None:
        section["eval_num_threads"] = eval_num_threads
    data[LOOP_CONFIG_SECTION] = section
    _atomic_write(path, _dump_config_toml(data))


def _loop_run_eval_payload(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    *,
    confirm: str,
    num_threads: int | None,
) -> dict[str, Any]:
    """Two-phase decision envelope for a billed, human-triggered eval.

    Phase 1 (no ``confirm``, or a stale/mismatched/expired nonce): mints a
    fresh preview envelope and returns -- never calls ``observe_default``,
    never bills. Phase 2 (a ``confirm`` matching the unexpired, unconsumed
    nonce): consumes the nonce (single-use) and runs the eval with
    ``force=True``, bypassing cadence only -- ``_observation_hold`` still
    applies.
    """
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if confirm.strip():
        consumed = _consume_run_eval_nonce(vault_path, cleaned, confirm.strip())
        if consumed is not None:
            return _execute_run_eval(
                store,
                vault_path,
                cleaned,
                worker=str(consumed["worker"]),
                judge=str(consumed["judge"]),
                num_threads=int(consumed["num_threads"]),
            )
    models = resolve_models_config()
    cadence = resolve_loop_cadence_config()
    requested_threads = num_threads if num_threads is not None else cadence.eval_num_threads
    nonce = _mint_run_eval_nonce(
        vault_path,
        cleaned,
        worker=models.worker,
        judge=models.judge,
        num_threads=requested_threads,
    )
    return envelope.read_ok(
        {
            "action": "run_eval",
            "topic": cleaned,
            "worker": models.worker,
            "judge": models.judge,
            "num_threads": requested_threads,
            "estimated_cost": (
                f"~1 worker+judge call pair per golden question at "
                f"num_threads={requested_threads} (total calls scale with the "
                f"topic's golden-set size)"
            ),
            "confirm_nonce": nonce,
            "ttl": _RUN_EVAL_NONCE_TTL_SECONDS,
        }
    )


def _execute_run_eval(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    *,
    worker: str,
    judge: str,
    num_threads: int,
) -> dict[str, Any]:
    evaluate = partial(
        harness_evaluate,
        num_threads=num_threads,
        worker_snapshot=worker,
        judge_snapshot=judge,
    )
    runner = build_loop_runner(
        vault_path, topic, evaluate=evaluate, store=store, runner_cls=LoopRunner
    )
    result = runner.observe_default(force=True)
    return envelope.read_ok(
        {
            "action": "run_eval",
            "topic": topic,
            "billed": True,
            "acted": result.acted,
            "decision": result.decision.value if result.decision else "none",
            "scalar": result.scalar,
            "message": result.message,
            "worker": worker,
            "judge": judge,
            "num_threads": num_threads,
        }
    )


def _nonce_path(vault_path: Path, kind: str, topic: str) -> Path:
    """Nonce file location for a given billed action ``kind`` (e.g. ``run-eval``,
    ``run-once``) and topic -- one file per (kind, topic) pair so concurrent
    billed actions never collide."""
    safe_topic = topic.replace("/", "-") or "vault"
    return vault_path / _LOOP_LOCKS_DIR / f"{kind}-nonce-{safe_topic}.json"


def _mint_nonce(vault_path: Path, kind: str, topic: str, extra: dict[str, Any]) -> str:
    """Mint + persist a single-use nonce for a billed action.

    Shared mechanism behind both ``run_eval`` and ``run_once``'s two-phase
    decision envelopes -- see ``_loop_run_eval_payload``/``_loop_once_payload``.
    """
    nonce = secrets.token_urlsafe(16)
    path = _nonce_path(vault_path, kind, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nonce": nonce,
        "topic": topic,
        "minted_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return nonce


def _consume_nonce(vault_path: Path, kind: str, topic: str, confirm: str) -> dict[str, Any] | None:
    """Verify + consume a single-use nonce; returns the minted payload or ``None``.

    The nonce file is deleted unconditionally on read (single-use, no probing
    a live nonce by sending a wrong ``confirm`` value) -- a mismatch or
    expiry falls through to phase 1, minting a fresh nonce.
    """
    path = _nonce_path(vault_path, kind, topic)
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    path.unlink(missing_ok=True)
    if payload.get("nonce") != confirm:
        return None
    try:
        minted_at = datetime.fromisoformat(str(payload["minted_at"]))
    except (KeyError, ValueError):
        return None
    age = (datetime.now(UTC) - minted_at).total_seconds()
    if age > _RUN_EVAL_NONCE_TTL_SECONDS:
        return None
    return payload


def _run_eval_nonce_path(vault_path: Path, topic: str) -> Path:
    return _nonce_path(vault_path, "run-eval", topic)


def _mint_run_eval_nonce(
    vault_path: Path, topic: str, *, worker: str, judge: str, num_threads: int
) -> str:
    return _mint_nonce(
        vault_path,
        "run-eval",
        topic,
        {"worker": worker, "judge": judge, "num_threads": num_threads},
    )


def _consume_run_eval_nonce(vault_path: Path, topic: str, confirm: str) -> dict[str, Any] | None:
    return _consume_nonce(vault_path, "run-eval", topic, confirm)


def _run_once_nonce_path(vault_path: Path, topic: str) -> Path:
    return _nonce_path(vault_path, "run-once", topic)


def _mint_run_once_nonce(vault_path: Path, topic: str) -> str:
    return _mint_nonce(vault_path, "run-once", topic, {})


def _consume_run_once_nonce(vault_path: Path, topic: str, confirm: str) -> dict[str, Any] | None:
    return _consume_nonce(vault_path, "run-once", topic, confirm)
