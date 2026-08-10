"""MCP tools ``source_ingest_open`` / ``source_ingest_submit`` -- the
client-driven session surface for ingesting one approved gap-fill suggestion.

Thin adapters over :mod:`knotica.core.source_ingest` (session lifecycle) and
:mod:`knotica.core.loop` (the gate). ``source_ingest_open`` opens or resumes a
private ingest session; the client then writes to it via the additive
``candidate`` argument on ``store_source``/``write_page``. ``source_ingest_submit``
finalizes the session: ``mode="dry-run"`` previews readiness with zero side
effects; ``mode="apply"`` publishes the candidate and synchronously drives the
loop's gate for it (rather than waiting on the async watcher), returning the
verdict -- merged, refused, or blocked on a missing gate baseline.

Idempotent, but only over the question it actually answered. A stamped
``gate_outcome`` is replayed when the inputs it was computed from -- candidate
tree, golden manifest, baseline, harness (see
:mod:`knotica.core.gate_inputs`) -- are all unchanged, and it says so with
``cached: true``. Move any one of them and the next submit evaluates afresh.
Keying the replay on the suggestion id alone, as this once did, meant a rebuilt
candidate measured against a replaced golden set and a corrected baseline still
returned the original verdict quoting the original bar.

``mode=dry-run`` runs its preflight either way: the checks are free, and they
are the caller's only readout of candidate health.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core import gate_inputs, source_ingest
from knotica.core.arena import heuristic_arena_score
from knotica.core.best_effort import best_effort
from knotica.core.branch_namespaces import quarantine_branch_name
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gapfill import GATE_VERDICT_MERGED, GATE_VERDICT_REFUSED, suggestions_path
from knotica.core.lint import lint_vault
from knotica.core.loop import LoopRunner, build_loop_runner, harness_evaluate
from knotica.core.loop_state import read_loop_state
from knotica.core.page import TopicNotFoundError
from knotica.core.records import SuggestionRecord, parse_suggestions_jsonl
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import LocalFSStore, VaultStore

__all__ = ["register_source_ingest_tools"]

ToolResult = CallToolResult

_APPROVED_STATUS = "approved"
_MODES: frozenset[str] = frozenset({"dry-run", "apply"})

#: Bounded drain for the synchronous apply-time gate cycle: `poll_once`
#: processes the *oldest unhandled* ``loop/c/*`` tip across every topic, not
#: necessarily the one this call just published, so a handful of unrelated
#: pending candidates may need to clear first. Bounded rather than unbounded
#: so a stuck loop fails loud instead of hanging the tool call.
_MAX_GATE_CYCLES = 20

_OPEN_DESCRIPTION = (
    "Open (or resume) an ingest of ONE approved gap-fill suggestion onto its own "
    "candidate context, so the loop can gate the result before it touches the wiki. "
    "Pass the suggestion_id of an APPROVED suggestion (from suggestions_read "
    "status=approved). Returns an opaque `candidate` handle to pass to every "
    "store_source/write_page for this ingest, the provenance to weave into the "
    "pages, and a resume block listing what is already written (re-open to resume "
    "a partial ingest -- never restart). Idempotent: opening twice returns the same "
    "handle and the current state. Does NOT ingest -- you then follow the ingest "
    "protocol writing to `candidate`. Read-adjacent: creates the candidate context "
    "only; no wiki page changes on the default branch. Never call this from "
    "detection alone -- only after the user has explicitly confirmed the "
    "suggestion is approved for ingest; an unconfirmed detection routes to "
    "suggestions_read or an offer instead."
)

_SUBMIT_DESCRIPTION = (
    "Finalize an approved-suggestion ingest and hand its candidate to the loop's "
    "gate, which evaluates the wiki WITH the new source and MERGES it only if it "
    "closes the gap without regressing other answers -- otherwise it REFUSES it "
    "(quarantined with a per-question diff, never silently kept). mode=dry-run "
    "checks the candidate is lint-clean, has the source and ≥1 page, and reports "
    "whether the topic has a gate baseline. mode=apply seals the ingest, runs the "
    "gate, and returns the verdict (merged, or refused with the top regressed "
    "questions). Idempotent: re-submitting an UNCHANGED gated candidate replays "
    "the prior verdict, flagged `cached: true`; if the candidate, the golden set, "
    "the baseline or the harness moved since, it evaluates afresh. "
    "Requires the vault lock. mode=apply never fires from "
    "detection alone -- only after the user has explicitly confirmed the "
    "ingest; an unconfirmed detection routes to a dry-run preview or an offer "
    "instead."
)


def register_source_ingest_tools(mcp: FastMCP) -> None:
    """Register ``source_ingest_open`` and ``source_ingest_submit`` on ``mcp``."""

    @mcp.tool(name="source_ingest_open", description=_OPEN_DESCRIPTION)
    def source_ingest_open(topic: str, suggestion_id: str, vault: str = "") -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _open_payload(store, resolved.path, topic, suggestion_id),
        )

    @mcp.tool(name="source_ingest_submit", description=_SUBMIT_DESCRIPTION)
    def source_ingest_submit(
        topic: str,
        suggestion_id: str,
        mode: str = "dry-run",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _submit_payload(
                store, resolved.path, topic, suggestion_id, mode=mode
            ),
        )


# ---------------------------------------------------------------------------
# source_ingest_open
# ---------------------------------------------------------------------------


def _open_payload(
    store: VaultStore, vault_path: Path, topic: str, suggestion_id: str
) -> dict[str, Any]:
    cleaned_topic = _validate_topic(topic)
    handle = source_ingest.open_ingest(store, vault_path, cleaned_topic, suggestion_id)
    record = _find_suggestion(store, cleaned_topic, suggestion_id)
    return {
        "topic": cleaned_topic,
        "suggestion_id": suggestion_id,
        "candidate": handle.candidate,
        "state": handle.state,
        "resume": {
            "source_present": handle.resume.source_present,
            "pages_present": list(handle.resume.pages_present),
            "index_synced": handle.resume.index_synced,
            # Non-null only when this open rebuilt the session from a refused
            # candidate's quarantine ref -- the one case where `state:
            # "resumed"` does not mean the branch was simply still there.
            "restored_from": handle.resume.restored_from,
        },
        "provenance": dict(handle.provenance),
        # Visible so a client re-opening a REFUSED (still-approved, re-workable)
        # suggestion sees its history instead of treating it as freshly queued
        # (the reward-hacking-adjacent risk of re-ingesting a proven-dilutive
        # source in a loop) -- null before the first gate cycle.
        "prior_gate_outcome": record.gate_outcome if record is not None else None,
    }


# ---------------------------------------------------------------------------
# source_ingest_submit -- dry-run / apply
# ---------------------------------------------------------------------------


def _submit_payload(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    suggestion_id: str,
    *,
    mode: str,
) -> dict[str, Any]:
    cleaned_topic = _validate_topic(topic)
    cleaned_mode = _validate_mode(mode)
    record = _require_suggestion(store, cleaned_topic, suggestion_id)
    replay = _replayable_verdict(store, vault_path, cleaned_topic, suggestion_id, record)
    if replay is not None and cleaned_mode == "apply":
        return replay
    if cleaned_mode == "dry-run":
        # The preflight runs even when a verdict is replayable. It is free, it is
        # the caller's only readout of candidate health, and short-circuiting it
        # made a stale replay indistinguishable from a fresh dry-run -- the
        # missing `lint_clean`/`source_present`/`would_evaluate` block was the
        # only visible symptom of the cache bug at the time it was reported.
        preflight = _dry_run_payload(store, vault_path, cleaned_topic, suggestion_id, record)
        return preflight if replay is None else {**replay, **preflight, "cached": True}
    return _apply_payload(store, vault_path, cleaned_topic, suggestion_id)


def _replayable_verdict(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    suggestion_id: str,
    record: SuggestionRecord,
) -> dict[str, Any] | None:
    """The stamped verdict, but only while it still answers the same question.

    A ``gate_outcome`` used to be replayed on the strength of the suggestion id
    alone, which is not one of the things a verdict depends on. This compares
    the fingerprint the gate stamped (:mod:`knotica.core.gate_inputs`) against
    the inputs as they stand now, and returns ``None`` -- meaning *evaluate* --
    the moment any of them moved, or whenever the record predates the
    fingerprint and so cannot be shown to still apply.

    **Only a refusal is re-openable.** A ``merged`` verdict is terminal: the
    work is already on the default branch, the suggestion has advanced to
    ``ingested``, and no candidate ref survives to compare against or to
    re-gate. Expiring one would not produce a fresh measurement -- it would
    reach ``open_ingest`` and fail ``SUGGESTION_NOT_APPROVED`` on a suggestion
    whose ingest genuinely finished. A refusal is the opposite in every
    respect: it deliberately leaves the suggestion ``approved``, keeps the work
    on a quarantine ref, and exists precisely to be reworked.
    """
    if record.gate_outcome is None:
        return None
    verdict = record.gate_outcome.get("verdict")
    current = _current_gate_inputs(store, vault_path, topic, suggestion_id)
    if verdict == GATE_VERDICT_REFUSED:
        recorded = gate_inputs.from_record(record.gate_outcome)
        if recorded is None or recorded.diff(current):
            return None
    payload = _verdict_envelope("apply", topic, suggestion_id, record)
    # Named, not merely implied by identical numbers: a replay and a fresh
    # evaluation were previously the same bytes on the wire.
    payload["cached"] = True
    payload["cache_key"] = current.to_dict()
    return payload


def _current_gate_inputs(
    store: VaultStore, vault_path: Path, topic: str, suggestion_id: str
) -> gate_inputs.GateInputs:
    """Fingerprint the gate inputs as a submit right now would find them.

    The candidate is whichever ref currently holds this suggestion's work: the
    private WIP branch while an ingest is open, else the quarantine ref a
    refusal left behind. Resolving the quarantine ref is what lets an untouched
    refused candidate compare equal and replay, while the first rework of it
    does not.
    """
    vcs = VaultVcs(vault_path)
    tree: str | None = None
    for ref in (
        source_ingest.wip_branch_name(topic, suggestion_id),
        quarantine_branch_name(topic, suggestion_id),
    ):
        if not vcs.branch_exists(ref):
            continue
        with best_effort():
            tree = vcs.ref_sha(f"{ref}^{{tree}}")
        break
    state = read_loop_state(store, topic)
    return gate_inputs.capture(
        store,
        topic,
        candidate_tree_sha=tree,
        baseline_scalar=state.baseline_scalar if state is not None else None,
    )


def _dry_run_payload(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    suggestion_id: str,
    record: SuggestionRecord,
) -> dict[str, Any]:
    _require_approved(suggestion_id, record)
    wip_branch = source_ingest.wip_branch_name(topic, suggestion_id)
    vcs = VaultVcs(vault_path)
    if vcs.branch_exists(wip_branch):
        # Resuming an already-open session is side-effect-free (open_ingest
        # only creates a worktree on the *first* call, when the branch does
        # not yet exist) -- safe to call here despite the "no side effects"
        # contract.
        handle = source_ingest.open_ingest(store, vault_path, topic, suggestion_id)
        worktree = source_ingest.worktree_path_for(vault_path, topic, suggestion_id)
        lint_store: VaultStore = LocalFSStore(worktree)
        candidate = handle.candidate
        source_present = handle.resume.source_present
        pages_present = list(handle.resume.pages_present)
    else:
        lint_store = store
        candidate = wip_branch
        # A refused candidate has no WIP branch but is not empty -- its work sits
        # on the quarantine ref that a re-open would resume from. Read it without
        # creating the worktree, so this stays genuinely side-effect-free.
        preview = source_ingest.preview_resume(vault_path, topic, suggestion_id)
        source_present = preview.source_present if preview is not None else False
        pages_present = list(preview.pages_present) if preview is not None else []
    gate_eligible, gate_eligible_reason = _gate_eligibility(store, topic)
    return {
        "mode": "dry-run",
        "topic": topic,
        "suggestion_id": suggestion_id,
        "candidate": candidate,
        # `lint_vault` -- the same checker `lint_check` and `vault_health
        # action=lint` run. This used to call `okf.check.check_vault`, an
        # entirely different rule set (OKF export compatibility), so a candidate
        # could report `lint_clean: true` while carrying violations the vault's
        # own linter would flag, and the two surfaces disagreed by construction.
        "lint_clean": not lint_vault(lint_store, topic),
        "source_present": source_present,
        "pages_present": pages_present,
        "gate_eligible": gate_eligible,
        "gate_eligible_reason": gate_eligible_reason,
        "would_evaluate": source_present and bool(pages_present) and gate_eligible,
    }


def _apply_payload(
    store: VaultStore, vault_path: Path, topic: str, suggestion_id: str
) -> dict[str, Any]:
    handle = source_ingest.open_ingest(store, vault_path, topic, suggestion_id)
    if not (handle.resume.source_present and handle.resume.pages_present):
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"source_ingest_submit failed because no source/pages exist on the "
            f"candidate for suggestion {suggestion_id!r}.",
            fix="Run source_ingest_open then store_source/write_page with "
            "candidate=<handle> first.",
        )
    gate_eligible, gate_eligible_reason = _gate_eligibility(store, topic)
    if not gate_eligible:
        return {
            "mode": "apply",
            "verdict": "blocked",
            "topic": topic,
            "suggestion_id": suggestion_id,
            "candidate": handle.candidate,
            "reason": gate_eligible_reason,
        }
    published = source_ingest.publish_ingest(handle)
    _run_gate(store, vault_path, topic, published)
    record = _require_suggestion(store, topic, suggestion_id)
    if record.gate_outcome is None:
        raise KnoticaError(
            ErrorCode.GIT_ERROR,
            f"source_ingest_submit failed because the gate processed {published!r} "
            "without producing a verdict (most likely a harness evaluation error).",
            fix="Run `knotica doctor` to inspect the loop state and eval logs, "
            "then retry source_ingest_submit(mode=apply).",
        )
    return _verdict_envelope("apply", topic, suggestion_id, record)


def _run_gate(store: VaultStore, vault_path: Path, topic: str, target_branch: str) -> None:
    """Drive the loop's gate synchronously until ``target_branch`` is processed."""
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
    for _ in range(_MAX_GATE_CYCLES):
        result = runner.poll_once()
        if result.branch == target_branch:
            return
        if not result.acted:
            break
    raise KnoticaError(
        ErrorCode.GIT_ERROR,
        f"source_ingest_submit failed because the gate never picked up "
        f"{target_branch!r} within {_MAX_GATE_CYCLES} cycles.",
        fix="Run `knotica doctor` to inspect the loop state, or re-run "
        "source_ingest_submit(mode=apply).",
    )


