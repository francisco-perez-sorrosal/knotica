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
from functools import partial
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult

from knotica.core.arena import heuristic_arena_score
from knotica.core.best_effort import best_effort
from knotica.core.config import ResolvedVault, config_file_path, diagnose
from knotica.core.config_write import atomic_write, dump_config_toml, read_config
from knotica.core.doctor import build_doctor_payload, run_doctor_checks
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.loop import LoopRunner, build_loop_runner, harness_evaluate
from knotica.core.loop_cadence_config import (
    LOOP_CONFIG_SECTION,
    resolve_loop_cadence_config,
    validate_arena_scorer,
    validate_eval_min_interval_hours,
    validate_eval_num_threads,
    validate_eval_window,
)
from knotica.core.loop_state import read_loop_state
from knotica.core.models_config import resolve_models_config
from knotica.core.operations.doctor_repair import doctor_repair
from knotica.core.page import TopicNotFoundError
from knotica.mcp_server import confirm_nonce, dispatch_telemetry, envelope
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
            "skipped_dirty": list(result.skipped_dirty),
            # Reported, not silent: these files moved on disk and the user can
            # see them in Obsidian.
            "relocated_reports": [
                {"from": old_path, "to": new_path}
                for old_path, new_path in result.relocated_reports
            ],
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
            dispatch_telemetry.record_two_phase(
                "loop", "run_once", cleaned, outcome=dispatch_telemetry.OUTCOME_CONFIRMED
            )
            return _execute_run_once(store, vault_path, cleaned)
        dispatch_telemetry.record_two_phase(
            "loop", "run_once", cleaned, outcome=dispatch_telemetry.OUTCOME_STALE_CONFIRM
        )
    nonce = _mint_run_once_nonce(vault_path, cleaned)
    dispatch_telemetry.record_two_phase(
        "loop", "run_once", cleaned, outcome=dispatch_telemetry.OUTCOME_PREVIEW
    )
    return envelope.read_ok(
        {
            "action": "run_once",
            "topic": cleaned,
            "estimated_cost": (
                "1 default-branch observation eval (if new content exists) plus "
                "at most one pending candidate-gate eval"
            ),
            # What would decline this tick if confirmed now. `run_once` does not
            # force, so both pacing holds apply to it.
            "holds": _hold_preview(store, vault_path, cleaned, force=False),
            "confirm_nonce": nonce,
            "ttl": confirm_nonce.NONCE_TTL_SECONDS,
        }
    )


def _hold_preview(
    store: VaultStore, vault_path: Path, topic: str, *, force: bool
) -> dict[str, Any]:
    """Read-only: what would decline this billed action if it were confirmed now.

    Best-effort -- a preview that cannot compute the holds must still quote the
    cost and mint its nonce, so a probe failure degrades to "unknown", never to
    a failed preview.
    """
    preview: dict[str, Any] = {"held": False, "reasons": [], "cadence_remaining_seconds": None}
    with best_effort():
        runner = build_loop_runner(
            vault_path, topic, evaluate=harness_evaluate, store=store, runner_cls=LoopRunner
        )
        preview = runner.hold_preview(force=force)
    return preview


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
            # Derived, not asserted. Both legs decline without reaching an eval
            # when a hold or an unchanged HEAD applies, and a tick that made no
            # model call did not bill -- reporting otherwise taught operators to
            # read a free no-op as money spent.
            "billed": observed.acted or candidate.acted,
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
    # Captured before the write so the caller can see whether the bar actually
    # moved. ``mode="best"`` re-picks the high-water mark *among reachable
    # bars*: when that mark sits above the newest measurement the runner now
    # refuses with a typed error rather than freezing a bar the corpus cannot
    # clear (the ``baseline_unreachable`` misconfiguration, caught at both
    # freeze-time entry points -- ``set_baseline`` shares the refusal). Note
    # that ``mode`` is this operation's own
    # argument, NOT the topic's ongoing ``baseline_policy``; they are named
    # alike and mean different things, which is the misreading this field
    # exists to prevent.
    previous = read_loop_state(store, cleaned)
    previous_scalar = previous.baseline_scalar if previous is not None else None
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
    changed = previous_scalar is None or abs(float(previous_scalar) - float(baseline)) > 1e-12
    message = (
        f"baseline re-frozen ({mode}) at {baseline:.4f}"
        if changed
        else f"baseline unchanged at {baseline:.4f}: {mode!r} already selects this record"
    )
    return envelope.read_ok(
        {
            "topic": cleaned,
            "baseline_scalar": baseline,
            "previous_scalar": previous_scalar,
            "changed": changed,
            "harness_version": state.baseline_harness_version,
            "baseline_policy": state.baseline_policy,
            "message": message,
        }
    )


