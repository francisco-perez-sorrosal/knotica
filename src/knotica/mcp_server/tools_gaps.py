"""Gap-queue MCP tools -- ``gap_report`` + ``gaps_read`` + ``gapfill_discover``.

One module per queue. ``tools_suggestions`` fronts ``suggestions.jsonl``, the
human-approval queue that exists only once discovery has found candidate
sources; this one fronts ``gaps.jsonl``, the P1 queue upstream of it, plus the
drain that moves records between the two.

Three tools: a **write** (``gap_report``) letting the client-as-brain file a
conversationally exposed knowledge gap; a **read** (``gaps_read``) over that
queue; and a **billed two-phase drain** (``gapfill_discover``) that runs source
discovery and stages the results as suggestions.

``gaps_read`` and ``gapfill_discover`` exist because the queue was write-only.
``gap_report`` wrote gaps that no tool could read back and no dashboard pane
touched, and discovery -- the one step that turns a gap into something
``suggestions_read`` can show -- was CLI-only. So a gap could be filed in
conversation, be invisible everywhere, and be un-actionable without dropping to
a terminal.

This module is on the MCP cold-start import path and imports **nothing** from
``discovery/`` at module level. ``core.gapfill.build_default_discovery_service``
defers the whole search chain into its own body, which is what keeps the drain
reachable from here without dragging httpx onto server startup
(``tests/test_discovery_import_boundary.py`` pins it).

The small ``_validate_topic`` / ``_validate_limit`` helpers are duplicated from
``tools_suggestions`` rather than shared: five tool modules already carry their
own copy, and a cross-module import of another tool module's privates is the
worse trade.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gap_classifier import gaps_path
from knotica.core.gapfill import (
    apply_gap_decision,
    build_default_discovery_service,
    refresh_suggestions_for_gaps,
    report_gap,
)
from knotica.core.page import TopicNotFoundError
from knotica.core.records import GAP_ORIGINS, GapRecord, RecordParseError
from knotica.mcp_server import confirm_nonce, dispatch_telemetry, envelope
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.search.cursor import Cursor, InvalidCursorError, decode_cursor, encode_cursor
from knotica.store import VaultStore

__all__ = ["register_gaps_lane_tools", "register_gaps_tools"]

ToolResult = CallToolResult

#: The synthetic filter value returning every record regardless of status.
_ALL_FILTER = "all"

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50

#: The three gap lifecycle statuses, fixed order so ``status_counts`` is stable.
#: P1 writes ``open``; P3/P4 flip a gap terminal.
_GAP_STATUS_VALUES: tuple[str, ...] = ("open", "resolved", "dismissed")
#: Recognized ``gaps_read`` status values (the three statuses plus the ``all`` view).
_GAP_STATUS_FILTERS: frozenset[str] = frozenset(_GAP_STATUS_VALUES) | {_ALL_FILTER}
#: Origin breakdown order. Derived from the vocabulary rather than restated, so a
#: new origin cannot be added to ``records`` and silently missing from the counts;
#: sorted() makes the wire order deterministic (measured, reported, retracted).
_GAP_ORIGIN_VALUES: tuple[str, ...] = tuple(sorted(GAP_ORIGINS))
#: The gaps cursor's sort contract: newest filed first. Distinct from the
#: suggestions sort so a cursor cannot be replayed across the two queues.
_GAPS_SORT = "detected-at-desc"

#: Nonce ``kind`` for the billed discovery drain. Per-action by construction, so
#: a ``run-eval`` or ``run-once`` nonce can never confirm a drain.
_DISCOVER_NONCE_KIND = "gapfill-discover"

_REPORT_MAX_REFERENCE_PAGES = 20

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

_GAPS_READ_DESCRIPTION = (
    "List diagnosed knowledge gaps for one topic -- the P1 queue that feeds source "
    "discovery, before any candidate sources exist for them. A gap appears here the "
    "moment it is filed (by the eval loop's regression classifier, by `gap_report`, "
    "or by the guillotine) and stays until a discovery drain promotes it into "
    "`fill action=suggestions_read` cards. Filter by status (open|resolved|dismissed, or 'all'; "
    "default open). Each gap carries its origin (measured|reported|retracted), "
    "fault_class (genuine_gap|dilution), the failed question, and reference_pages. "
    "Sorted newest-first. Paginate with the opaque cursor from a prior next_cursor "
    "(default 20, max 50 per page). Read-only -- no commits, no lock. Use this to "
    "answer 'what gaps are open on this topic' and to show a filed gap has landed; "
    "use `fill action=suggestions_read` for gaps that already have sources to approve."
)

_REVIEW_GAP_DESCRIPTION = (
    "Dismiss a diagnosed gap that is not worth sourcing, or reopen one you "
    "dismissed. decision='dismiss' requires a non-empty 'reason' and is legal "
    "ONLY from an open gap; decision='reopen' is legal only from a dismissed "
    "gap and 'reason' is optional. A resolved gap -- already answered by a "
    "merged source -- accepts neither: undoing a merge is a vault operation, "
    "not a queue edit; a source status refuses with an INVALID_ARGUMENT error. "
    "The reason is persisted on the gap record and survives a re-read. One "
    "commit; requires a lock."
)

_DISCOVER_DESCRIPTION = (
    "Run source discovery for a topic's open gaps: formulate one query per gap, "
    "call the configured search provider plus OpenAlex enrichment, and stage the "
    "ranked candidates as pending suggestions for review. This is the step that "
    "turns a gap into something `fill action=suggestions_read` can show. "
    "BILLED and two-phase: a bare call previews (how many gaps would drain, "
    "whether a provider is configured, the cost) and returns a short-lived "
    "confirm_nonce WITHOUT spending anything; only a second call passing that "
    "nonce as 'confirm' makes the search calls. Never pass confirm on the user's "
    "behalf -- the preview exists for them to see and approve first. "
    "max_gaps caps the drain to the N highest-priority open gaps (0 = all); pass "
    "1 to drain a single gap. With no provider key configured this is a clean "
    "no-op that stages nothing and reports provider_configured=false."
)


def register_gaps_tools(mcp: FastMCP) -> None:
    """Register ``gap_report`` on ``mcp``."""

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


def register_gaps_lane_tools(mcp: FastMCP) -> None:
    """Register ``gaps_read`` and ``gapfill_discover``, reachable only through a lane.

    Split from :func:`register_gaps_tools` because the published surface no
    longer carries them: ``fill action=gaps_read`` and
    ``fill action=gapfill_discover`` are the ways in. The registrations still
    exist because that is the seam the lane dispatchers collect their handlers
    through -- a lane routes to *these* function objects, not to copies of
    them. See ``tools_dispatch_lane_common.py``.
    """

    @mcp.tool(name="gaps_read", description=_GAPS_READ_DESCRIPTION)
    def gaps_read(
        topic: str,
        status: str = "open",
        cursor: str = "",
        limit: int = _DEFAULT_LIMIT,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, _resolved: envelope.read_ok(
                _gaps_read_payload(store, topic, status=status, cursor=cursor, limit=limit)
            ),
        )

    @mcp.tool(name="gapfill_discover", description=_DISCOVER_DESCRIPTION)
    def gapfill_discover(
        topic: str,
        max_gaps: int = 0,
        confirm: str = "",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _discover_payload(
                store, resolved.path, topic, max_gaps=max_gaps, confirm=confirm
            ),
        )

    @mcp.tool(name="review_gap", description=_REVIEW_GAP_DESCRIPTION)
    def review_gap(
        topic: str,
        gap_id: str,
        decision: str,
        reason: str = "",
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _review_gap_payload(
                store, resolved.path, topic, gap_id, decision=decision, reason=reason
            ),
        )


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
# gaps_read -- the P1 queue, before discovery promotes anything out of it
# ---------------------------------------------------------------------------


def _gaps_read_payload(
    store: VaultStore, topic: str, *, status: str, cursor: str, limit: int
) -> dict[str, Any]:
    """Build the paginated read envelope over one topic's gap queue."""
    cleaned_topic = _validate_topic(topic)
    status_filter = _validate_gap_status_filter(status)
    page_size = _validate_limit(limit)
    records, skipped = _read_gap_records(store, cleaned_topic)

    matching = _sorted_gaps(_filter_gaps_by_status(records, status_filter))
    offset = _resolve_gap_offset(cursor, status_filter)
    page = matching[offset : offset + page_size]
    has_more = offset + page_size < len(matching)
    next_cursor = (
        encode_cursor(Cursor(query=status_filter, sort=_GAPS_SORT, offset=offset + page_size))
        if has_more
        else ""
    )
    return {
        "topic": cleaned_topic,
        "status_filter": status_filter,
        "gaps": [_gap_record_dict(record) for record in page],
        "status_counts": _gap_status_counts(records),
        "origin_counts": _gap_origin_counts(records),
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total_count": len(matching),
        "skipped_malformed": skipped,
    }


