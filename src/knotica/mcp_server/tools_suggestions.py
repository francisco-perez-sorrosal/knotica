"""Gap-fill queue tools -- ``suggestions_read`` + ``suggestions_review`` + ``gap_report``.

Three deterministic MCP tools over the per-topic gap/suggestion queues: one
**read** tool that pages the human-approval suggestions
(``<topic>/.knotica/suggestions/suggestions.jsonl``) for the dashboard/interactive
client; one **action-parameterized mutating** tool that flips one suggestion's
lifecycle status after a human decision; and one **report** tool that lets the
client-as-brain file a conversationally exposed knowledge gap into the P1 gap
queue (``<topic>/.knotica/gaps/gaps.jsonl``), from which the existing drain
surfaces it. All three are stateless, topic-explicit, and honor the
``NOT_CONFIGURED`` contract via :func:`with_resolved_vault`.

This module is on the MCP cold-start import path, so it imports **nothing** from
``discovery/`` -- reads parse ``suggestions.jsonl`` line-by-line (tolerating and
counting malformed lines) and writes delegate to
:func:`knotica.core.gapfill.apply_decision` /
:func:`knotica.core.gapfill.report_gap`, none of which pull the heavy
search chain. The read tool re-uses the opaque search
cursor (base64 offset token) so a stale/malformed cursor fails closed as
``INVALID_CURSOR`` exactly like the search surface (dec-002).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gapfill import apply_decision, plan_decision, report_gap, suggestions_path
from knotica.core.page import TopicNotFoundError
from knotica.core.records import RecordParseError, SuggestionRecord
from knotica.mcp_server import envelope
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.search.cursor import Cursor, InvalidCursorError, decode_cursor, encode_cursor
from knotica.store import VaultStore

__all__ = ["register_suggestions_tools"]

ToolResult = CallToolResult

#: The five lifecycle statuses, in a fixed order so ``status_counts`` is stable.
_STATUS_VALUES: tuple[str, ...] = ("pending", "approved", "rejected", "deferred", "ingested")
#: The synthetic filter value returning every non-terminal-hidden record.
_ALL_FILTER = "all"
#: Statuses visible under ``status="all"`` -- terminal rejected/ingested are hidden.
_ALL_VISIBLE: frozenset[str] = frozenset({"pending", "approved", "deferred"})
#: Recognized ``status`` argument values (the five statuses plus the ``all`` view).
_STATUS_FILTERS: frozenset[str] = frozenset(_STATUS_VALUES) | {_ALL_FILTER}

#: The four decisions a review may apply, listed for the bad-action error text.
_ACTIONS: tuple[str, ...] = ("approve", "reject", "defer", "mark_ingested")
_MODES: frozenset[str] = frozenset({"dry-run", "apply"})

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50

#: The suggestions cursor's sort contract: newest gap first, best candidate first
#: within a gap. A token minted under any other sort id is stale (dec-002).
_SUGGESTIONS_SORT = "generation-desc,rank-asc"

_READ_DESCRIPTION = (
    "List gap-fill suggestions for one topic: human-approval cards joining a "
    "diagnosed genuine_gap to a ranked, reputability-scored source. Filter by "
    "status (pending|approved|rejected|deferred|ingested, or 'all' for the "
    "non-terminal pending+approved+deferred view; default pending). Sorted "
    "newest-gap-first. Paginate with the opaque cursor from a prior next_cursor "
    "(default 20, max 50 per page). Returns status_counts (full breakdown) for "
    "the dashboard badge. Read-only -- no commits, no lock."
)

_REVIEW_DESCRIPTION = (
    "Record a human decision on one gap-fill suggestion. action=approve queues "
    "the source for the next interactive ingest; action=reject discards it (a "
    "non-empty reason is required); action=defer hides it as 'not now' "
    "(reversible); action=mark_ingested closes an approved suggestion after "
    "ingest. mode=dry-run previews the transition without writing; mode=apply "
    "performs exactly one commit. Only pending or deferred suggestions can be "
    "approved/rejected; only pending can be deferred; only approved can be "
    "marked ingested."
)


_REPORT_DESCRIPTION = (
    "File a knowledge gap the wiki just failed to answer. Call this ONLY when both "
    "hold: (1) you queried this topic's wiki for the user and the answer was wrong, "
    "missing, or too thin to be useful, AND (2) the user confirms it is a real gap "
    "worth researching. Pass the user's actual failed question verbatim as "
    "'question' (never paraphrase, summarize, or invent one); add a short 'reason' "
    "for why the wiki fell short and any 'reference_pages' the answer should have "
    "cited. The gap enters the same human-approval discovery queue as eval-detected "
    "gaps, tagged origin=reported. Do NOT file speculatively, in bulk, or to seed "
    "topics -- one confirmed conversational miss at a time. Repeat reports of the "
    "same question are automatically deduplicated. One commit; requires a lock."
)

_REPORT_MAX_REFERENCE_PAGES = 20


def register_suggestions_tools(mcp: FastMCP) -> None:
    """Register ``suggestions_read``, ``suggestions_review``, and ``gap_report`` on ``mcp``."""

    @mcp.tool(name="suggestions_read", description=_READ_DESCRIPTION)
    def suggestions_read(
        topic: str,
        status: str = "pending",
        cursor: str = "",
        limit: int = _DEFAULT_LIMIT,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: envelope.read_ok(
                _read_payload(store, topic, status=status, cursor=cursor, limit=limit)
            ),
        )

    @mcp.tool(name="suggestions_review", description=_REVIEW_DESCRIPTION)
    def suggestions_review(
        topic: str,
        suggestion_id: str,
        action: str,
        mode: str = "dry-run",
        reason: str = "",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _review_payload(
                store,
                resolved.path,
                topic,
                suggestion_id,
                action=action,
                mode=mode,
                reason=reason,
            ),
        )

    @mcp.tool(name="gap_report", description=_REPORT_DESCRIPTION)
    def gap_report(
        topic: str,
        question: str,
        reason: str = "",
        reference_pages: list[str] | None = None,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _report_payload(
                store,
                resolved.path,
                topic,
                question,
                reason=reason,
                reference_pages=reference_pages,
            ),
        )


# ---------------------------------------------------------------------------
# suggestions_read -- filter, sort, paginate, count
# ---------------------------------------------------------------------------


def _read_payload(
    store: VaultStore, topic: str, *, status: str, cursor: str, limit: int
) -> dict[str, Any]:
    """Build the paginated read envelope for one status filter."""
    cleaned_topic = _validate_topic(topic)
    status_filter = _validate_status_filter(status)
    page_size = _validate_limit(limit)
    records, skipped = _read_records(store, cleaned_topic)

    counts = _status_counts(records)
    matching = _sorted(_filter_by_status(records, status_filter))
    offset = _resolve_offset(cursor, status_filter)
    page = matching[offset : offset + page_size]
    has_more = offset + page_size < len(matching)
    next_cursor = (
        encode_cursor(
            Cursor(query=status_filter, sort=_SUGGESTIONS_SORT, offset=offset + page_size)
        )
        if has_more
        else ""
    )
    return {
        "topic": cleaned_topic,
        "status_filter": status_filter,
        "suggestions": [_record_dict(record) for record in page],
        "status_counts": counts,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total_count": len(matching),
        "skipped_malformed": skipped,
    }


def _filter_by_status(
    records: list[SuggestionRecord], status_filter: str
) -> list[SuggestionRecord]:
    """The records matching a filter: one status, or the ``all`` non-terminal view."""
    if status_filter == _ALL_FILTER:
        return [record for record in records if record.status in _ALL_VISIBLE]
    return [record for record in records if record.status == status_filter]


def _sorted(records: list[SuggestionRecord]) -> list[SuggestionRecord]:
    """Deterministic order: newest proposal first, best rank first, id as final tiebreak.

    Keys on ``proposed_at`` -- a real timestamp present on every suggestion
    regardless of origin -- rather than ``detected_generation``, which reads a
    constant zero for ``reported``/``retracted`` suggestions (no eval
    generation backs them) and buried them at the bottom of every page.
    """
    by_tiebreak = sorted(records, key=lambda record: (record.rank, record.suggestion_id))
    return sorted(by_tiebreak, key=lambda record: record.proposed_at, reverse=True)


def _status_counts(records: list[SuggestionRecord]) -> dict[str, int]:
    """The full per-status breakdown (every status present, zero when absent)."""
    counter = Counter(record.status for record in records)
    return {status: counter.get(status, 0) for status in _STATUS_VALUES}


def _resolve_offset(cursor: str, status_filter: str) -> int:
    """Decode an opaque page cursor, failing closed on a stale/malformed token."""
    if not cursor:
        return 0
    decoded = decode_cursor(cursor)
    if decoded.sort != _SUGGESTIONS_SORT:
        raise InvalidCursorError(
            f"Cursor was minted under sort {decoded.sort!r}, "
            f"but the current sort contract is {_SUGGESTIONS_SORT!r}."
        )
    if decoded.query != status_filter:
        raise InvalidCursorError(
            "Cursor was minted for a different status filter and cannot continue this read."
        )
    return decoded.offset


# ---------------------------------------------------------------------------
# suggestions_review -- validate, preview (dry-run) or commit (apply)
# ---------------------------------------------------------------------------


def _review_payload(
    store: VaultStore,
    root: str | Path,
    topic: str,
    suggestion_id: str,
    *,
    action: str,
    mode: str,
    reason: str,
) -> dict[str, Any]:
    """Validate the request, then preview (dry-run) or commit (apply) one decision."""
    cleaned_topic = _validate_topic(topic)
    cleaned_mode = _validate_mode(mode)
    cleaned_action = _validate_action(action)
    if cleaned_mode == "dry-run":
        return _dry_run(store, cleaned_topic, suggestion_id, action=cleaned_action, reason=reason)
    return _apply(store, root, cleaned_topic, suggestion_id, action=cleaned_action, reason=reason)


def _dry_run(
    store: VaultStore, topic: str, suggestion_id: str, *, action: str, reason: str
) -> dict[str, Any]:
    """Preview the transition without writing (reuses the pure lifecycle validator)."""
    record = _require_record(store, topic, suggestion_id)
    plan = plan_decision(record, decision=action, reason=reason or None)
    return {
        "mode": "dry-run",
        "topic": topic,
        "suggestion_id": suggestion_id,
        "action": action,
        "from_status": plan.from_status,
        "to_status": plan.to_status,
        "would_commit": True,
        "reason_required": action == "reject",
        "candidate_title": _candidate_title(record),
        "preview": _preview_text(action),
    }


def _apply(
    store: VaultStore,
    root: str | Path,
    topic: str,
    suggestion_id: str,
    *,
    action: str,
    reason: str,
) -> dict[str, Any]:
    """Commit one decision in a single transaction; map a missing id to a typed error."""
    _require_record(store, topic, suggestion_id)  # SUGGESTION_NOT_FOUND before the write attempt
    result = apply_decision(
        store, root, topic, suggestion_id, decision=action, reason=reason or None
    )
    return {
        "mode": "apply",
        "topic": topic,
        "suggestion_id": suggestion_id,
        "action": action,
        "from_status": result.from_status,
        "to_status": result.to_status,
        "committed": True,
        "commit": result.commit_sha,
        "decided_at": result.decided_at,
        "ingested_at": result.ingested_at,
    }


def _require_record(store: VaultStore, topic: str, suggestion_id: str) -> SuggestionRecord:
    """Find one suggestion by id, or raise the typed ``SUGGESTION_NOT_FOUND`` error."""
    records, _skipped = _read_records(store, topic)
    for record in records:
        if record.suggestion_id == suggestion_id:
            return record
    raise KnoticaError(
        ErrorCode.SUGGESTION_NOT_FOUND,
        f"no suggestion {suggestion_id!r} in topic {topic!r}",
    )


def _preview_text(action: str) -> str:
    """The one-line human preview of what an applied decision would do."""
    previews = {
        "approve": "Approve -> queues 1 ingest instruction for the next interactive session.",
        "reject": "Reject -> discards this source from the queue (reason recorded).",
        "defer": "Defer -> hides this suggestion as 'not now' (reversible).",
        "mark_ingested": "Mark ingested -> closes this approved suggestion after ingest.",
    }
    return previews[action]


# ---------------------------------------------------------------------------
# gap_report -- file one conversationally reported gap
# ---------------------------------------------------------------------------


def _report_payload(
    store: VaultStore,
    root: str | Path,
    topic: str,
    question: str,
    *,
    reason: str,
    reference_pages: list[str] | None,
) -> dict[str, Any]:
    """Validate the request and file one reported gap in a single commit."""
    cleaned_topic = _validate_topic(topic)
    pages = _validate_reference_pages(reference_pages)
    result = report_gap(
        store,
        root,
        cleaned_topic,
        question,
        reason=reason or None,
        reference_pages=pages,
    )
    return {
        "topic": result.topic,
        "gap_id": result.gap_id,
        "qa_id": result.qa_id,
        "question": result.question,
        "fault_class": result.fault_class,
        "status": result.status,
        "origin": result.origin,
        "reason": result.reason,
        "reference_pages": list(result.reference_pages),
        "written": result.written,
        "duplicate": not result.written,
    }


def _validate_reference_pages(reference_pages: list[str] | None) -> tuple[str, ...]:
    """Coerce the optional reference-pages argument to a bounded tuple of strings."""
    if reference_pages is None:
        return ()
    if not isinstance(reference_pages, list) or any(
        not isinstance(page, str) for page in reference_pages
    ):
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"reference_pages must be a list of strings, got {reference_pages!r}",
            fix="Pass reference_pages as a JSON array of page-name strings, or omit it.",
        )
    if len(reference_pages) > _REPORT_MAX_REFERENCE_PAGES:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"reference_pages may name at most {_REPORT_MAX_REFERENCE_PAGES} pages, "
            f"got {len(reference_pages)}",
            fix=f"Pass at most {_REPORT_MAX_REFERENCE_PAGES} reference pages.",
        )
    return tuple(reference_pages)


# ---------------------------------------------------------------------------
# Shared reading + validation helpers
# ---------------------------------------------------------------------------


def _read_records(store: VaultStore, topic: str) -> tuple[list[SuggestionRecord], int]:
    """Parse the queue tolerantly: valid records plus a malformed-line count.

    Unlike :func:`parse_suggestions_jsonl` (which raises on the first bad line),
    the read surface honesty-counts malformed lines so a single corrupt record
    never hides the rest of the queue (mirrors ``metrics_read``).
    """
    path = suggestions_path(topic)
    if not store.exists(path):
        return [], 0
    records: list[SuggestionRecord] = []
    skipped = 0
    for line in store.read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            records.append(SuggestionRecord.from_json_line(line))
        except (RecordParseError, ValueError):
            skipped += 1
    return records, skipped


def _record_dict(record: SuggestionRecord) -> dict[str, Any]:
    """Render one record as its wire dict (candidate denormalized), via the JSON line."""
    return json.loads(record.to_json_line())


def _candidate_title(record: SuggestionRecord) -> str:
    title = record.candidate.get("title")
    return title if isinstance(title, str) else ""


def _validate_topic(topic: str) -> str:
    """Normalize a topic to a single path segment or raise ``TOPIC_NOT_FOUND``."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    return cleaned


def _validate_status_filter(status: str) -> str:
    cleaned = status.strip().lower()
    if cleaned not in _STATUS_FILTERS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"status must be one of {'|'.join(_STATUS_VALUES)}|{_ALL_FILTER}, got {status!r}",
            fix=f"Pass status as one of: {', '.join(sorted(_STATUS_FILTERS))}.",
        )
    return cleaned


def _validate_limit(limit: int) -> int:
    if limit < 1 or limit > _MAX_LIMIT:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"limit must be in 1..{_MAX_LIMIT}, got {limit}",
            fix=f"Pass limit between 1 and {_MAX_LIMIT}.",
        )
    return limit


def _validate_mode(mode: str) -> str:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in _MODES:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"mode must be 'dry-run' or 'apply', got {mode!r}",
            fix="Pass mode='dry-run' to preview or mode='apply' to record the decision.",
        )
    return cleaned


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned
