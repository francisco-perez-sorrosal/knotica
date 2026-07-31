"""Payload construction and validation for the ``notes`` dispatcher's actions.

This module builds ``action=list``/``action=read``/``action=drift``'s response
payloads and validates the ``notes`` tool's arguments. The router in
:mod:`knotica.mcp_server.tools_dispatch_notes` only registers the MCP tool
and dispatches into this module -- it does not know how a page is built.

``status`` is a note's resolved-anchor bucket, derived from the resolved
projections of its anchors: a note is as drifted as its weakest anchor. The
resolver ladder produces ``exact``, ``shifted``, ``fuzzy``, ``orphaned``, and
``unanchored``. ``unanchored`` is not drift -- it means the anchor never
pointed at a page at all (no quote, an unreadable claimed page, or a quote
matched on several claimed pages), never that something the anchor once
pointed at is now gone -- but it is still a real bucket a caller can filter
and count on.

``action=drift`` is the review queue: every anchor (not note) whose resolved
status is ``fuzzy``, ``orphaned``, or ``anchor-invalid`` -- the three buckets
a human actually needs to look at. See the "drift" section below for the
per-item payload shape.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.notes.anchor import AnchorRecord
from knotica.core.notes.reconcile import Transition, reconcile_notes
from knotica.core.notes.resolve import Projection
from knotica.core.notes.store import NotesListing, ResolvedNote
from knotica.core.page import TopicNotFoundError
from knotica.core.schema import validated_topic
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.dispatch_telemetry import record_rejected_action
from knotica.mcp_server.tools_notes import render_anchors
from knotica.search.cursor import Cursor, InvalidCursorError, decode_cursor, encode_cursor
from knotica.store import VaultStore

#: The synthetic filter value meaning "do not filter on this axis".
_ALL_FILTER = "all"

#: The resolved-anchor buckets the resolver can actually produce, ordered
#: **least severe first**. ``unanchored`` sits between ``exact`` and
#: ``shifted``: a genuine ``orphaned``, ``fuzzy``, or ``shifted`` anchor on the
#: same note must still surface over a merely unanchored one, since those are
#: the buckets a person actually needs to act on. ``fuzzy`` sits between
#: ``shifted`` and ``orphaned``: the resolver kept the anchor placed, but only
#: via a paraphrase rather than the verbatim text, so it is more severe than a
#: self-healed ``shifted`` anchor but less severe than losing the passage
#: outright.
#:
#: ``anchor-invalid`` is deliberately **not** a member of this tuple. It is a
#: data-integrity outcome -- the quote is absent from the anchor's own
#: historical blob, meaning a corrupt or hand-forged record -- not a drift
#: outcome the resolver measured against the live vault. Bucketing it here
#: would report corruption as the vault's most severe drift; it is surfaced
#: through its own count instead (see :func:`_drift_status`).
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
_ANCHOR_STATUSES: tuple[str, ...] = ("exact", "unanchored", "shifted", "fuzzy", "orphaned")

#: The two ends of :data:`_ANCHOR_STATUSES`, named so the ladder's orientation
#: is asserted rather than assumed. ``_drift_status`` reports the most severe
#: bucket a note carries, so the last element is the one a new bucket would
#: displace.
_LEAST_SEVERE_ANCHOR_STATUS = "exact"
_MOST_SEVERE_ANCHOR_STATUS = "orphaned"

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
    query = _cursor_query(intent_filter, status_filter)
    offset = _resolve_offset(cursor, query)
    page = matching[offset : offset + page_size]
    has_more = offset + page_size < len(matching)
    return {
        "topic": topic,
        "intent_filter": intent_filter,
        "status_filter": status_filter,
        "notes": [_note_summary(note) for note in page],
        "intent_counts": _intent_counts(listing.notes),
        "status_counts": _status_counts(listing.notes),
        "next_cursor": _next_cursor(query, offset + page_size, has_more),
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
    ``fuzzy`` plus ``orphaned``, and folding ``anchor-invalid`` in would
    inflate that count with a data-integrity problem rather than a "the wiki
    moved on" one. The per-anchor status still surfaces ``anchor-invalid``
    verbatim via ``render_anchors`` -- only the note-level bucket excludes it.
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


def _next_cursor(query: str, offset: int, has_more: bool) -> str:
    """Mint an opaque cursor for ``query`` (an action's own filter token), or ``""``."""
    if not has_more:
        return ""
    return encode_cursor(Cursor(query=query, sort=_NOTES_SORT, offset=offset))


def _cursor_query(intent_filter: str, status_filter: str) -> str:
    """``list``'s cursor query -- both filter axes pinned in, so changing either
    invalidates a prior page's cursor. ``drift`` has no filter axes and uses
    :data:`_DRIFT_CURSOR_QUERY` instead of calling this."""
    return f"intent={intent_filter};status={status_filter}"


def _resolve_offset(cursor: str, query: str) -> int:
    """Decode an opaque page cursor, failing closed on a stale/malformed token.

    ``query`` is the action's own filter token (see :func:`_cursor_query` for
    ``list``, :data:`_DRIFT_CURSOR_QUERY` for ``drift``) -- a cursor minted
    under a different one cannot continue this read.
    """
    if not cursor:
        return 0
    decoded = decode_cursor(cursor)
    if decoded.sort != _NOTES_SORT:
        raise InvalidCursorError(
            f"Cursor was minted under sort {decoded.sort!r}, "
            f"but the current sort contract is {_NOTES_SORT!r}."
        )
    if decoded.query != query:
        raise InvalidCursorError(
            "Cursor was minted for a different filter and cannot continue this read."
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
# drift -- the review queue
#
# Membership is per-anchor, not per-note (a multi-anchor note resolves each
# anchor independently -- one queue item per anchor whose own status is a
# member, not one item per note): `fuzzy` union `orphaned` union
# `anchor-invalid`. `exact`, `shifted`, and `unanchored` self-healed or never
# pointed at anything, so none of the three belong in a human's review queue.
#
# `total_count` is `len(items)` -- the full queue, corruption included -- so
# pagination (`next_cursor`/`has_more`/`total_count`) stays one contract with
# `items`; `invalid_count` is a breakdown of how many of those are
# `anchor-invalid`, not a disjoint bucket. This deliberately differs from
# `wiki_status.notes.drifted` (`fuzzy + orphaned` only) -- the queue header
# and that badge disagree by design, not by bug.
# ---------------------------------------------------------------------------

#: The three resolved-anchor statuses a human needs to look at. `fuzzy` and
#: `orphaned` are resolver-measured drift (the wiki moved on); `anchor-invalid`
#: is a data-integrity outcome (the record itself is corrupt) but still needs
#: eyes on it. Mirrors `knotica.core.notes.reconcile`'s own membership bound --
#: keep the two definitions in sync if either changes.
_QUEUE_MEMBER_STATUSES = frozenset({"fuzzy", "orphaned", "anchor-invalid"})

#: `drift` takes no `intent`/`status` filter, so its cursor carries a fixed
#: token rather than one built from filter axes like `list`'s.
_DRIFT_CURSOR_QUERY = "action=drift"

#: A member tuple: the note it belongs to, the 0-based index of the anchor
#: within that note, the anchor itself, and its resolved projection.
_DriftMember = tuple[ResolvedNote, int, AnchorRecord, Projection]


def _drift_payload(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    listing: NotesListing,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    page_size = _validate_limit(limit)
    members = _drift_members(listing.notes)
    transitions = _transitions_by_anchor(
        store,
        vcs,
        topic,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    offset = _resolve_offset(cursor, _DRIFT_CURSOR_QUERY)
    page = members[offset : offset + page_size]
    has_more = offset + page_size < len(members)
    items = [
        _drift_item(
            store,
            member,
            transitions.get((member[0].document.id, member[1])),
            complete_orphan_threshold,
        )
        for member in page
    ]
    invalid_count = sum(
        1 for *_rest, projection in members if projection.status == "anchor-invalid"
    )
    return {
        "topic": topic,
        "items": items,
        "next_cursor": _next_cursor(_DRIFT_CURSOR_QUERY, offset + page_size, has_more),
        "has_more": has_more,
        "total_count": len(members),
        "invalid_count": invalid_count,
    }


def _drift_members(notes: tuple[ResolvedNote, ...]) -> list[_DriftMember]:
    """Every queue-member anchor across ``notes``, sorted like `list`'s notes."""
    return [
        (note, index, anchor, projection)
        for note in _sorted(list(notes))
        for index, (anchor, projection) in enumerate(note.resolved_anchors)
        if projection.status in _QUEUE_MEMBER_STATUSES
    ]


def _transitions_by_anchor(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> dict[tuple[str, int], Transition]:
    transitions = reconcile_notes(
        store,
        vcs,
        topic,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    return {(transition.note_id, transition.anchor_index): transition for transition in transitions}


def _drift_item(
    store: VaultStore,
    member: _DriftMember,
    transition: Transition | None,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    note, anchor_index, anchor, projection = member
    overlap = projection.score
    return {
        "note": _note_summary(note),
        "drift": {
            "anchor_index": anchor_index,
            "pinned_quote": anchor.quote,
            "live_quote": _drift_live_quote(store, anchor, projection),
            "overlap": overlap if overlap is not None else 0.0,
            "alternatives": _drift_alternatives(anchor, overlap, complete_orphan_threshold),
            "rewritten_at": transition.rewritten_at
            if transition and transition.rewritten_at
            else "",
            "rewritten_by": transition.rewritten_by
            if transition and transition.rewritten_by
            else "",
        },
    }


def _drift_live_quote(store: VaultStore, anchor: AnchorRecord, projection: Projection) -> str:
    """The live text at the anchor's resolved span -- populated for `fuzzy` only.

    `orphaned` (any fidelity) and `anchor-invalid` carry no placement
    confident enough to quote back verbatim -- a human compares
    `pinned_quote` against `alternatives` instead.
    """
    if projection.status != "fuzzy" or projection.span is None:
        return ""
    if not anchor.page or not store.exists(anchor.page):
        return ""
    start, end = projection.span
    return store.read_text(anchor.page)[start:end]


def _drift_alternatives(
    anchor: AnchorRecord, overlap: float | None, complete_orphan_threshold: float
) -> list[dict[str, Any]]:
    """One alternative when the best-scored candidate clears the floor, else none.

    Candidate generation only ever searches the anchor's own page in this
    phase, so `page` is always `anchor.page`. `overlap is None` covers both
    `anchor-invalid` (no candidate search ran at all -- the quote was never
    in the historical blob, so there is no trustworthy position to search
    from) and a deleted-page orphan (no page left to search): neither has a
    candidate to offer, regardless of the configured threshold.
    """
    if overlap is None or overlap < complete_orphan_threshold:
        return []
    return [{"page": anchor.page, "heading": anchor.heading, "overlap": overlap}]


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def _validate_action(action: str, dispatcher: str, actions: tuple[str, ...]) -> str:
    cleaned = action.strip().lower()
    if cleaned not in actions:
        record_rejected_action(dispatcher, action, actions)
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"notes action must be one of {'|'.join(actions)}, got {action!r}",
            fix=f"Pass action as one of: {', '.join(actions)}.",
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
