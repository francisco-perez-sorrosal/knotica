"""Operator dispatcher ``notes`` -- Phase 1 registers exactly ``list`` and ``read``.

Recall and inspection of the personal notes layer. Both actions are read-only,
so -- like ``arena`` -- this dispatcher carries no mutation precondition in its
description: there is nothing here to gate.

**Deliberately restricted action set.** The full notes design names seven
actions; the five that mutate or resolve drift (``drift``, ``reanchor``,
``detach``, ``promote``, ``archive``) are a later phase and are *not* registered
here. Supplying one is rejected with ``INVALID_ARGUMENT`` rather than accepted
and quietly ignored -- an action that appears to work and does nothing is worse
than one that says it does not exist.

``status`` is a note's resolved-anchor bucket, derived from the resolved
projections of its anchors: a note is as drifted as its weakest anchor. Phase
1's resolver ladder produces ``exact``, ``shifted``, ``orphaned``, and
``unanchored`` -- there is no fuzzy rung yet, so no ``fuzzy`` key is invented
to stand in for a capability that does not exist. ``unanchored`` is not drift
-- it means the anchor never pointed at a page at all (no quote, an unreadable
claimed page, or a quote matched on several claimed pages), never that
something the anchor once pointed at is now gone -- but it is still a real
bucket a caller can filter and count on.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.notes.store import NotesListing, ResolvedNote, list_notes
from knotica.core.page import TopicNotFoundError
from knotica.core.schema import validated_topic
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.dispatch_telemetry import record_dispatch, record_rejected_action
from knotica.mcp_server.tools_notes import render_anchors
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.search.cursor import Cursor, InvalidCursorError, decode_cursor, encode_cursor
from knotica.store import VaultStore

__all__ = ["register_dispatch_notes_tools"]

ToolResult = CallToolResult

_DISPATCHER = "notes"
_ACTIONS = ("list", "read")

#: The resolved-anchor buckets Phase 1's resolver can actually produce, ordered
#: **least severe first**. ``unanchored`` sits between ``exact`` and
#: ``shifted``: a genuine ``orphaned`` or ``shifted`` anchor on the same note
#: must still surface over a merely unanchored one, since those are the buckets
#: a person actually needs to act on.
#:
#: **The order is load-bearing, in four places at once**: it is the severity
#: ladder ``_drift_status`` walks to bucket a multi-anchor note, the membership
#: filter that decides which per-anchor statuses are bucketable at all, the key
#: set of ``status_counts``, and the accepted values of the ``status`` filter.
#: Inserting a bucket re-buckets every note that carries one on either side of
#: it; appending one makes it the most severe status in the vault. Neither
#: fails anything by itself, so the two constants below name the ends of the
#: ladder and a test pins them -- a reordering has to break a test before it can
#: break a listing.
_ANCHOR_STATUSES: tuple[str, ...] = ("exact", "unanchored", "shifted", "orphaned")

#: The two ends of :data:`_ANCHOR_STATUSES`, named so the ladder's orientation
#: is asserted rather than assumed. ``_drift_status`` reports the most severe
#: bucket a note carries, so the last element is the one a new bucket would
#: displace.
_LEAST_SEVERE_ANCHOR_STATUS = "exact"
_MOST_SEVERE_ANCHOR_STATUS = "orphaned"

#: The synthetic filter value meaning "do not filter on this axis".
_ALL_FILTER = "all"

_INTENT_VALUES: tuple[str, ...] = ("reflection", "dispute", "gap", "question")

#: A hand-authored note may carry any intent string -- the parser deliberately
#: does not enforce ``NOTE_INTENTS`` on read, so a note stays readable even
#: with a typo'd or invented intent. This bucket keeps `intent_counts` summing
#: to `total_count` for those notes, rather than silently under-reporting.
_OTHER_INTENT = "other"

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50

#: The listing's sort contract: newest note first, id ascending as the
#: tiebreak. A cursor minted under any other ordering is stale.
_NOTES_SORT = "created-desc,id-asc"

_NOTES_DISPATCH_DESCRIPTION = (
    "Browse the personal notes layer (marginalia) for one topic -- the notes "
    "written with `note_capture` or by hand in Obsidian. `action=list` is the "
    'recall path ("what did I note about this?"): notes live outside the wiki '
    "corpus, so `search` will never find them. Filter `list` by `intent` "
    "(reflection|dispute|gap|question|all) and by resolved anchor `status` "
    "(exact|shifted|orphaned|unanchored|all), and paginate with the opaque cursor from a "
    "prior next_cursor (default 20, max 50 per page); the response carries "
    "intent_counts and status_counts for the whole topic. `action=read` returns "
    "one note in full -- its text and every anchor with the page, the passage "
    "originally pinned, and how that pin resolves against the vault today. Both "
    "actions are read-only: no commits, no lock. Pass vault to select a "
    "configured vault."
)


def register_dispatch_notes_tools(mcp: FastMCP) -> None:
    """Register the ``notes`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="notes", description=_NOTES_DISPATCH_DESCRIPTION)
    def notes(
        action: str,
        topic: str = "",
        note_id: str = "",
        intent: str = _ALL_FILTER,
        status: str = _ALL_FILTER,
        cursor: str = "",
        limit: int = _DEFAULT_LIMIT,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _dispatch_payload(
                store,
                resolved.path,
                action,
                topic,
                note_id=note_id,
                intent=intent,
                status=status,
                cursor=cursor,
                limit=limit,
            ),
        )


def _dispatch_payload(
    store: VaultStore,
    vault_path: Path,
    action: str,
    topic: str,
    *,
    note_id: str,
    intent: str,
    status: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action)
    cleaned_topic = _validate_topic(store, topic)
    record_dispatch(_DISPATCHER, cleaned_action, cleaned_topic)
    listing = list_notes(store, VaultVcs(vault_path), cleaned_topic)
    if cleaned_action == "read":
        return _read_payload(cleaned_topic, listing, note_id)
    return _list_payload(
        cleaned_topic, listing, intent=intent, status=status, cursor=cursor, limit=limit
    )


# ---------------------------------------------------------------------------
# list -- filter, sort, paginate, count
# ---------------------------------------------------------------------------


def _list_payload(
    topic: str,
    listing: NotesListing,
    *,
    intent: str,
    status: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    intent_filter = _validate_intent_filter(intent)
    status_filter = _validate_status_filter(status)
    page_size = _validate_limit(limit)

    matching = _sorted(_filtered(listing.notes, intent_filter, status_filter))
    offset = _resolve_offset(cursor, intent_filter, status_filter)
    page = matching[offset : offset + page_size]
    has_more = offset + page_size < len(matching)
    return {
        "topic": topic,
        "intent_filter": intent_filter,
        "status_filter": status_filter,
        "notes": [_note_summary(note) for note in page],
        "intent_counts": _intent_counts(listing.notes),
        "status_counts": _status_counts(listing.notes),
        "next_cursor": _next_cursor(intent_filter, status_filter, offset + page_size, has_more),
        "has_more": has_more,
        "total_count": len(matching),
        "skipped_malformed": listing.skipped_malformed,
    }


def _filtered(
    notes: tuple[ResolvedNote, ...], intent_filter: str, status_filter: str
) -> list[ResolvedNote]:
    return [
        note
        for note in notes
        if _matches_intent(note.document.intent, intent_filter)
        and _matches(_drift_status(note), status_filter)
    ]


def _matches(value: str | None, wanted: str) -> bool:
    return wanted == _ALL_FILTER or value == wanted


def _matches_intent(value: str, wanted: str) -> bool:
    """Like :func:`_matches`, but ``other`` catches a hand-typed unknown intent."""
    if wanted == _OTHER_INTENT:
        return value not in _INTENT_VALUES
    return _matches(value, wanted)


def _sorted(notes: list[ResolvedNote]) -> list[ResolvedNote]:
    """Newest note first, id ascending as the tiebreak -- the cursor's contract."""
    by_id = sorted(notes, key=lambda note: note.document.id)
    return sorted(by_id, key=lambda note: note.document.created, reverse=True)


def _note_summary(note: ResolvedNote) -> dict[str, Any]:
    document = note.document
    return {
        "note_id": document.id,
        "path": note.path,
        "intent": document.intent,
        "created": document.created,
        "updated": document.updated,
        "note_status": document.status,
        "status": _drift_status(note),
        "tags": list(document.tags),
        "note": document.body,
        "anchors": render_anchors(note),
        "skipped_anchor_count": document.skipped_anchor_count,
    }


def _drift_status(note: ResolvedNote) -> str | None:
    """The note's drift bucket -- as drifted as its most severe bucketable anchor.

    Walks :data:`_ANCHOR_STATUSES` from the severe end; see that constant for
    why its order cannot be changed casually.

    ``None`` for a note with no bucketable anchors: either it has no anchors
    at all, or every anchor is ``anchor-invalid`` -- a corrupt/hand-forged
    record, not an anchor-resolution outcome. ``anchor-invalid`` is never
    folded into ``orphaned`` here: ``core/status.py`` counts ``drifted`` as
    ``orphaned`` only, and folding would inflate that count with a
    data-integrity problem rather than a "the wiki moved on" one. The
    per-anchor status still surfaces ``anchor-invalid`` verbatim via
    ``render_anchors`` -- only the note-level bucket excludes it.
    """
    statuses = {
        projection.status
        for _anchor, projection in note.resolved_anchors
        if projection.status in _ANCHOR_STATUSES
    }
    if not statuses:
        return None
    for candidate in reversed(_ANCHOR_STATUSES):
        if candidate in statuses:
            return candidate
    return None


def _intent_counts(notes: tuple[ResolvedNote, ...]) -> dict[str, int]:
    """Per-intent breakdown, plus ``other`` for a hand-typed unknown intent.

    Always sums to the topic's ``total_count``: every note has exactly one of
    the four known intents or falls into ``other`` -- never silently dropped.
    """
    counter = Counter(note.document.intent for note in notes)
    counts = {value: counter.get(value, 0) for value in _INTENT_VALUES}
    counts[_OTHER_INTENT] = sum(
        count for intent, count in counter.items() if intent not in _INTENT_VALUES
    )
    return counts


def _status_counts(notes: tuple[ResolvedNote, ...]) -> dict[str, int]:
    """Per-status breakdown over every anchored note in the topic.

    Anchorless notes are in no bucket, so the counts can sum to less than
    ``total_count`` -- deliberately, rather than inflating ``exact``.
    """
    counter = Counter(status for note in notes if (status := _drift_status(note)) is not None)
    return {value: counter.get(value, 0) for value in _ANCHOR_STATUSES}


def _next_cursor(intent_filter: str, status_filter: str, offset: int, has_more: bool) -> str:
    if not has_more:
        return ""
    return encode_cursor(
        Cursor(query=_cursor_query(intent_filter, status_filter), sort=_NOTES_SORT, offset=offset)
    )


def _cursor_query(intent_filter: str, status_filter: str) -> str:
    """Both filter axes pinned into the token -- changing either invalidates it."""
    return f"intent={intent_filter};status={status_filter}"


def _resolve_offset(cursor: str, intent_filter: str, status_filter: str) -> int:
    """Decode an opaque page cursor, failing closed on a stale/malformed token."""
    if not cursor:
        return 0
    decoded = decode_cursor(cursor)
    if decoded.sort != _NOTES_SORT:
        raise InvalidCursorError(
            f"Cursor was minted under sort {decoded.sort!r}, "
            f"but the current sort contract is {_NOTES_SORT!r}."
        )
    if decoded.query != _cursor_query(intent_filter, status_filter):
        raise InvalidCursorError(
            "Cursor was minted for a different intent/status filter and cannot continue this read."
        )
    return decoded.offset


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def _read_payload(topic: str, listing: NotesListing, note_id: str) -> dict[str, Any]:
    cleaned_id = note_id.strip()
    if not cleaned_id:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            "notes action=read failed because no note_id was given.",
            fix="Pass the note_id from `notes action=list`.",
        )
    for note in listing.notes:
        if note.document.id == cleaned_id:
            return {"topic": topic, **_note_summary(note)}
    raise KnoticaError(
        ErrorCode.NOTE_NOT_FOUND,
        f"notes action=read failed because no note {cleaned_id!r} exists in topic {topic!r}.",
    )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def _validate_action(action: str) -> str:
    cleaned = action.strip().lower()
    if cleaned not in _ACTIONS:
        record_rejected_action(_DISPATCHER, action, _ACTIONS)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"notes action must be one of {'|'.join(_ACTIONS)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(_ACTIONS)}.",
        )
    return cleaned


def _validate_topic(store: VaultStore, topic: str) -> str:
    """Reject anything that is not an existing topic directory.

    Mirrors ``capture_note``'s validation exactly: a bare-topic-shape check
    (``core.schema.validated_topic``, which also rejects dot-prefixed
    segments like ``.``/``..``) followed by an existence check against the
    store. Without the existence check, a mistyped topic silently returns an
    empty listing instead of ``TOPIC_NOT_FOUND`` -- a wrong answer delivered
    with false confidence.
    """
    try:
        cleaned = validated_topic(topic)
    except ValueError as error:
        raise TopicNotFoundError(topic or "(empty)") from error
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    return cleaned


def _validate_intent_filter(intent: str) -> str:
    cleaned = intent.strip().lower()
    allowed = (*_INTENT_VALUES, _OTHER_INTENT, _ALL_FILTER)
    if cleaned not in allowed:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"intent must be one of {'|'.join(allowed)}, got {intent!r}",
            fix=f"Pass intent as one of: {', '.join(allowed)}.",
        )
    return cleaned


def _validate_status_filter(status: str) -> str:
    cleaned = status.strip().lower()
    if cleaned != _ALL_FILTER and cleaned not in _ANCHOR_STATUSES:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"status must be one of {'|'.join(_ANCHOR_STATUSES)}|{_ALL_FILTER}, got {status!r}",
            fix=f"Pass status as one of: {', '.join((*_ANCHOR_STATUSES, _ALL_FILTER))}.",
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
