"""Opaque, self-contained search cursor -- encode/decode with strict validation.

The pagination contract exposes an **opaque** ``next_cursor`` token instead of
a raw offset so the model-facing contract survives future backend swaps
(ripgrep today, vector/hybrid later) unchanged. The token is a URL-safe base64
encoding of ``{"query": ..., "sort": ..., "offset": ..., "families": ...}`` --
fully self-contained, so a stateless server needs no cursor memory between
calls.

Validation is strict and fails closed: anything that is not exactly the
expected shape -- bad base64, bad JSON, wrong keys, wrong types, negative
offset -- raises :class:`InvalidCursorError`. So does a **stale** token: one
minted for a different query, under a different sort contract than the one
this module currently guarantees, or under a different folder-family
selection. ``sort`` and ``families`` both exist to invalidate outstanding
tokens rather than silently page wrongly -- ``sort`` when the *ordering*
changes, ``families`` when the *result set* does. Adapters map the exception
to the ``INVALID_CURSOR`` envelope code ("restart the search without a
cursor").

Because the payload shape is checked exactly, adding ``families`` invalidated
every token minted before it existed. That is the intended failure: such a
token decodes to ``InvalidCursorError`` and the caller restarts the walk,
which is strictly safer than honouring an offset whose corpus is unknown.
Cursors live for the length of one page walk, so nothing durable depends on
them.
"""

import base64
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass

#: The sort contract every cursor is pinned to. Cursor validity depends on the
#: ordering being deterministic: page N and page N+1 are separate calls, so the
#: full ranking must come out identical both times (score descending, ties
#: broken by path ascending). A token carrying any other sort id is stale.
SORT_SCORE_DESC_PATH_ASC = "score-desc,path-asc"

_CURSOR_FIELDS = frozenset({"query", "sort", "offset", "families"})


class InvalidCursorError(ValueError):
    """A search cursor token is malformed or stale.

    Malformed: not base64, not JSON, wrong keys or value types, negative
    offset. Stale: minted for a different query, or under a sort contract
    other than the current one. Either way the token cannot be trusted to
    continue a page walk; the caller must restart the search without a
    cursor. Adapters render this as the ``INVALID_CURSOR`` error envelope.
    """


@dataclass(frozen=True, slots=True)
class Cursor:
    """The decoded pagination state a ``next_cursor`` token self-carries.

    ``families`` is the canonical rendering of the folder-family selection a
    search cursor was minted under (see :func:`canonical_families`), or ``""``
    for cursors on surfaces that have no family axis at all -- the notes,
    drift-queue and suggestions walks. It defaults to ``""`` so those surfaces
    mint tokens unchanged.
    """

    query: str
    sort: str
    offset: int
    families: str = ""


def canonical_families(families: Iterable[str]) -> str:
    """Render a family selection as a deterministic cursor field.

    Sorted and comma-joined, because the cursor payload is compared for
    equality after a JSON round-trip: an unsorted rendering would mint two
    different tokens for the same selection and invalidate a caller's own
    walk depending on set iteration order.
    """
    return ",".join(sorted(families))


def encode_cursor(cursor: Cursor) -> str:
    """Render ``cursor`` as an opaque URL-safe base64 token."""
    payload = json.dumps(asdict(cursor), sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(token: str) -> Cursor:
    """Decode ``token`` back into a :class:`Cursor`, validating shape strictly.

    Raises:
        InvalidCursorError: If the token is not base64, not JSON, not an
            object with exactly the keys ``query``/``sort``/``offset``, or
            carries wrong value types (offset must be a non-negative int;
            bools are rejected).
    """
    try:
        # b64decode with the URL-safe alphabet: urlsafe_b64decode has no
        # `validate` parameter, and without validation non-alphabet bytes are
        # silently discarded instead of failing closed.
        payload = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        raw = json.loads(payload.decode("utf-8"))
    except ValueError as exc:  # binascii.Error, JSONDecodeError, UnicodeError all subclass it
        raise InvalidCursorError(f"Cursor token is not a valid encoded cursor: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != _CURSOR_FIELDS:
        raise InvalidCursorError(
            f"Cursor payload must be an object with exactly the keys {sorted(_CURSOR_FIELDS)}."
        )
    # mypy false positive (verified, td-019 cluster D): `set(raw) != _CURSOR_FIELDS`
    # compares a `set` to a `frozenset`, and mypy's warn_unreachable binder
    # mis-evaluates that as always-true, marking every line below as dead --
    # reproduced in isolation with `_CURSOR_FIELDS` typed as plain `set[str]`
    # (no false positive) vs. `frozenset[str]` (false positive), independent
    # of `raw`'s `Any`-ness. The validation itself is correct and unchanged.
    query, sort, offset = raw["query"], raw["sort"], raw["offset"]
    families = raw["families"]
    if not isinstance(query, str) or not isinstance(sort, str) or not isinstance(families, str):
        raise InvalidCursorError("Cursor 'query', 'sort' and 'families' must be strings.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise InvalidCursorError("Cursor 'offset' must be a non-negative integer.")
    return Cursor(query=query, sort=sort, offset=offset, families=families)


def resolve_offset(token: str, query: str, families: str = "") -> int:
    """Return the page offset a cursor ``token`` continues from.

    An empty token means "first page" (offset 0). A non-empty token is
    decoded and checked for staleness against the live call: its ``sort``
    must be the current sort contract, its ``query`` must match the query
    being searched, and its ``families`` must match the selection being
    searched -- a cursor from a different query or a different family
    selection cannot be trusted to continue this walk.

    The ``families`` check matters as much as ``sort``: changing the family
    selection changes the *result set*, so continuing at an offset minted
    under a different selection would silently page into a different corpus
    rather than merely reorder one. Callers on surfaces with no family axis
    pass ``""`` and are unaffected.

    Raises:
        InvalidCursorError: On any malformed or stale token.
    """
    if not token:
        return 0
    cursor = decode_cursor(token)
    if cursor.sort != SORT_SCORE_DESC_PATH_ASC:
        raise InvalidCursorError(
            f"Cursor was minted under sort '{cursor.sort}', "
            f"but the current sort contract is '{SORT_SCORE_DESC_PATH_ASC}'."
        )
    if cursor.query != query:
        raise InvalidCursorError(
            "Cursor was minted for a different query and cannot continue this search."
        )
    if cursor.families != families:
        raise InvalidCursorError(
            f"Cursor was minted for families '{cursor.families or '(none)'}', "
            f"but this search selects '{families or '(none)'}'."
        )
    return cursor.offset
