"""The anchor resolution ladder -- rungs 0-9, the ceiling for this phase.

A pure function of two text blobs, an :class:`~knotica.core.notes.anchor.AnchorRecord`,
and two threshold floats: no store, no vault handle, no lock, no write, no config read. That
purity is the entire point of the bi-partite anchor design -- resolution is free and
re-runnable against any HEAD, so a resolver improvement applies retroactively to every note
without touching a single file. Thresholds are the caller's job to resolve (from
``[notes]`` config); this module only ever compares against the floats it is handed.

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
   ``exact`` at ``span`` fidelity, score ``1.0``. Stop.
4. The quote occurs verbatim at a different offset (proximity to the historical offset
   disambiguates repeats) -- ``shifted`` at ``span`` fidelity, score ``1.0``. Stop.
5. **Keyword candidate generation** (:func:`knotica.core.notes.candidates.generate_candidates`)
   proposes a bounded set of plausible spans in ``head_text``, seeded from the quote's own
   rarest words. An empty set (the quote shares no vocabulary with the page at all) is a
   normal outcome, not an error -- it simply means no raw score can be computed.
6. **Scoring** (:func:`knotica.core.notes.scoring.score_candidates`) argmaxes the Hypothesis
   -weighted similarity over the candidate set.
7. ``score >= guess_threshold`` -- ``fuzzy`` at ``span`` fidelity, the candidate span carries
   the placement and ``best_guess`` stays ``None`` (``span`` already claims a location;
   ``best_guess`` would duplicate it under a contract that means the opposite -- "might be
   here, not claiming it"). The span reported is the scorer's *aligned* sub-span -- the
   region of the winning window the quote actually matches, which is the region the score
   describes -- not the wider sentence-bounded window rung 5 proposed it inside. It tracks
   the passage as it now reads, so a passage reworded longer yields a longer span; it is
   still a similarity match, not a character-exact one. Stop.
8. The historical enclosing heading -- the nearest heading line at or above the historical
   offset in ``historical_text``, any level -- still exists as a heading in ``head_text`` at
   HEAD (any level; the first match wins) -- ``orphaned`` at ``section`` fidelity.
   ``best_guess`` is the *surviving section's own span* (from its heading line to the next
   heading of any level, or end of text) -- structural evidence from the heading match, not a
   similarity window. ``score`` is ``min(raw_score, guess_threshold - CLAMP_EPSILON)`` when
   candidate scoring produced a raw score, or exactly ``guess_threshold - CLAMP_EPSILON`` when
   it did not (the quote shared no vocabulary with the page at all, so scoring never ran) --
   either way strictly below ``guess_threshold``, so a low-confidence match is never silently
   treated as good enough. Stop.
9. The heading is gone too, but a raw score exists and ``score >= complete_orphan_threshold``
   -- ``orphaned`` at ``page`` fidelity, ``best_guess`` is the scorer's own argmax candidate,
   aligned the same way rung 7's span is (no structural evidence this time, only the
   best-scoring similarity).
10. Otherwise -- ``orphaned`` at ``page`` fidelity, ``best_guess: None`` (a garbage guess is
    worse than no guess), but a score is still computed and reported when one exists. When no
    candidate was ever scored (rung 5 returned nothing) *and* no heading survived either,
    :data:`_NO_CANDIDATE_FLOOR_SCORE` (``0.0``, the scorer's own theoretical floor) is reported
    instead of ``None`` -- see that constant's docstring for why.

``Projection.fidelity`` is ``None`` exactly when ``status == "anchor-invalid"``: that status
means nothing was ever located, so no fidelity claim -- not even ``"topic"`` -- is honest to
make about the record. ``unanchored`` carries ``"topic"`` fidelity like ``orphaned`` does, so
it is unaffected by that pairing.

``score is None`` if and only if ``fidelity is None or fidelity == "topic"`` -- the axis that
actually governs it is whether candidate scoring could possibly have run, not an enumeration
of statuses: ``unanchored``, ``anchor-invalid``, and ``orphaned``/``topic`` (page gone at
HEAD) never reach the scoring rungs at all, and every other fidelity carries a computed value
-- including an ``orphaned``/``page`` result with no guess, which differs from its guessed
sibling in whether a guess is *shown*, not in whether a score was *computed*.
``best_guess is not None`` implies ``score is not None`` throughout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from knotica.core.notes import candidates, scoring
from knotica.core.notes.anchor import AnchorRecord

__all__ = ["CLAMP_EPSILON", "Projection", "resolve_anchor"]

ProjectionStatus = Literal["exact", "shifted", "fuzzy", "orphaned", "unanchored", "anchor-invalid"]
ProjectionFidelity = Literal["span", "section", "page", "topic"]

#: The buffer subtracted from ``guess_threshold`` at rung 8, so an
#: ``orphaned``/``section`` score always renders strictly below
#: ``guess_threshold`` -- never silently reclassified as ``fuzzy`` by a
#: boundary-value bug -- and is legible in a review UI (``0.74`` rather than
#: ``0.7499999999999999``). A named constant, not a float-ulp difference.
CLAMP_EPSILON = 0.01

#: Reported at rung 9/10 when candidate scoring never ran at all (the quote
#: shared no vocabulary with the page -- rung 5 returned nothing) *and* no
#: heading survived either, so there is no raw score to fall back on.
#: ``0.0`` -- never ``None``. Two reasons, not one:
#:
#: 1. ``fidelity == "page"`` may never carry ``score: None`` (see the
#:    module's nullability invariant), so the branch must report
#:    *something*, and ``0.0`` is the scorer's own theoretical floor --
#:    every similarity ratio and the position term are bounded ``>= 0.0``.
#: 2. It is honest, not a placeholder standing in for "unknown": zero
#:    candidates means zero lexical overlap survives anywhere on the page --
#:    a stronger, more informative signal than a weak candidate that was
#:    found and scored low. An ``orphaned``/``page`` result at ``0.0``
#:    (total rewrite, nothing recognisable) and one at, say, ``0.21`` (a
#:    weak match found and rejected) are materially different measurements,
#:    and collapsing both to ``None`` would erase that difference from the
#:    score distribution this phase is meant to gather.
#:
#: It always falls below any positive ``complete_orphan_threshold``, landing
#: on the no-guess branch exactly as "nothing to go on" should.
_NO_CANDIDATE_FLOOR_SCORE = 0.0

_HEADING_LINE_RE = re.compile(r"^#{1,6}[ \t]+.*$", re.MULTILINE)


@dataclass(frozen=True)
class Projection:
    """The resolved placement of one anchor of record, or its invalidity.

    ``fidelity`` is ``None`` if and only if ``status == "anchor-invalid"`` -- enforced below
    rather than left to convention, since a consumer trusting an incidental ``"topic"`` value
    on a corrupt record would render a false claim ("pinned at topic level") about an anchor
    that was never valid in the first place.

    ``score`` is ``None`` if and only if ``fidelity is None or fidelity == "topic"`` --
    scoring only ever runs once a page exists to score candidates against. ``best_guess``,
    when populated, always carries an accompanying ``score``.

    ``score_measured`` says whether ``score`` is an actual comparison or a sentinel the
    ladder had to supply. It is ``True`` for a verbatim hit (``1.0`` is exact) and wherever
    candidate scoring produced a raw value; it is ``False`` at rung 8's no-candidate clamp
    and at rung 10's :data:`_NO_CANDIDATE_FLOOR_SCORE`, where nothing was compared at all.
    Consumers that *render* the score to a human must not present an unmeasured value as a
    similarity percentage: the rung-8 clamp is ``guess_threshold - CLAMP_EPSILON``, which is
    a **ceiling**, so the case with the least evidence would otherwise display the highest
    confidence any orphan can show. ``score_measured`` implies ``score is not None``.
    """

    status: ProjectionStatus
    fidelity: ProjectionFidelity | None
    span: tuple[int, int] | None
    score: float | None
    best_guess: tuple[int, int] | None
    score_measured: bool = False

    def __post_init__(self) -> None:
        is_invalid = self.status == "anchor-invalid"
        if is_invalid and self.fidelity is not None:
            raise ValueError("An anchor-invalid projection must carry no fidelity.")
        if not is_invalid and self.fidelity is None:
            raise ValueError(f"A {self.status!r} projection must carry a fidelity.")

        score_must_be_none = self.fidelity is None or self.fidelity == "topic"
        if score_must_be_none and self.score is not None:
            raise ValueError(f"A {self.status!r}/{self.fidelity!r} projection must carry no score.")
        if not score_must_be_none and self.score is None:
            raise ValueError(f"A {self.status!r}/{self.fidelity!r} projection must carry a score.")

        if self.best_guess is not None and self.score is None:
            raise ValueError("A projection with a best_guess must carry a score.")

        if self.score_measured and self.score is None:
            raise ValueError("A projection with no score cannot report a measured one.")


def resolve_anchor(
    historical_text: str,
    head_text: str | None,
    anchor: AnchorRecord,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> Projection:
    """Resolve ``anchor`` against its historical and current text -- the full ladder."""
    if not anchor.page:
        return _projection("unanchored", "topic")

    historical_offset = _locate_historical(historical_text, anchor.quote, anchor.start)
    if historical_offset is None:
        return _projection("anchor-invalid", None)

    if not head_text:
        return _projection("orphaned", "topic")

    quote_length = len(anchor.quote)
    if head_text[historical_offset : historical_offset + quote_length] == anchor.quote:
        span = (historical_offset, historical_offset + quote_length)
        return _projection("exact", "span", span=span, score=1.0, score_measured=True)

    nearest_offset = _nearest_occurrence(head_text, anchor.quote, historical_offset)
    if nearest_offset is not None:
        span = (nearest_offset, nearest_offset + quote_length)
        return _projection("shifted", "span", span=span, score=1.0, score_measured=True)

    return _resolve_by_similarity(
        historical_text,
        head_text,
        anchor,
        historical_offset,
        guess_threshold,
        complete_orphan_threshold,
    )


def _resolve_by_similarity(
    historical_text: str,
    head_text: str,
    anchor: AnchorRecord,
    historical_offset: int,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> Projection:
    """Rungs 5-10: keyword candidates, scoring, and the graded-orphan bands."""
    quote = anchor.quote
    window = scoring.CONTEXT_WINDOW
    prefix = historical_text[max(0, historical_offset - window) : historical_offset]
    suffix = historical_text[
        historical_offset + len(quote) : historical_offset + len(quote) + window
    ]

    candidate_windows = candidates.generate_candidates(quote, head_text)
    scored = scoring.score_candidates(
        candidate_windows, head_text, quote, prefix, suffix, historical_offset
    )
    raw_span, raw_score = scored if scored is not None else (None, None)

    if raw_score is not None and raw_score >= guess_threshold:
        return _projection("fuzzy", "span", span=raw_span, score=raw_score, score_measured=True)

    heading = _historical_enclosing_heading(historical_text, historical_offset)
    section_span = _surviving_section_span(head_text, heading) if heading is not None else None
    if section_span is not None:
        clamp_ceiling = guess_threshold - CLAMP_EPSILON
        clamped_score = min(raw_score, clamp_ceiling) if raw_score is not None else clamp_ceiling
        # A structural floor, not merely a config-layer concern: guess_threshold=0.0 is a
        # value the config accepts on its own (the pair-coherence check lives one layer up,
        # in notes_config.py -- this module is deliberately config-free and takes raw floats
        # from any caller), which would otherwise clamp to a negative score here.
        clamped_score = max(0.0, clamped_score)
        return _projection(
            "orphaned",
            "section",
            score=clamped_score,
            best_guess=section_span,
            score_measured=raw_score is not None,
        )

    page_score = raw_score if raw_score is not None else _NO_CANDIDATE_FLOOR_SCORE
    measured = raw_score is not None
    if page_score >= complete_orphan_threshold:
        return _projection(
            "orphaned", "page", score=page_score, best_guess=raw_span, score_measured=measured
        )
    return _projection("orphaned", "page", score=page_score, score_measured=measured)


def _projection(
    status: ProjectionStatus,
    fidelity: ProjectionFidelity | None,
    *,
    span: tuple[int, int] | None = None,
    score: float | None = None,
    best_guess: tuple[int, int] | None = None,
    score_measured: bool = False,
) -> Projection:
    return Projection(
        status=status,
        fidelity=fidelity,
        span=span,
        score=score,
        best_guess=best_guess,
        score_measured=score_measured,
    )


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


def _historical_enclosing_heading(historical_text: str, historical_offset: int) -> str | None:
    """The text of the nearest heading line at or above ``historical_offset``.

    Regardless of level -- the innermost enclosing section. ``None`` when the quote sits
    above every heading line in ``historical_text`` (or there are none at all), in which case
    rung 8 cannot fire.
    """
    enclosing: str | None = None
    for match in _HEADING_LINE_RE.finditer(historical_text):
        if match.start() > historical_offset:
            break
        enclosing = _heading_text(match.group(0))
    return enclosing


def _surviving_section_span(head_text: str, heading_text: str) -> tuple[int, int] | None:
    """The span of the surviving section, or ``None`` if the heading is gone from HEAD.

    The first heading line in ``head_text`` (any level) whose text equals ``heading_text``,
    through the next heading line of any level, or through the end of ``head_text`` when none
    follows -- a section "survives" at any level, so a heading demoted from ``##`` to ``###``
    is still that section.
    """
    heading_lines = list(_HEADING_LINE_RE.finditer(head_text))
    for index, match in enumerate(heading_lines):
        if _heading_text(match.group(0)) != heading_text:
            continue
        end = heading_lines[index + 1].start() if index + 1 < len(heading_lines) else len(head_text)
        return match.start(), end
    return None


def _heading_text(line: str) -> str:
    """A heading line's text, with the leading ``#`` markers and whitespace stripped."""
    return line.lstrip("#").strip()
