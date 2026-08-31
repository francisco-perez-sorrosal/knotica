"""The Fill lane's read-only session-status projection -- the nine-state watch
(``dec-091``).

An approved suggestion's ingest session moves through git-observable state
that only branch existence, branch content, and the suggestion record's own
``status``/``gate_outcome`` actually know about -- the client never reports
in. :func:`session_status` reads that state and classifies it into exactly
one of nine named states, plus who acts next, so the dashboard and the
conversational surface never have to re-derive the distinction themselves.

**Server-derived states only.** This module is the *only* place a session's
state is computed; every consumer (a dashboard rail, a dispatched turn)
renders the ``state``/``next.actor`` this function returns -- it never
infers state from raw branch names or record fields on its own. A client
that re-derived state would risk disagreeing with this module the moment
either side changed, and rail state is exactly the kind of thing the loop's
own client-as-brain invariant does not extend to (the server, not the
client, knows what git actually holds).

**Cost discipline**: at most 2-3 git
subprocesses per call. One ``list_branch_tips`` covers WIP, candidate and
quarantine existence in a single ``for-each-ref`` (rather than one call per
namespace, as :mod:`knotica.core.candidate_gate` does for its own idle-reason
prose); an open WIP session costs one more call to read its committed
content, and only the two states that must distinguish "nothing written yet"
from "stale" (``waiting_on_client``/``swept``) pay a third call to read the
WIP tip's commit timestamp. Every other state resolves in one or two calls.
The signature takes a single ``suggestion_id`` -- never a collection -- so a
batch call is unrepresentable, not merely undocumented.

Priority among the nine states, given a frozen baseline exists (checked
first: ``blocked`` outranks every other row, since its own predicate names
nothing about branches or suggestion status):

1. An open WIP branch -- checked for committed content, then for a
   quarantine sibling (a *reopened* rework session), then for staleness.
2. A published, not-yet-gated candidate branch -- ``submitted``.
3. A stamped gate verdict -- ``merged`` or ``refused``.
4. Otherwise -- ``not_started``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from knotica.core.branch_namespaces import (
    candidate_branch_name,
    quarantine_branch_name,
    wip_branch_name,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gapfill import suggestions_path
from knotica.core.lint import INDEX_PATH
from knotica.core.loop_state import read_loop_state
from knotica.core.page import topic_relative_page_name
from knotica.core.process_model import LANE_STAGE_IDS
from knotica.core.records import SuggestionRecord, parse_suggestions_jsonl
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

__all__ = [
    "SessionNextAction",
    "SessionState",
    "SessionStatus",
    "session_status",
]

SessionState = Literal[
    "not_started",
    "waiting_on_client",
    "client_wrote",
    "rework_in_flight",
    "submitted",
    "merged",
    "refused",
    "blocked",
    "swept",
]

NextActor = Literal["you", "claude", "system", "none"]

#: A WIP session older than this with no content written is read as expired
#: rather than merely quiet -- mirrors :mod:`knotica.core.source_ingest`'s own
#: sweep threshold (a fixed policy value, not shared logic worth importing
#: across a module boundary; see that module's own ``_SOURCES_DIR`` docstring
#: for the same local-redeclaration call).
_STALE_SECONDS = 24 * 60 * 60

#: Mirrors ``operations/store_source.py``'s vault-layout literal (see
#: :mod:`knotica.core.source_ingest`'s identical constant and its docstring
#: for why a small local redeclaration beats reaching into another module's
#: private surface).
_SOURCES_DIR = "sources"

_GATE_VERDICT_MERGED = "merged"
_GATE_VERDICT_REFUSED = "refused"
_INGESTED_STATUS = "ingested"
_APPROVED_STATUS = "approved"


@dataclass(frozen=True, slots=True)
class SessionNextAction:
    """Who acts next, and in one sentence, what -- the anti-dead-end guarantee."""

    actor: NextActor
    do: str


@dataclass(frozen=True, slots=True)
class SessionStatus:
    """The full read contract for one suggestion's ingest session."""

    suggestion_id: str
    stage: str
    stage_index: int
    state: SessionState
    source_present: bool
    pages_present: tuple[str, ...]
    index_synced: bool
    gate_eligible: bool
    gate_eligible_reason: str
    restored_from: str | None
    gate_outcome: dict[str, object] | None
    next: SessionNextAction


