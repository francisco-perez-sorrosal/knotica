"""The anchor resolution ladder -- steps 0-3 only (Phase 1's ceiling).

A pure function of two text blobs and an :class:`~knotica.core.notes.anchor.AnchorRecord`:
no store, no vault handle, no lock, no write. That purity is the entire point of the
bi-partite anchor design -- resolution is free and re-runnable against any HEAD, so a
resolver improvement applies retroactively to every note without touching a single file.

The ladder, in order:

0. **No page was ever claimed.** ``anchor.page`` is empty -- the anchor never pointed at
   anything, whether because the capture had no quote at all, the claimed page could not be
   read, or the quote matched several claimed pages at once. There is no page to read history
   from, so none of the later rungs are meaningful: reported as ``unanchored`` at ``topic``
   fidelity, checked first and regardless of whether ``anchor.quote`` happens to be non-empty
   (a degraded capture preserves the quote for readability; that does not make it locatable).
   This is distinct from ``orphaned``, which means a page *was* claimed and something about it
   is now gone -- an anchor that never pointed at a page has not lost anything.
1. **Historical resolution.** Locate ``anchor.quote`` in ``historical_text`` -- the blob as
   it stood at ``anchor.pinned_at`` -- disambiguated by ``anchor.start`` when the quote
   repeats. Failure here means the anchor was never valid (hand-edited or forged): reported
   as ``anchor-invalid``, a data-integrity outcome distinct from "the wiki moved on" and
   checked before any comparison against ``head_text`` is attempted.
2. ``head_text`` is missing or empty (the page was deleted or renamed) -- ``orphaned`` at
   ``topic`` fidelity. Stop.
3. The quote occurs verbatim in ``head_text`` at the same offset it held historically --
   ``exact`` at ``span`` fidelity. Stop.
4. The quote occurs verbatim at a different offset (proximity to the historical offset
   disambiguates repeats) -- ``shifted`` at ``span`` fidelity. Stop.
5. Otherwise the page is intact but the quote is gone -- ``orphaned`` at ``page`` fidelity,
   with no best-guess span. Phase 2 adds fuzzy matching (keyword candidates, similarity
   scoring) past this point; Phase 1 stops here deliberately -- an absent capability is
   simpler than a stub that lies about being tested.

``Projection.fidelity`` is ``None`` exactly when ``status == "anchor-invalid"``: that status
means nothing was ever located, so no fidelity claim -- not even ``"topic"`` -- is honest to
make about the record. The pairing is enforced structurally, not by convention. ``unanchored``
carries ``"topic"`` fidelity like ``orphaned`` does, so it is unaffected by that pairing.
"""

from dataclasses import dataclass
from typing import Literal

from knotica.core.notes.anchor import AnchorRecord

__all__ = ["Projection", "resolve_anchor"]

ProjectionStatus = Literal["exact", "shifted", "orphaned", "unanchored", "anchor-invalid"]
ProjectionFidelity = Literal["span", "page", "topic"]


@dataclass(frozen=True)
class Projection:
    """The resolved placement of one anchor of record, or its invalidity.

    ``fidelity`` is ``None`` if and only if ``status == "anchor-invalid"`` -- enforced below
    rather than left to convention, since a consumer trusting an incidental ``"topic"`` value
    on a corrupt record would render a false claim ("pinned at topic level") about an anchor
    that was never valid in the first place.
    """

    status: ProjectionStatus
    fidelity: ProjectionFidelity | None
    span: tuple[int, int] | None

    def __post_init__(self) -> None:
        is_invalid = self.status == "anchor-invalid"
        if is_invalid and self.fidelity is not None:
            raise ValueError("An anchor-invalid projection must carry no fidelity.")
        if not is_invalid and self.fidelity is None:
            raise ValueError(f"A {self.status!r} projection must carry a fidelity.")


def resolve_anchor(historical_text: str, head_text: str | None, anchor: AnchorRecord) -> Projection:
    """Resolve ``anchor`` against its historical and current text -- ladder steps 0-3."""
    if not anchor.page:
        return Projection(status="unanchored", fidelity="topic", span=None)

    historical_offset = _locate_historical(historical_text, anchor.quote, anchor.start)
    if historical_offset is None:
        return Projection(status="anchor-invalid", fidelity=None, span=None)

    if not head_text:
        return Projection(status="orphaned", fidelity="topic", span=None)

    quote_length = len(anchor.quote)
    if head_text[historical_offset : historical_offset + quote_length] == anchor.quote:
        span = (historical_offset, historical_offset + quote_length)
        return Projection(status="exact", fidelity="span", span=span)

    nearest_offset = _nearest_occurrence(head_text, anchor.quote, historical_offset)
    if nearest_offset is not None:
        span = (nearest_offset, nearest_offset + quote_length)
        return Projection(status="shifted", fidelity="span", span=span)

    return Projection(status="orphaned", fidelity="page", span=None)


def _locate_historical(text: str, quote: str, start: int | None) -> int | None:
    """The offset ``quote`` occupies in the historical blob, or ``None`` if never valid."""
    if start is not None:
        return start if text[start : start + len(quote)] == quote else None
    offset = text.find(quote)
    return offset if offset != -1 else None


def _nearest_occurrence(text: str, quote: str, reference_offset: int) -> int | None:
    """The occurrence of ``quote`` in ``text`` closest to ``reference_offset``, if any."""
    offsets = _occurrences(text, quote)
    if not offsets:
        return None
    return min(offsets, key=lambda offset: abs(offset - reference_offset))


def _occurrences(text: str, quote: str) -> list[int]:
    """Every start offset at which ``quote`` occurs in ``text``, in order."""
    offsets: list[int] = []
    start = 0
    while True:
        found = text.find(quote, start)
        if found == -1:
            return offsets
        offsets.append(found)
        start = found + 1
