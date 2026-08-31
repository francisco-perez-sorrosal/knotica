"""Locating a model-supplied support quote back to real line numbers in the page.

Provenance for a synthesized candidate: the model returns verbatim excerpts it
grounded its answer in, and this module finds each one in the page's raw text so a
reviewer can deep-link the evidence. **Model-supplied line numbers are never
trusted** -- the range is always recomputed here from the located span.

Two rungs, first-occurrence-wins on each: an exact substring hit, then a
whitespace-normalized hit that recovers a quote the model copied across a
soft-wrapped line boundary. A quote that matches neither is kept as a
``verified: False`` entry -- never guessed, never silently dropped.
"""

from collections.abc import Mapping

#: The model-supplied synthesis field carrying 1-3 short verbatim excerpts the
#: reference answer is grounded in; the parser turns these into located
#: :data:`_SUPPORT_KEY` provenance entries. Absent when the model omits it.
_SUPPORT_QUOTES_KEY = "support_quotes"

#: The candidate key carrying the located provenance spans (the review-app
#: contract). Omitted entirely when the model returned no usable quote -- the
#: app treats absence and an empty list identically via ``candidate.get(...)``.
_SUPPORT_KEY = "support"

#: Field names of one located/unlocated support entry. A located entry carries
#: all five; an unlocated one carries only quote/page/verified (``verified``
#: ``False``, no line numbers) -- never a guessed range.
_SUPPORT_QUOTE_KEY = "quote"
_SUPPORT_PAGE_KEY = "page"
_SUPPORT_LINE_START_KEY = "line_start"
_SUPPORT_LINE_END_KEY = "line_end"
_SUPPORT_VERIFIED_KEY = "verified"


def _build_support(
    payload: Mapping[str, object], page_name: str, page_raw: str
) -> list[dict[str, object]]:
    """Locate each model-supplied support quote in ``page_raw`` (best-effort provenance).

    Tolerant on both axes: an absent or non-list ``support_quotes`` yields no
    entries, and a malformed individual entry (a non-string or blank quote) is
    skipped rather than raising -- provenance is a nice-to-have that must never
    fail an otherwise-good candidate. A quote that cannot be located is kept as a
    ``verified: False`` entry (never guessed, never dropped silently); only shape
    noise is discarded.
    """
    quotes = payload.get(_SUPPORT_QUOTES_KEY)
    if not isinstance(quotes, (list, tuple)):
        return []
    return [
        _support_entry(quote, page_name, page_raw)
        for quote in quotes
        if isinstance(quote, str) and quote.strip()
    ]


def _support_entry(quote: str, page_name: str, page_raw: str) -> dict[str, object]:
    """One located/unlocated provenance entry for ``quote`` (the review-app contract)."""
    span = _locate_span(page_raw, quote)
    if span is None:
        return {
            _SUPPORT_QUOTE_KEY: quote,
            _SUPPORT_PAGE_KEY: page_name,
            _SUPPORT_VERIFIED_KEY: False,
        }
    line_start, line_end = span
    return {
        _SUPPORT_QUOTE_KEY: quote,
        _SUPPORT_PAGE_KEY: page_name,
        _SUPPORT_LINE_START_KEY: line_start,
        _SUPPORT_LINE_END_KEY: line_end,
        _SUPPORT_VERIFIED_KEY: True,
    }


def _locate_span(raw: str, quote: str) -> tuple[int, int] | None:
    """Locate ``quote`` in ``raw`` and return its 1-based inclusive line range.

    A two-rung matching ladder, first-occurrence-wins on each rung: an exact
    substring hit first; then a whitespace-normalized hit (runs of whitespace and
    newlines collapsed to a single space on both sides), which recovers a quote
    the model copied across a soft-wrapped line boundary, mapped back to the real
    offsets in ``raw``. Returns ``None`` when neither rung matches -- the caller
    records that as an unverified entry rather than guessing a range.
    """
    exact = raw.find(quote)
    if exact != -1:
        return _line_range(raw, exact, exact + len(quote) - 1)
    normalized_quote = _normalize_whitespace(quote)
    if not normalized_quote:
        return None
    normalized_raw, offsets = _normalize_whitespace_with_offsets(raw)
    hit = normalized_raw.find(normalized_quote)
    if hit == -1:
        return None
    last = hit + len(normalized_quote) - 1
    return _line_range(raw, offsets[hit], offsets[last])


def _line_range(raw: str, first_index: int, last_index: int) -> tuple[int, int]:
    """The 1-based inclusive ``(start, end)`` lines spanning ``raw``'s [first, last] chars."""
    return raw.count("\n", 0, first_index) + 1, raw.count("\n", 0, last_index) + 1


def _normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace (newlines included) to one space; strip the ends."""
    return " ".join(text.split())


def _normalize_whitespace_with_offsets(raw: str) -> tuple[str, list[int]]:
    """Whitespace-normalize ``raw``, returning the result and a per-char index map.

    ``offsets[i]`` is the index in ``raw`` of the character that produced
    ``normalized[i]``; a collapsed whitespace run maps to the index of its first
    whitespace character. The map lets :func:`_locate_span` translate a
    normalized-space match back to real ``raw`` offsets for the line-range count.
    """
    normalized: list[str] = []
    offsets: list[int] = []
    in_whitespace = False
    for index, char in enumerate(raw):
        if char.isspace():
            if not in_whitespace:
                normalized.append(" ")
                offsets.append(index)
                in_whitespace = True
            continue
        normalized.append(char)
        offsets.append(index)
        in_whitespace = False
    return "".join(normalized), offsets