def _read_gap_records(store: VaultStore, topic: str) -> tuple[list[GapRecord], int]:
    """Parse the gap queue tolerantly: valid records plus a malformed-line count.

    Mirrors :func:`_read_records`, and for the same reason: this is a display
    surface, so one corrupt line must not hide the rest of the queue. That rules
    out ``parse_gaps_jsonl`` (raises on the first bad line) and
    ``gapfill._open_genuine_gaps`` (raises, and drops ``dilution`` gaps -- correct
    for a drain deciding what to query for, wrong for a reader answering "what is
    on this queue").
    """
    path = gaps_path(topic)
    if not store.exists(path):
        return [], 0
    records: list[GapRecord] = []
    skipped = 0
    for line in store.read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            records.append(GapRecord.from_json_line(line))
        except (RecordParseError, ValueError):
            skipped += 1
    return records, skipped


def _filter_gaps_by_status(records: list[GapRecord], status_filter: str) -> list[GapRecord]:
    """The records matching a filter, where ``all`` means literally all three.

    Deliberately unlike :func:`_filter_by_status`, where ``all`` is a
    *non-terminal* view that hides rejected/ingested. A gap's terminal statuses
    are ``resolved`` and ``dismissed``, so carrying that convention over would
    leave ``all`` showing only ``open`` -- a synonym for the default, and a filter
    that silently answers a different question than the one asked.
    """
    if status_filter == _ALL_FILTER:
        return list(records)
    return [record for record in records if record.status == status_filter]


