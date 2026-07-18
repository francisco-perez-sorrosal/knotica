"""Headless query retrieval — key-term search with page/source balance.

Interactive query (``query.md``) tells the client to ``search`` with the
question's *key terms*, not the full natural-language sentence. The headless
runners (:class:`~knotica.evals.runner.MessagesApiRunner`,
:func:`~knotica.core.query_engine.answer_question`) must mirror that: passing
the raw question as whitespace-split OR terms lets common words match huge
stored sources and crowd out short concept pages within the small top-K budget.
"""

from __future__ import annotations

import math
import string
from collections.abc import Sequence

from knotica.search import RipgrepBackend, SearchResult

__all__ = ["question_to_search_query", "retrieve_search_results"]

#: Terms dropped before headless search — question glue, not retrieval signal.
_SEARCH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "between",
        "by",
        "did",
        "differ",
        "difference",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "than",
        "that",
        "the",
        "this",
        "to",
        "vs",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
    }
)

#: Pool multiplier when scanning before the page/source merge.
_SCAN_POOL_FACTOR = 10

#: Upper bound on the pre-merge scan (search ``limit`` is clamped to 50).
_MAX_SCAN_LIMIT = 50

_PUNCT_STRIP = string.punctuation + "…"


def _clean_token(raw: str) -> str:
    """Strip leading/trailing punctuation without breaking in-word hyphens."""
    return raw.strip(_PUNCT_STRIP)


def question_to_search_query(question: str) -> str:
    """Reduce a natural-language question to search key terms.

    Strips edge punctuation, drops short tokens and :data:`_SEARCH_STOP_WORDS`, and
    preserves original casing when a token carries uppercase (proper nouns such
    as ``Huxley-Gödel``). Returns ``""`` when nothing distinctive remains.
    """
    terms: list[str] = []
    for raw in question.split():
        stripped = _clean_token(raw)
        if not stripped:
            continue
        normalized = stripped.lower()
        if len(normalized) < 3 or normalized in _SEARCH_STOP_WORDS:
            continue
        terms.append(stripped if any(character.isupper() for character in stripped) else normalized)
    return " ".join(terms)


def retrieve_search_results(
    backend: RipgrepBackend,
    topic: str,
    question: str,
    *,
    limit: int,
) -> tuple[SearchResult, ...]:
    """Return up to ``limit`` pointers for headless query synthesis.

    Uses :func:`question_to_search_query` (falling back to the trimmed question
    when that yields nothing), scans a bounded pool, then merges with a
    page-first quota so concept pages are not drowned out by large sources.
    """
    cleaned = question.strip()
    query = question_to_search_query(cleaned) or cleaned
    if not query:
        return ()
    scan_limit = min(_MAX_SCAN_LIMIT, max(limit, limit * _SCAN_POOL_FACTOR))
    page = backend.search(query, topic=topic, limit=scan_limit)
    if not page.results:
        return ()
    return _balanced_merge(page.results, limit=limit)


def _balanced_merge(ranked: Sequence[SearchResult], *, limit: int) -> tuple[SearchResult, ...]:
    """Take roughly half pages and half sources, then fill from the ranked pool."""
    if limit <= 0:
        return ()
    pages = [result for result in ranked if result.kind == "page"]
    sources = [result for result in ranked if result.kind == "source"]
    page_quota = max(1, math.ceil(limit / 2))
    source_quota = max(0, limit - page_quota)

    selected: list[SearchResult] = []
    seen: set[str] = set()

    def _take(pool: Sequence[SearchResult], quota: int) -> None:
        for result in pool:
            if len(selected) >= limit or quota <= 0:
                return
            if result.path in seen:
                continue
            selected.append(result)
            seen.add(result.path)
            quota -= 1

    _take(pages, page_quota)
    _take(sources, source_quota)
    for result in ranked:
        if len(selected) >= limit:
            break
        if result.path not in seen:
            selected.append(result)
            seen.add(result.path)
    return tuple(selected[:limit])
