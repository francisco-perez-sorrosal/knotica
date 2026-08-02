"""Payload construction for the ``notes`` dispatcher's read-only actions --
``list``, ``read``, and ``drift``.

None of the three take a lock, make a commit, or return a decision envelope --
that is what distinguishes them from the mutating pair in
:mod:`knotica.mcp_server.tools_dispatch_notes_mutations`. Shared vocabulary,
argument validation, and cursor helpers live in the leaf module
:mod:`knotica.mcp_server.tools_dispatch_notes_common`; the router in
:mod:`knotica.mcp_server.tools_dispatch_notes` only registers the MCP tool and
dispatches into this module and its mutating sibling -- it does not know how a
page is built.

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
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.tools_dispatch_notes_common import (
    _ALL_FILTER,
    _ANCHOR_STATUSES,
    _INTENT_VALUES,
    _OTHER_INTENT,
    _cursor_query,
    _drift_live_quote,
    _next_cursor,
    _resolve_offset,
    _validate_intent_filter,
    _validate_limit,
    _validate_status_filter,
)
from knotica.mcp_server.tools_notes import render_anchors
from knotica.store import VaultStore

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
        listing=listing,
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
    listing: NotesListing,
) -> dict[tuple[str, int], Transition]:
    """Index the topic's transitions by ``(note_id, anchor_index)``.

    ``listing`` is threaded through rather than re-derived: the caller already
    resolved it to find the queue members, and resolving it twice was the
    dominant cost of a drift-queue open.
    """
    transitions = reconcile_notes(
        store,
        vcs,
        topic,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
        listing=listing,
    )
    return {(transition.note_id, transition.anchor_index): transition for transition in transitions}


def _drift_item(
    store: VaultStore,
    member: _DriftMember,
    transition: Transition | None,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    note, anchor_index, anchor, projection = member
    # A superseded page was replaced outright, so the ladder's best guess points
    # into content that has nothing to do with the anchored passage. Offering it
    # invites the reader to re-anchor onto an unrelated span; saying "this page
    # was replaced" is both true and actionable, where "it might be here" is
    # neither. Phase 3 measured one such event supplying 85% of all orphaning.
    superseded = transition.superseded if transition else False
    # `None` -- not `0.0` -- when the ladder had to supply a sentinel instead of a
    # measurement. Rung 8's no-candidate clamp is `guess_threshold - CLAMP_EPSILON`,
    # a *ceiling*, so rendering it as a survival percentage shows the case with the
    # least evidence as the most confident one. A consumer must distinguish "0% of
    # it survived" from "nothing was comparable".
    overlap = projection.score if projection.score_measured else None
    return {
        "note": _note_summary(note),
        "drift": {
            "anchor_index": anchor_index,
            "pinned_quote": anchor.quote,
            "live_quote": _drift_live_quote(store, anchor, projection),
            "overlap": overlap,
            "cause": "superseded" if superseded else "rewritten",
            "alternatives": []
            if superseded
            else _drift_alternatives(anchor, projection, overlap, complete_orphan_threshold),
            "rewritten_at": transition.rewritten_at
            if transition and transition.rewritten_at
            else "",
            "rewritten_by": transition.rewritten_by
            if transition and transition.rewritten_by
            else "",
        },
    }


def _drift_alternatives(
    anchor: AnchorRecord,
    projection: Projection,
    overlap: float | None,
    complete_orphan_threshold: float,
) -> list[dict[str, Any]]:
    """One alternative when there is a guess worth showing, else none.

    Two independent reasons to offer one, because the ladder has two:

    - `projection.best_guess` is populated -- rung 8's *structural* guess (the
      enclosing heading survived) or rung 9's argmax window. A structural guess
      stands on the heading match, not on a similarity score, so it is offered
      regardless of `overlap` and even when nothing was measurable at all. The
      threshold alone cannot express that: it would drop the guess in exactly
      the case where the surviving heading is the only trustworthy evidence.
    - a *measured* `overlap` at or above `complete_orphan_threshold`. This is
      what carries `fuzzy`, which deliberately sets no `best_guess` -- its
      `span` already claims the placement, and duplicating it under a field
      meaning "might be here, not claiming it" would invite a consumer to read
      the wrong one.

    `anchor-invalid` and deleted-page orphans satisfy neither: no candidate
    search ran, and no heading was matched.

    Candidate generation only ever searches the anchor's own page in this
    phase, so `page` is always `anchor.page`. `overlap` is `None` when the
    guess is structural rather than measured; the consumer must render that as
    prose, never as a percentage.
    """
    has_structural_guess = projection.best_guess is not None
    clears_floor = overlap is not None and overlap >= complete_orphan_threshold
    if not has_structural_guess and not clears_floor:
        return []
    return [{"page": anchor.page, "heading": anchor.heading, "overlap": overlap}]