def _sorted_gaps(records: list[GapRecord]) -> list[GapRecord]:
    """Deterministic order: newest filed first, gap id as the tiebreak.

    Keys on ``detected_at`` -- a real timestamp on every gap regardless of origin
    -- rather than ``detected_generation``, which is a constant zero for
    ``reported``/``retracted`` gaps (no eval generation backs them) and would sink
    exactly the hand-filed gaps a reader is most likely looking for.
    """
    by_tiebreak = sorted(records, key=lambda record: record.gap_id)
    return sorted(by_tiebreak, key=lambda record: record.detected_at, reverse=True)


def _gap_status_counts(records: list[GapRecord]) -> dict[str, int]:
    """The full per-status breakdown (every status present, zero when absent)."""
    counter = Counter(record.status for record in records)
    return {status: counter.get(status, 0) for status in _GAP_STATUS_VALUES}


def _gap_origin_counts(records: list[GapRecord]) -> dict[str, int]:
    """The per-origin breakdown, so a surface can badge measured vs reported."""
    counter = Counter(record.origin for record in records)
    return {origin: counter.get(origin, 0) for origin in _GAP_ORIGIN_VALUES}


def _gap_record_dict(record: GapRecord) -> dict[str, Any]:
    """Render one gap as its wire dict, via the JSON line (single serialization site)."""
    return cast("dict[str, Any]", json.loads(record.to_json_line()))