def _verdict_envelope(
    mode: str, topic: str, suggestion_id: str, record: SuggestionRecord
) -> dict[str, Any]:
    """Translate a stamped ``gate_outcome`` into the D2 wire verdict envelope."""
    outcome = record.gate_outcome or {}
    verdict = outcome.get("verdict")
    payload: dict[str, Any] = {
        "mode": mode,
        "verdict": verdict,
        "topic": topic,
        "suggestion_id": suggestion_id,
        "scalar": outcome.get("scalar"),
        "baseline_scalar": outcome.get("baseline_scalar"),
        "suggestion_status": record.status,
    }
    if verdict == GATE_VERDICT_MERGED:
        payload["merged_ref"] = outcome.get("ref")
        payload["ingested_at"] = record.ingested_at
        payload["committed"] = True
    elif verdict == GATE_VERDICT_REFUSED:
        diff_summary = outcome.get("reason")
        regressed_questions = outcome.get("regressed_questions") or []
        payload["refused_ref"] = outcome.get("ref")
        payload["diff_summary"] = diff_summary
        payload["regressed_questions"] = regressed_questions
        # Decision-envelope (additive): the same data, nested under the
        # uniform ``diff`` shape shared with the other gates' previews.
        payload["diff"] = {
            "diff_summary": diff_summary,
            "regressed_questions": regressed_questions,
        }
    return payload


