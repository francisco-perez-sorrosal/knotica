"""Search boundary -- ``SearchBackend`` protocol and the ripgrep backend.

Read-only full-text search over the vault, returning **pointer** results
(topic, path, snippet, score -- never full page bodies) inside the stable
pagination envelope ``{results, next_cursor, has_more, total_count}``. The
cursor is an opaque, self-contained base64 token (see :mod:`.cursor`), so the
stateless server holds no pagination memory between calls. Never writes the
vault; no git/log/schema knowledge. Swappable behind the protocol (future
embedding-based backends keep the exact same envelope).

Contract summary
----------------
* Default page size 10, maximum 50; out-of-range ``limit`` values are clamped,
  never errors.
* ``topic=""`` searches all topics; a named topic scopes the search to that
  topic's pages **and** its stored sources (``sources/<topic>/``).
* Ordering is deterministic -- score descending, ties broken by path
  ascending -- because cursor validity depends on page N and page N+1 ranking
  the full result set identically.
* A malformed or stale cursor raises
  :class:`~knotica.search.cursor.InvalidCursorError`; adapters map it to the
  ``INVALID_CURSOR`` error envelope.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from knotica.search.cursor import (
    SORT_SCORE_DESC_PATH_ASC,
    Cursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    resolve_offset,
)

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "SORT_SCORE_DESC_PATH_ASC",
    "Cursor",
    "InvalidCursorError",
    "ResultKind",
    "RipgrepBackend",
    "SearchBackend",
    "SearchPage",
    "SearchResult",
    "clamp_limit",
    "decode_cursor",
    "encode_cursor",
    "paginate",
    "resolve_offset",
]

#: Page-size contract: default keeps responses small for the model consumer;
#: the maximum bounds a single response even when the caller asks for more.
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50

#: What kind of vault file a pointer refers to: a wiki page or a stored
#: source under ``sources/<topic>/``. Sources are searched too (they carry
#: the raw material pages cite); the marker lets the consumer tell them apart.
ResultKind = Literal["page", "source"]


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One pointer result -- where a match lives, never the page body.

    Attributes:
        topic: The topic the file belongs to (`""` for vault-root files such
            as the catalog pages). For sources this is the ``<topic>`` segment
            of ``sources/<topic>/...``.
        path: Vault-relative POSIX path of the matching file.
        snippet: The first matching line, stripped and truncated -- enough to
            decide whether to ``read_page`` it.
        score: Relevance score -- total term-occurrence count in the file.
        kind: ``"page"`` or ``"source"`` (see :data:`ResultKind`).
    """

    topic: str
    path: str
    snippet: str
    score: int
    kind: ResultKind

    def render(self) -> dict[str, Any]:
        """Render as the plain-dict shape carried in the envelope's ``results``."""
        return {
            "topic": self.topic,
            "path": self.path,
            "snippet": self.snippet,
            "score": self.score,
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One page of the search envelope ``{results, next_cursor, has_more, total_count}``.

    ``next_cursor`` is the opaque token for the next page, or ``""`` when this
    is the last page (mirroring the tool argument's empty-string default for
    "no cursor").
    """

    results: tuple[SearchResult, ...]
    next_cursor: str
    has_more: bool
    total_count: int

    def render(self) -> dict[str, Any]:
        """Render as the plain-dict envelope adapters return to the client."""
        return {
            "results": [result.render() for result in self.results],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "total_count": self.total_count,
        }


def clamp_limit(limit: int) -> int:
    """Clamp a requested page size into the contract range (1..50)."""
    return min(max(limit, 1), MAX_PAGE_SIZE)


def paginate(
    ranked: Sequence[SearchResult],
    query: str,
    offset: int,
    limit: int,
) -> SearchPage:
    """Slice a fully **ranked** result sequence into one envelope page.

    ``ranked`` must already be in the deterministic sort order the cursor
    contract pins (score descending, path ascending) -- this helper only
    windows it. An offset at or past the end yields an empty final page with
    ``has_more=False`` (a cursor whose result set shrank underneath it is not
    an error; the walk simply ends). Backend-agnostic: any future backend
    reuses this to keep the envelope identical.
    """
    total_count = len(ranked)
    page = tuple(ranked[offset : offset + limit])
    has_more = offset + limit < total_count
    next_cursor = ""
    if has_more:
        next_cursor = encode_cursor(
            Cursor(query=query, sort=SORT_SCORE_DESC_PATH_ASC, offset=offset + limit)
        )
    return SearchPage(
        results=page, next_cursor=next_cursor, has_more=has_more, total_count=total_count
    )


@runtime_checkable
class SearchBackend(Protocol):
    """Structural protocol for vault search backends.

    One method: cursor in, envelope (cursor out) -- the full pagination
    contract lives at this seam so a backend swap changes nothing the
    consumer sees. Implementations must be read-only and deterministic
    (identical vault + query => identical ranking across calls, or every
    outstanding cursor silently pages wrong).
    """

    def search(
        self,
        query: str,
        *,
        topic: str = "",
        cursor: str = "",
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> SearchPage:
        """Search the vault and return one page of pointer results.

        Args:
            query: Search terms (whitespace-separated; OR semantics).
            topic: Scope to one topic (its pages + its sources); ``""``
                searches all topics.
            cursor: Opaque token from a prior page's ``next_cursor``; ``""``
                for the first page.
            limit: Results per page; clamped to 1..50.

        Raises:
            InvalidCursorError: If ``cursor`` is malformed or stale.
        """
        ...


from knotica.search.ripgrep import RipgrepBackend  # noqa: E402  (re-export; needs the names above)