def _validate_gap_status_filter(status: str) -> str:
    cleaned = status.strip().lower()
    if cleaned not in _GAP_STATUS_FILTERS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"status must be one of {'|'.join(_GAP_STATUS_VALUES)}|{_ALL_FILTER}, got {status!r}",
            fix=f"Pass status as one of: {', '.join(sorted(_GAP_STATUS_FILTERS))}.",
        )
    return cleaned


def _resolve_gap_offset(cursor: str, status_filter: str) -> int:
    """Decode an opaque page cursor, failing closed on a stale/malformed token."""
    if not cursor:
        return 0
    decoded = decode_cursor(cursor)
    if decoded.sort != _GAPS_SORT:
        raise InvalidCursorError(
            f"Cursor was minted under sort {decoded.sort!r}, "
            f"but the current sort contract is {_GAPS_SORT!r}."
        )
    if decoded.query != status_filter:
        raise InvalidCursorError(
            "Cursor was minted for a different status filter and cannot continue this read."
        )
    return decoded.offset


# ---------------------------------------------------------------------------
# review_gap -- the human dismiss/reopen transition over the gap queue
# ---------------------------------------------------------------------------


def _review_gap_payload(
    store: VaultStore,
    root: str | Path,
    topic: str,
    gap_id: str,
    *,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """Validate the request and apply one human dismiss/reopen transition.

    ``apply_gap_decision`` raises a bare ``ValueError`` for an unknown ``gap_id``
    (it has no MCP-facing caller of its own to map that error) -- caught here and
    re-raised as a typed ``INVALID_ARGUMENT`` so this tool never lets an
    unmapped exception escape the envelope boundary, mirroring
    ``tools_suggestions.py``'s ``_require_record`` guard for the sibling
    suggestion transition.
    """
    cleaned_topic = _validate_topic(topic)
    try:
        result = apply_gap_decision(
            store,
            root,
            cleaned_topic,
            gap_id,
            decision=decision,
            reason=reason or None,
        )
    except ValueError as error:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            str(error),
            fix="Call `fill action=gaps_read` to list current gap ids.",
        ) from error
    return {
        "gap_id": result.gap_id,
        "topic": result.topic,
        "decision": result.decision,
        "from_status": result.from_status,
        "to_status": result.to_status,
        "reason": result.reason,
        "decided_at": result.decided_at,
        "question": result.question,
        "changed": result.changed,
        "commit_sha": result.commit_sha,
    }


# ---------------------------------------------------------------------------
# gapfill_discover -- the billed hop from the gap queue to the suggestion queue
# ---------------------------------------------------------------------------


def _discover_payload(
    store: VaultStore, vault_path: Path, topic: str, *, max_gaps: int, confirm: str
) -> dict[str, Any]:
    """Two-phase decision envelope for the billed discovery drain.

    Same nonce protocol as ``loop action=run_eval`` / ``action=run_once``, keyed
    under its own ``kind`` so one action's nonce can never confirm another's.
    Phase 1 (no ``confirm``, or a stale/mismatched/expired nonce) mints a preview
    and returns -- it never calls ``service.discover``, so it never bills. Phase 2
    (a ``confirm`` matching an unexpired, unconsumed nonce) consumes it and runs
    the drain.

    The drain itself was CLI-only until now. That left the whole P1->P3 hop
    unreachable from the two surfaces a gap is actually filed and read on, so a
    gap could be seen and never acted on without dropping to a terminal.
    """
    cleaned_topic = _validate_topic(topic)
    cap = _validate_max_gaps(max_gaps)

    if confirm.strip():
        consumed = confirm_nonce.consume(
            vault_path, _DISCOVER_NONCE_KIND, cleaned_topic, confirm.strip()
        )
        if consumed is not None:
            dispatch_telemetry.record_two_phase(
                "gapfill_discover",
                "discover",
                cleaned_topic,
                outcome=dispatch_telemetry.OUTCOME_CONFIRMED,
            )
            return _execute_discover(store, vault_path, cleaned_topic, cap)
        dispatch_telemetry.record_two_phase(
            "gapfill_discover",
            "discover",
            cleaned_topic,
            outcome=dispatch_telemetry.OUTCOME_STALE_CONFIRM,
        )

    payload = _discover_preview(store, vault_path, cleaned_topic, cap)
    dispatch_telemetry.record_two_phase(
        "gapfill_discover",
        "discover",
        cleaned_topic,
        outcome=dispatch_telemetry.OUTCOME_PREVIEW,
    )
    return envelope.read_ok(payload)