def session_status(
    store: VaultStore, root: str | Path, topic: str, suggestion_id: str
) -> SessionStatus:
    """Classify one suggestion's ingest session into one of nine named states.

    Pure read: makes no commit and moves no branch tip (verified by the
    caller's own regression test, since a projection that mutated what it
    reads would be exactly the state-leak the process-lens pre-mortem warns
    against). Raises ``KnoticaError`` (``SUGGESTION_NOT_FOUND``) when
    ``suggestion_id`` names no suggestion in ``topic``.
    """
    record = _require_suggestion(store, topic, suggestion_id)
    gate_eligible, gate_eligible_reason = _gate_eligibility(store, topic)

    if not gate_eligible:
        return _status(
            record,
            "blocked",
            actor="you",
            do=f"Freeze a baseline in improve · instrument first ({gate_eligible_reason}).",
            gate_eligible=False,
            gate_eligible_reason=gate_eligible_reason,
        )

    vcs = VaultVcs(Path(root))
    tips = dict(vcs.list_branch_tips())  # one subprocess; covers wip/candidate/quarantine

    wip = wip_branch_name(topic, suggestion_id)
    wip_sha = tips.get(wip)
    if wip_sha is not None:
        return _open_session_status(
            vcs, topic, record, wip, wip_sha, tips, gate_eligible, gate_eligible_reason
        )

    if candidate_branch_name(topic, suggestion_id) in tips:
        return _status(
            record,
            "submitted",
            actor="system",
            do="The gate is running on the submitted candidate.",
            gate_eligible=gate_eligible,
            gate_eligible_reason=gate_eligible_reason,
        )

    return _gated_or_not_started_status(
        record, topic, suggestion_id, tips, gate_eligible, gate_eligible_reason
    )


def _open_session_status(
    vcs: VaultVcs,
    topic: str,
    record: SuggestionRecord,
    wip: str,
    wip_sha: str,
    tips: dict[str, str],
    gate_eligible: bool,
    gate_eligible_reason: str,
) -> SessionStatus:
    """Classify an open WIP session: reworked, written, stale, or waiting.

    Quarantine existence is read off ``tips`` (the one ``list_branch_tips``
    call the caller already made), never a second ``branch_exists`` -- the
    cost-discipline budget has no room for a redundant existence check the
    caller's own scan already answered.

    A reopened rework session inherits its quarantined predecessor's content
    onto the new WIP branch (``open_ingest`` branches from the quarantine
    tip), so ``source_present``/``pages_present`` are typically already true
    the moment it is reopened -- ``restored_from`` is therefore checked
    *before* the content check, not after: a session the client is actively
    reworking stays ``rework_in_flight`` (dispatch to continue) rather than
    reading as already-ready-to-submit ``client_wrote``.
    """
    quarantine = quarantine_branch_name(topic, record.suggestion_id)
    changed = vcs.changed_paths("HEAD", wip)  # one subprocess
    source_present, pages_present, index_synced = _written_content(topic, changed)

    if quarantine in tips:
        return _status(
            record,
            "rework_in_flight",
            actor="claude",
            do="Continue the dispatch to rework the refused session.",
            source_present=source_present,
            pages_present=pages_present,
            index_synced=index_synced,
            restored_from=quarantine,
            gate_eligible=gate_eligible,
            gate_eligible_reason=gate_eligible_reason,
        )

    if source_present and pages_present:
        return _status(
            record,
            "client_wrote",
            actor="you",
            do="Submit — runs the gate.",
            source_present=source_present,
            pages_present=pages_present,
            index_synced=index_synced,
            gate_eligible=gate_eligible,
            gate_eligible_reason=gate_eligible_reason,
        )

    if _is_stale(vcs, wip_sha):  # one subprocess
        return _status(
            record,
            "swept",
            actor="you",
            do="Session expired after 24h — reopen to restart.",
            source_present=source_present,
            pages_present=pages_present,
            index_synced=index_synced,
            gate_eligible=gate_eligible,
            gate_eligible_reason=gate_eligible_reason,
        )

    return _status(
        record,
        "waiting_on_client",
        actor="claude",
        do="Continue the dispatch to write the source and its pages.",
        source_present=source_present,
        pages_present=pages_present,
        index_synced=index_synced,
        gate_eligible=gate_eligible,
        gate_eligible_reason=gate_eligible_reason,
    )