def _loop_cadence_payload(
    vault_path: Path,
    topic: str,
    *,
    eval_min_interval_hours: float | None,
    eval_window: str | None,
    eval_num_threads: int | None,
    arena_scorer: str | None,
    confirm: str = "",
) -> dict[str, Any]:
    """Read (no params) or additively write (any param) the ``[loop]`` config."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    updates = _validated_cadence_updates(
        eval_min_interval_hours=eval_min_interval_hours,
        eval_window=eval_window,
        eval_num_threads=eval_num_threads,
        arena_scorer=arena_scorer,
    )
    if updates:
        preview = _arena_scorer_spend_gate(vault_path, cleaned, updates, confirm)
        if preview is not None:
            return preview
        _write_loop_cadence_config(updates)
    resolved = resolve_loop_cadence_config()
    return envelope.read_ok(
        {
            "topic": cleaned,
            "eval_min_interval_hours": resolved.eval_min_interval_hours,
            "eval_window": resolved.eval_window,
            "eval_num_threads": resolved.eval_num_threads,
            "arena_scorer": resolved.arena_scorer,
        }
    )


def _validated_cadence_updates(
    *,
    eval_min_interval_hours: float | None,
    eval_window: str | None,
    eval_num_threads: int | None,
    arena_scorer: str | None,
) -> dict[str, object]:
    """The supplied ``[loop]`` keys, every one validated; empty when none were.

    **All four** are checked before any of them is written. Validating one key
    and writing the rest raw leaves a config the resolver refuses to parse --
    the caller gets an error *and* every unrelated ``[loop]`` reader
    (``build_loop_runner``, the cadence check, the CLI watcher) breaks until a
    human edits the file by hand. ``from_argument`` codes the refusal as a bad
    argument rather than a broken install, because that is what it is here.
    """
    updates: dict[str, object] = {}
    if eval_min_interval_hours is not None:
        updates["eval_min_interval_hours"] = validate_eval_min_interval_hours(
            eval_min_interval_hours, from_argument=True
        )
    if eval_window is not None:
        updates["eval_window"] = validate_eval_window(eval_window, from_argument=True)
    if eval_num_threads is not None:
        updates["eval_num_threads"] = validate_eval_num_threads(
            eval_num_threads, from_argument=True
        )
    if arena_scorer is not None:
        updates["arena_scorer"] = validate_arena_scorer(arena_scorer, from_argument=True)
    return updates


def _arena_scorer_spend_gate(
    vault_path: Path, topic: str, updates: dict[str, object], confirm: str
) -> dict[str, Any] | None:
    """Two-phase confirm for ``arena_scorer="eval"``; ``None`` means proceed.

    Switching the arena onto the eval-backed scorer commits strictly more spend
    than ``run_eval`` does -- every future gate-failure race bills one
    golden-set eval *per variant*, and those races fire autonomously from the
    daemon with no human present. A billed decision that big cannot be one
    unconfirmed call when a single eval is two. Switching back to ``heuristic``
    is free and needs no gate.
    """
    if updates.get("arena_scorer") != "eval":
        return None
    if confirm.strip():
        if _consume_arena_scorer_nonce(vault_path, topic, confirm.strip()) is not None:
            dispatch_telemetry.record_two_phase(
                "loop", "cadence", topic, outcome=dispatch_telemetry.OUTCOME_CONFIRMED
            )
            return None
        dispatch_telemetry.record_two_phase(
            "loop", "cadence", topic, outcome=dispatch_telemetry.OUTCOME_STALE_CONFIRM
        )
    nonce = _mint_arena_scorer_nonce(vault_path, topic)
    dispatch_telemetry.record_two_phase(
        "loop", "cadence", topic, outcome=dispatch_telemetry.OUTCOME_PREVIEW
    )
    current = resolve_loop_cadence_config()
    return envelope.read_ok(
        {
            "action": "cadence",
            "topic": topic,
            "arena_scorer": current.arena_scorer,
            "requested_arena_scorer": "eval",
            "estimated_cost": (
                "~1 full golden-set eval per raced variant, on every future "
                "gate-failure race (a 4-variant race over a 21-question set is 84 "
                "worker+judge pairs) -- races fire autonomously from the loop daemon"
            ),
            "confirm_nonce": nonce,
            "ttl": confirm_nonce.NONCE_TTL_SECONDS,
            "message": (
                "nothing was written: arena_scorer='eval' is a spending decision. "
                "Call again passing this nonce as `confirm` to apply it."
            ),
        }
    )


def _mint_arena_scorer_nonce(vault_path: Path, topic: str) -> str:
    return confirm_nonce.mint(vault_path, "arena-scorer", topic, {})


def _consume_arena_scorer_nonce(
    vault_path: Path, topic: str, confirm: str
) -> dict[str, Any] | None:
    return confirm_nonce.consume(vault_path, "arena-scorer", topic, confirm)


def _write_loop_cadence_config(updates: dict[str, object]) -> None:
    """Additively merge validated ``[loop]`` keys into ``config.toml``.

    Reuses ``core.config_write``'s read/dump/atomic-write primitives (no
    bespoke TOML-dump logic here) -- every sibling top-level key and every
    other table (``[models]``, ``[gapfill]``, ``[vaults.*]``, ...) round-trips
    untouched because only the ``loop`` dict key is mutated before the
    re-serialize.

    Every value here is already validated (see
    :func:`_validated_cadence_updates`): a rejected write must leave the config
    byte-identical, so this function opens the file only once nothing can be
    rejected.
    """
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = read_config(path)
    section = dict(data.get(LOOP_CONFIG_SECTION, {}))
    section.update(updates)
    data[LOOP_CONFIG_SECTION] = section
    atomic_write(path, dump_config_toml(data))


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
    ``force=True``, which clears both *pacing* holds -- cadence and the
    blocked/failure retry floor. ``_observation_hold`` (a live ingest, or the
    quiet window) still applies: those describe a vault that is mid-write, not a
    loop being paced, so a human cannot usefully override them.
    """
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    if confirm.strip():
        consumed = _consume_run_eval_nonce(vault_path, cleaned, confirm.strip())
        if consumed is not None:
            dispatch_telemetry.record_two_phase(
                "loop", "run_eval", cleaned, outcome=dispatch_telemetry.OUTCOME_CONFIRMED
            )
            return _execute_run_eval(
                store,
                vault_path,
                cleaned,
                worker=str(consumed["worker"]),
                judge=str(consumed["judge"]),
                num_threads=int(consumed["num_threads"]),
            )
        dispatch_telemetry.record_two_phase(
            "loop", "run_eval", cleaned, outcome=dispatch_telemetry.OUTCOME_STALE_CONFIRM
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
    dispatch_telemetry.record_two_phase(
        "loop", "run_eval", cleaned, outcome=dispatch_telemetry.OUTCOME_PREVIEW
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
            # `run_eval` forces, so only the non-pacing ingest hold can bite.
            "holds": _hold_preview(store, vault_path, cleaned, force=True),
            "confirm_nonce": nonce,
            "ttl": confirm_nonce.NONCE_TTL_SECONDS,
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
            # See ``_execute_run_once``: an observation that declined reached no
            # model, so it billed nothing.
            "billed": result.acted,
            "acted": result.acted,
            "decision": result.decision.value if result.decision else "none",
            "scalar": result.scalar,
            "message": result.message,
            "worker": worker,
            "judge": judge,
            "num_threads": num_threads,
        }
    )


def _run_eval_nonce_path(vault_path: Path, topic: str) -> Path:
    return confirm_nonce.nonce_path(vault_path, "run-eval", topic)


def _mint_run_eval_nonce(
    vault_path: Path, topic: str, *, worker: str, judge: str, num_threads: int
) -> str:
    return confirm_nonce.mint(
        vault_path,
        "run-eval",
        topic,
        {"worker": worker, "judge": judge, "num_threads": num_threads},
    )


def _consume_run_eval_nonce(vault_path: Path, topic: str, confirm: str) -> dict[str, Any] | None:
    return confirm_nonce.consume(vault_path, "run-eval", topic, confirm)


def _run_once_nonce_path(vault_path: Path, topic: str) -> Path:
    return confirm_nonce.nonce_path(vault_path, "run-once", topic)


def _mint_run_once_nonce(vault_path: Path, topic: str) -> str:
    return confirm_nonce.mint(vault_path, "run-once", topic, {})


def _consume_run_once_nonce(vault_path: Path, topic: str, confirm: str) -> dict[str, Any] | None:
    return confirm_nonce.consume(vault_path, "run-once", topic, confirm)