def _discover_preview(
    store: VaultStore, vault_path: Path, topic: str, cap: int | None
) -> dict[str, Any]:
    """Phase 1: what a drain would do, and what it would cost. Bills nothing.

    Builds the discovery service to answer ``provider_configured`` honestly.
    That constructs adapters and imports ``discovery/``; it issues no request, so
    the preview stays free. Reporting the flag without checking would be the one
    number in this envelope a caller cannot verify for themselves.
    """
    drainable = _drainable_gap_count(store, topic)
    would_drain = drainable if cap is None else min(cap, drainable)
    provider_configured = build_default_discovery_service() is not None
    return {
        "action": "gapfill_discover",
        "topic": topic,
        "open_gaps": drainable,
        "would_drain": would_drain,
        "max_gaps": cap,
        "provider_configured": provider_configured,
        "estimated_cost": (
            f"{would_drain} search-provider quer{'y' if would_drain == 1 else 'ies'} "
            "plus OpenAlex enrichment per ranked candidate"
            if provider_configured and would_drain
            else "none — nothing would be staged"
        ),
        "confirm_nonce": confirm_nonce.mint(
            vault_path, _DISCOVER_NONCE_KIND, topic, {"max_gaps": cap}
        ),
        "ttl": confirm_nonce.NONCE_TTL_SECONDS,
    }


def _execute_discover(
    store: VaultStore, vault_path: Path, topic: str, cap: int | None
) -> dict[str, Any]:
    """Phase 2: the billing boundary. Everything above this line is free."""
    result = refresh_suggestions_for_gaps(
        store, vault_path, topic, service=build_default_discovery_service(), max_gaps=cap
    )
    return envelope.read_ok(
        {
            "action": "gapfill_discover",
            "topic": topic,
            "provider_configured": result.service_available,
            "gaps_considered": result.gaps_considered,
            "gaps_drained": result.gaps_drained,
            "suggestions_staged": result.suggestions_written,
        }
    )


def _drainable_gap_count(store: VaultStore, topic: str) -> int:
    """Open ``genuine_gap`` records -- what a drain would actually consider.

    Narrower than ``gaps_read``'s ``open`` filter, which also shows ``dilution``
    gaps: discovery has nothing to search for on one, so counting them here would
    quote a drain larger than the one that runs.
    """
    records, _skipped = _read_gap_records(store, topic)
    return sum(
        1 for record in records if record.status == "open" and record.fault_class == "genuine_gap"
    )


def _validate_max_gaps(max_gaps: int) -> int | None:
    """``0`` means "every open gap"; anything negative is a caller error."""
    if max_gaps < 0:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"max_gaps must be 0 (all) or a positive count, got {max_gaps}",
            fix="Pass max_gaps=0 to drain every open gap, or a positive cap.",
        )
    return max_gaps or None


# ---------------------------------------------------------------------------
# Validation helpers (duplicated per module -- see the module docstring)
# ---------------------------------------------------------------------------


def _validate_topic(topic: str) -> str:
    """Normalize a topic to a single path segment or raise ``TOPIC_NOT_FOUND``."""
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    return cleaned


def _validate_limit(limit: int) -> int:
    if limit < 1 or limit > _MAX_LIMIT:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"limit must be in 1..{_MAX_LIMIT}, got {limit}",
            fix=f"Pass limit between 1 and {_MAX_LIMIT}.",
        )
    return limit