# ---------------------------------------------------------------------------
# Shared reading + validation helpers
# ---------------------------------------------------------------------------


def _gate_eligibility(store: VaultStore, topic: str) -> tuple[bool, str]:
    """Whether ``topic`` has a frozen gate baseline -- no evaluation run."""
    state = read_loop_state(store, topic)
    if state is None or state.baseline_scalar is None:
        return False, f"no baseline frozen for topic {topic!r}; call loop_set_baseline first."
    return True, ""


def _find_suggestion(store: VaultStore, topic: str, suggestion_id: str) -> SuggestionRecord | None:
    """Look up one suggestion by id, any status (``None`` when absent)."""
    path = suggestions_path(topic)
    if not store.exists(path):
        return None
    records = parse_suggestions_jsonl(store.read_text(path))
    return next((r for r in records if r.suggestion_id == suggestion_id), None)


def _require_suggestion(store: VaultStore, topic: str, suggestion_id: str) -> SuggestionRecord:
    record = _find_suggestion(store, topic, suggestion_id)
    if record is None:
        raise KnoticaError(
            ErrorCode.SUGGESTION_NOT_FOUND,
            f"source_ingest_submit failed because no suggestion {suggestion_id!r} "
            f"exists in topic {topic!r}.",
        )
    return record


def _require_approved(suggestion_id: str, record: SuggestionRecord) -> None:
    if record.status != _APPROVED_STATUS:
        raise KnoticaError(
            ErrorCode.SUGGESTION_NOT_APPROVED,
            f"source_ingest_submit failed because suggestion {suggestion_id!r} is "
            f"{record.status!r}, not {_APPROVED_STATUS!r}.",
        )


def _validate_topic(topic: str) -> str:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    return cleaned


def _validate_mode(mode: str) -> str:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in _MODES:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' to preview or mode='apply' to submit.",
        )
    return cleaned