def _gated_or_not_started_status(
    record: SuggestionRecord,
    topic: str,
    suggestion_id: str,
    tips: dict[str, str],
    gate_eligible: bool,
    gate_eligible_reason: str,
) -> SessionStatus:
    """No open session, no pending candidate: read the stamped gate verdict, if any."""
    outcome = record.gate_outcome
    quarantine_exists = quarantine_branch_name(topic, suggestion_id) in tips
    if outcome is not None:
        verdict = outcome.get("verdict")
        if verdict == _GATE_VERDICT_MERGED and record.status == _INGESTED_STATUS:
            return _status(
                record,
                "merged",
                actor="none",
                do="Closed — merged into the wiki.",
                gate_outcome=_gate_outcome_summary(outcome),
                gate_eligible=gate_eligible,
                gate_eligible_reason=gate_eligible_reason,
            )
        if (
            verdict == _GATE_VERDICT_REFUSED
            and record.status == _APPROVED_STATUS
            and quarantine_exists
        ):
            reason = outcome.get("reason")
            do = "Rework it" + (f": {reason}" if reason else " — the gate found regressions.")
            return _status(
                record,
                "refused",
                actor="you",
                do=do,
                gate_outcome=_gate_outcome_summary(outcome),
                gate_eligible=gate_eligible,
                gate_eligible_reason=gate_eligible_reason,
            )

    return _status(
        record,
        "not_started",
        actor="you",
        do="Open a session to start ingesting this source.",
        gate_eligible=gate_eligible,
        gate_eligible_reason=gate_eligible_reason,
    )


def _is_stale(vcs: VaultVcs, sha: str) -> bool:
    return (time.time() - vcs.commit_timestamp(sha)) > _STALE_SECONDS


def _written_content(topic: str, changed: list[str]) -> tuple[bool, tuple[str, ...], bool]:
    """Mirrors :class:`knotica.core.source_ingest.ResumeState`'s own derivation
    from a ``changed_paths`` diff -- re-derived here rather than imported
    across the module boundary (see the ``_SOURCES_DIR`` docstring above)."""
    source_prefix = f"{_SOURCES_DIR}/{topic}/"
    page_prefix = f"{topic}/"
    pages = sorted(
        topic_relative_page_name(topic, path)
        for path in changed
        if path.startswith(page_prefix) and path.endswith(".md")
    )
    source_present = any(path.startswith(source_prefix) for path in changed)
    index_synced = INDEX_PATH in changed
    return source_present, tuple(pages), index_synced


def _gate_outcome_summary(outcome: dict[str, object]) -> dict[str, object]:
    """The four wire-contract fields, dropped from the record's full opaque blob."""
    return {
        "verdict": outcome.get("verdict"),
        "scalar": outcome.get("scalar"),
        "baseline_scalar": outcome.get("baseline_scalar"),
        "reason": outcome.get("reason"),
    }


def _gate_eligibility(store: VaultStore, topic: str) -> tuple[bool, str]:
    """Whether ``topic`` has a frozen gate baseline -- no evaluation run.

    Mirrors ``mcp_server/tools_source_ingest.py``'s own ``_gate_eligibility``:
    a small, adapter-facing check re-derived here rather than imported, since
    ``core/`` must not depend on the ``mcp_server`` adapter layer.
    """
    state = read_loop_state(store, topic)
    if state is None or state.baseline_scalar is None:
        return False, f"no baseline frozen for topic {topic!r}"
    return True, ""


def _require_suggestion(store: VaultStore, topic: str, suggestion_id: str) -> SuggestionRecord:
    path = suggestions_path(topic)
    records = parse_suggestions_jsonl(store.read_text(path)) if store.exists(path) else []
    record = next((r for r in records if r.suggestion_id == suggestion_id), None)
    if record is None:
        raise KnoticaError(
            ErrorCode.SUGGESTION_NOT_FOUND,
            f"session_status failed because no suggestion {suggestion_id!r} exists in "
            f"topic {topic!r}.",
        )
    return record


def _status(
    record: SuggestionRecord,
    state: SessionState,
    *,
    actor: NextActor,
    do: str,
    source_present: bool = False,
    pages_present: tuple[str, ...] = (),
    index_synced: bool = False,
    restored_from: str | None = None,
    gate_outcome: dict[str, object] | None = None,
    gate_eligible: bool,
    gate_eligible_reason: str,
) -> SessionStatus:
    stage = "ingest"
    return SessionStatus(
        suggestion_id=record.suggestion_id,
        stage=stage,
        stage_index=LANE_STAGE_IDS["fill"].index(stage),
        state=state,
        source_present=source_present,
        pages_present=pages_present,
        index_synced=index_synced,
        gate_eligible=gate_eligible,
        gate_eligible_reason=gate_eligible_reason,
        restored_from=restored_from,
        gate_outcome=gate_outcome,
        next=SessionNextAction(actor=actor, do=do),
    )
