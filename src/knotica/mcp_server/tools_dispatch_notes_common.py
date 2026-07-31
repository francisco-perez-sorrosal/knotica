"""Shared vocabulary, argument validation, and cursor helpers for the ``notes``
dispatcher's actions.

The ``notes`` dispatcher's payload construction lives in two cohesion-scoped
sibling modules -- :mod:`knotica.mcp_server.tools_dispatch_notes_read`
(``list``/``read``/``drift``: no lock, no commit, no decision envelope) and
:mod:`knotica.mcp_server.tools_dispatch_notes_mutations` (``reanchor``/
``detach``: dry-run/apply mode pair, one commit per ``apply``, a decision
envelope on ``dry-run``). This module is the leaf both sit on: the
resolved-anchor status vocabulary, the argument validators every action
shares, the opaque-cursor helpers, and :func:`_drift_live_quote` -- the one
piece of read-only lookup both the drift review queue and the mutating
actions' preview context need. It imports from neither sibling, keeping the
dependency graph one-directional (both siblings import this leaf; the router
in :mod:`knotica.mcp_server.tools_dispatch_notes` imports all three; nothing
imports the router).

``status`` is a note's resolved-anchor bucket, derived from the resolved
projections of its anchors: a note is as drifted as its weakest anchor. The
resolver ladder produces ``exact``, ``shifted``, ``fuzzy``, ``orphaned``, and
``unanchored``.
"""

from __future__ import annotations

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.notes.anchor import AnchorRecord
from knotica.core.notes.resolve import Projection
from knotica.core.page import TopicNotFoundError
from knotica.core.schema import validated_topic
from knotica.mcp_server.dispatch_telemetry import record_rejected_action
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
#: through its own count instead (see :func:`_drift_status` in
#: :mod:`knotica.mcp_server.tools_dispatch_notes_read`).
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

#: ``reanchor``/``detach``'s two modes: ``dry-run`` (the schema default)
#: previews without writing, ``apply`` performs exactly one commit.
_MODES: tuple[str, ...] = ("dry-run", "apply")
_DEFAULT_MODE = "dry-run"


# ---------------------------------------------------------------------------
# Cursor helpers -- shared page-token contract for `list` and `drift`
# ---------------------------------------------------------------------------


def _next_cursor(query: str, offset: int, has_more: bool) -> str:
    """Mint an opaque cursor for ``query`` (an action's own filter token), or ``""``."""
    if not has_more:
        return ""
    return encode_cursor(Cursor(query=query, sort=_NOTES_SORT, offset=offset))


def _cursor_query(intent_filter: str, status_filter: str) -> str:
    """``list``'s cursor query -- both filter axes pinned in, so changing either
    invalidates a prior page's cursor. ``drift`` has no filter axes and uses its
    own fixed query token instead of calling this."""
    return f"intent={intent_filter};status={status_filter}"


def _resolve_offset(cursor: str, query: str) -> int:
    """Decode an opaque page cursor, failing closed on a stale/malformed token.

    ``query`` is the action's own filter token (see :func:`_cursor_query` for
    ``list``; ``drift`` uses its own fixed token) -- a cursor minted under a
    different one cannot continue this read.
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
# Read-only lookup shared by the drift review queue and the mutating actions'
# preview context -- lives here (not in the read module) because the
# mutations module needs it too, and read/mutations do not import each other.
# ---------------------------------------------------------------------------


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


def _validate_mode(mode: str) -> str:
    cleaned = mode.strip().lower().replace("_", "-")
    if cleaned not in _MODES:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"mode must be one of {'|'.join(_MODES)}, got {mode!r}",
            fix=f"Pass mode as one of: {', '.join(_MODES)}.",
        )
    return cleaned
