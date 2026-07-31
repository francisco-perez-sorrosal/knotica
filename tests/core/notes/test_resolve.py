"""Behavioral contract of the anchor resolution ladder.

Derived from the notes-overlay resolution ladder, not from any
implementation. ``resolve_anchor`` is a pure function of two text blobs, an
anchor of record, and two threshold floats -- it takes no store, no vault
handle, and produces no writes. Given
``(historical_text, head_text, anchor, guess_threshold, complete_orphan_threshold)``
it answers one of these outcomes:

- the quote is found in ``head_text`` at the same offset it held in
  ``historical_text`` -- ``exact``, ``span`` fidelity, score ``1.0``;
- the quote is found in ``head_text`` at a different offset (the nearest
  occurrence to the historical offset wins when the quote repeats) --
  ``shifted``, ``span`` fidelity, score ``1.0``;
- the page itself is gone at HEAD (an absent or empty ``head_text``, standing
  in for "this page was deleted or renamed") -- ``orphaned``, ``topic``
  fidelity, no guessed span;
- the quote is gone verbatim but a keyword-seeded candidate scores at or
  above ``guess_threshold`` -- ``fuzzy``, ``span`` fidelity, the candidate
  span carries the placement and ``best_guess`` stays ``None`` (``span``
  already claims a location; ``best_guess`` would duplicate it under a
  contract that means the opposite -- "might be here, not claiming it");
- a candidate scores below ``guess_threshold`` but the historical enclosing
  heading still exists at HEAD -- ``orphaned``, ``section`` fidelity,
  ``best_guess`` is the *surviving section's own span* (structural evidence
  from the heading match, not a similarity window), and ``score`` is
  ``min(raw_score, guess_threshold - CLAMP_EPSILON)`` when candidate scoring
  produced a raw score, or exactly ``guess_threshold - CLAMP_EPSILON`` when
  it did not (the quote shared no vocabulary with the page at all, so
  scoring never ran) -- either way strictly below ``guess_threshold`` so a
  low-confidence match is never silently treated as good enough;
- the heading is gone too, but the candidate still scores at or above
  ``complete_orphan_threshold`` -- ``orphaned``, ``page`` fidelity,
  ``best_guess`` is the scorer's own argmax candidate window (no structural
  evidence this time, only the best-scoring similarity);
- the candidate scores below ``complete_orphan_threshold`` -- ``orphaned``,
  ``page`` fidelity, ``best_guess: None`` -- a garbage guess is worse than no
  guess, but a score was still computed and is still reported.

A further outcome exists for a different failure entirely: the quote is not
even in the *historical* text the anchor claims to pin. That is not "the
wiki moved on" -- it is "this anchor was never valid," a hand-corrupted or
forged record. It is reported as ``anchor-invalid``, is checked before any
comparison against ``head_text`` is even attempted, and must never collapse
into the ``orphaned`` outcomes above: the two mean opposite things to a
reader deciding whether to trust their own reflection.

A final outcome covers an anchor that never claimed a page at all --
``unanchored``, ``topic`` fidelity -- checked before anything else runs.

``score is None`` if and only if ``fidelity is None`` (``anchor-invalid``) or
``fidelity == "topic"`` (``unanchored``, or ``orphaned`` with the page gone at
HEAD) -- the axis that actually governs it is whether candidate scoring could
possibly have run, not an enumeration of statuses. Every other fidelity
carries a computed value, including an ``orphaned``/``page`` result with no
guess -- that case and its guessed sibling differ in whether a guess is
*shown*, not in whether a score was *computed*. ``best_guess is not None``
implies ``score is not None`` throughout.
"""

import dataclasses
import inspect
import re

from knotica.core.notes import candidates, scoring
from knotica.core.notes.anchor import AnchorRecord
from knotica.core.notes.resolve import Projection, resolve_anchor

# SYSTEMS_PLAN's stated defaults. Most fixtures below resolve at rungs 0-3
# and never consult these at all; a couple of fallthrough fixtures do reach
# the scoring path and happen to land within these real bands (verified
# against the shipped scorer via `_score_the_real_way`, not guessed).
_STANDARD_GUESS_THRESHOLD = 0.75
_STANDARD_COMPLETE_ORPHAN_THRESHOLD = 0.35


def _anchor(**overrides: object) -> AnchorRecord:
    """A minimal, valid anchor; override only the fields a test cares about."""
    defaults: dict[str, object] = {
        "page": "agentic-systems/agent-memory.md",
        "heading": "Working memory",
        "fidelity": "span",
        "pinned_at": "9f1a3c0",
        "quote": "the model has no persistent notion of the goal it is optimizing for",
        "start": None,
    }
    defaults.update(overrides)
    return AnchorRecord(**defaults)


def _occurrences(text: str, needle: str) -> list[int]:
    """Every start offset at which ``needle`` occurs in ``text``, in order."""
    positions = []
    start = 0
    while True:
        found = text.find(needle, start)
        if found == -1:
            return positions
        positions.append(found)
        start = found + 1


def _score_the_real_way(
    historical_text: str, quote: str, head_text: str
) -> tuple[tuple[int, int], float] | None:
    """The ``(span, score)`` the shipped candidate generator and scorer compute.

    ``resolve_anchor`` is specified to delegate to exactly
    :func:`knotica.core.notes.candidates.generate_candidates` and
    :func:`knotica.core.notes.scoring.score_candidates` at the ladder's
    keyword-candidate and scoring rungs, extracting the anchor's historical
    prefix/suffix with :data:`knotica.core.notes.scoring.CONTEXT_WINDOW`
    (rung 0's job). Computing the same result independently here gives each
    fixture below a real, algorithm-derived oracle to assert against instead
    of a hand-guessed number -- a fixture whose expected span/score is
    computed this way fails a test only when ``resolve_anchor`` disagrees
    with the very functions it is specified to call.
    """
    historical_offset = historical_text.index(quote)
    window = scoring.CONTEXT_WINDOW
    prefix = historical_text[max(0, historical_offset - window) : historical_offset]
    suffix = historical_text[
        historical_offset + len(quote) : historical_offset + len(quote) + window
    ]
    windows = candidates.generate_candidates(quote, head_text)
    return scoring.score_candidates(windows, head_text, quote, prefix, suffix, historical_offset)


_HEADING_LINE_RE = re.compile(r"^#{1,6}[ \t]+.*$", re.MULTILINE)


def _section_span(head_text: str, heading_text: str) -> tuple[int, int]:
    """The surviving section's span the way an ``orphaned``/``section`` result
    is specified to compute it: the first heading line at HEAD (any level)
    whose text equals ``heading_text``, through the next heading line of any
    level, or through the end of ``head_text`` when none follows.
    """
    heading_lines = list(_HEADING_LINE_RE.finditer(head_text))
    for index, match in enumerate(heading_lines):
        text = match.group(0).lstrip("#").strip()
        if text == heading_text:
            end = (
                heading_lines[index + 1].start()
                if index + 1 < len(heading_lines)
                else len(head_text)
            )
            return match.start(), end
    raise AssertionError(f"heading {heading_text!r} was not found as a heading line in head_text")


# ---------------------------------------------------------------------------
# Exact
# ---------------------------------------------------------------------------


def test_quote_unchanged_at_the_recorded_offset_resolves_exact():
    quote = "the model has no persistent notion of the goal it is optimizing for"
    text = (
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon."
    )
    offset = text.index(quote)
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(
        text,
        text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "exact"
    assert projection.fidelity == "span"
    assert projection.span == (offset, offset + len(quote))
    assert projection.score == 1.0
    assert projection.best_guess is None


def test_quote_at_the_very_start_of_the_page_resolves_exact():
    quote = "reward hacking is just goodhart with extra steps"
    text = f"{quote}\n\nA page that opens directly with the pinned quote, no preamble."
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(
        text,
        text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "exact"
    assert projection.span == (0, len(quote))
    assert projection.score == 1.0


def test_historical_occurrence_disambiguated_by_start_is_the_one_matched_at_head():
    """The quote repeats within the historical blob itself; ``start`` names
    which occurrence was actually captured. Resolution must anchor to that
    occurrence, not to whichever one a naive first-match would find.
    """
    quote = "the model learns to satisfy the metric rather than the goal"
    historical_text = (
        f"Early draft aside, since discarded: {quote}\n\n"
        "The argument develops with more nuance across several paragraphs here.\n\n"
        f"{quote}\n\n"
        "Closing remarks on the final version of the argument."
    )
    occurrences = _occurrences(historical_text, quote)
    assert len(occurrences) == 2, "fixture must actually contain the quote twice"
    captured_offset = occurrences[1]
    anchor = _anchor(quote=quote, start=captured_offset)

    projection = resolve_anchor(
        historical_text,
        historical_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "exact"
    assert projection.span == (captured_offset, captured_offset + len(quote))


# ---------------------------------------------------------------------------
# Shifted
# ---------------------------------------------------------------------------


def test_quote_moved_by_an_inserted_paragraph_above_it_resolves_shifted():
    quote = "the model learns to satisfy the metric rather than the goal"
    historical_text = (
        "Preface paragraph about incentives and Goodhart's law.\n\n"
        f"{quote}\n\n"
        "Closing thoughts on reward hacking."
    )
    head_text = (
        "Preface paragraph about incentives and Goodhart's law.\n\n"
        "A new paragraph inserted here after a later editing pass, expanding on "
        "the background before the original point is made.\n\n"
        f"{quote}\n\n"
        "Closing thoughts on reward hacking."
    )
    historical_offset = historical_text.index(quote)
    head_offset = head_text.index(quote)
    assert head_offset != historical_offset, "fixture must actually shift the offset"
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "shifted"
    assert projection.fidelity == "span"
    assert projection.span == (head_offset, head_offset + len(quote))
    assert projection.score == 1.0
    assert projection.best_guess is None


def test_quote_repeated_at_head_resolves_to_the_occurrence_nearest_the_historical_offset():
    """The quote now occurs twice at HEAD: once as an early, unrelated
    citation, and once at roughly its original location. Proximity to the
    historical offset -- not simply the first match in the text -- must pick
    the latter.
    """
    quote = "the model learns to satisfy the metric rather than the goal"
    historical_text = (
        "Section one sets up the incentive structure under discussion here, laying out "
        "several claims that will matter for what follows across the remaining sections "
        "of this particular note.\n\n"
        f"{quote}\n\n"
        "Section three draws out the implications for practice."
    )
    historical_offset = historical_text.index(quote)
    head_text = (
        f'Section one opens by quoting an earlier draft: "{quote}" as a foreword.\n\n'
        "Some connecting material was added during a later revision explaining "
        "additional context for the reader before the original point returns.\n\n"
        f"{quote}\n\n"
        "Section three draws out the implications for practice."
    )
    occurrences = _occurrences(head_text, quote)
    assert len(occurrences) == 2, "fixture must actually repeat the quote at head"
    nearest_offset = min(occurrences, key=lambda offset: abs(offset - historical_offset))
    assert nearest_offset != occurrences[0], (
        "fixture must make the nearest occurrence differ from the first occurrence, "
        "otherwise proximity selection is indistinguishable from naive first-match"
    )
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "shifted"
    assert projection.span == (nearest_offset, nearest_offset + len(quote))


# ---------------------------------------------------------------------------
# Unanchored -- no page was ever claimed (checked before anything else)
# ---------------------------------------------------------------------------


def test_an_anchor_with_no_page_resolves_unanchored_at_topic_fidelity_with_no_span():
    anchor = _anchor(page="", quote="")

    projection = resolve_anchor(
        "",
        None,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "unanchored"
    assert projection.fidelity == "topic"
    assert projection.span is None
    assert projection.score is None
    assert projection.best_guess is None


def test_an_anchor_with_no_page_but_a_preserved_quote_still_resolves_unanchored():
    """A degraded capture keeps the quote verbatim for readability -- that does
    not make the anchor locatable, so it must resolve the same as a bare topic
    pin with no quote at all.
    """
    anchor = _anchor(page="", quote="a passage preserved for readability only")

    projection = resolve_anchor(
        "",
        None,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "unanchored"
    assert projection.fidelity == "topic"
    assert projection.span is None
    assert projection.score is None


def test_a_pageless_anchor_resolves_unanchored_even_when_the_quote_is_nowhere_to_be_found():
    """Historical resolution must never run for a pageless anchor -- there is
    no page to have read history from, so even a quote that would fail the
    historical-lookup rung (and land on ``anchor-invalid`` for a real anchor)
    must not leak that outcome here.
    """
    anchor = _anchor(page="", quote="a quote that appears nowhere in the historical blob")

    projection = resolve_anchor(
        "completely unrelated historical text",
        None,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "unanchored"
    assert projection.fidelity == "topic"
    assert projection.span is None
    assert projection.score is None


# ---------------------------------------------------------------------------
# Orphaned -- page gone at HEAD
#
# No scoring is possible when there is no HEAD text to score against.
# ``score``/``best_guess`` are left unasserted in the two tests immediately
# below purely to keep each test focused on the fidelity/span behavior it
# names -- the nullability contract itself (this status carries
# ``fidelity == "topic"`` and therefore ``score is None``, per the settled
# invariant) is pinned once, as its own named behavior, by
# ``test_score_is_none_exactly_when_fidelity_is_none_or_topic`` below.
# ---------------------------------------------------------------------------


def test_deleted_page_signalled_by_none_resolves_orphaned_at_topic_fidelity_with_no_span():
    historical_text = "The page used to say: the metric quietly became the goal itself."
    anchor = _anchor(quote="the metric quietly became the goal itself")

    projection = resolve_anchor(
        historical_text,
        None,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "topic"
    assert projection.span is None


def test_deleted_page_signalled_by_empty_text_resolves_orphaned_at_topic_fidelity():
    historical_text = "The page used to say: the metric quietly became the goal itself."
    anchor = _anchor(quote="the metric quietly became the goal itself")

    projection = resolve_anchor(
        historical_text,
        "",
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "topic"
    assert projection.span is None


# ---------------------------------------------------------------------------
# Orphaned -- page present, quote gone verbatim, but the ladder now keeps
# going past this point (Phase 1 stopped here; Phase 2 does not). With the
# real default thresholds this specific fixture's candidate happens to score
# above `complete_orphan_threshold`, so a guess is now offered where Phase 1
# offered none -- verified against the shipped scorer, not asserted blindly.
# ---------------------------------------------------------------------------


def test_quote_absent_from_an_otherwise_intact_page_resolves_orphaned_at_page_fidelity():
    quote = "the model learns to satisfy the metric rather than the goal"
    historical_text = f"Preface.\n\n{quote}\n\nClosing thoughts."
    head_text = (
        "Preface.\n\nThis paragraph was rewritten entirely and no longer contains "
        "anything resembling the original wording.\n\nClosing thoughts."
    )
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"
    assert projection.span is None
    assert projection.best_guess is not None, (
        "this fixture's candidate scores above complete_orphan_threshold under the "
        "real scorer -- a guess must be offered, not withheld"
    )


# ---------------------------------------------------------------------------
# Fuzzy -- a paraphrased passage scores at or above guess_threshold
# ---------------------------------------------------------------------------


def test_paraphrased_passage_scoring_at_or_above_guess_threshold_resolves_fuzzy():
    quote = "the model has no persistent notion of the goal it is optimizing for"
    historical_text = (
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon."
    )
    paraphrase = "the model retains no persistent notion of the goal it is optimizing for"
    head_text = historical_text.replace(quote, paraphrase)
    assert quote not in head_text, "fixture must not leave the verbatim quote behind"
    anchor = _anchor(quote=quote, heading="")

    oracle = _score_the_real_way(historical_text, quote, head_text)
    assert oracle is not None, "fixture must share seed words with the reworded page"
    oracle_span, oracle_score = oracle
    guess_threshold = 0.52
    assert oracle_score >= guess_threshold, (
        "fixture's real score must clear guess_threshold for this to be a fuzzy case"
    )

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=0.35,
    )

    assert projection.status == "fuzzy"
    assert projection.fidelity == "span"
    assert projection.span == oracle_span
    assert projection.score == oracle_score
    # `span` already claims the placement; `best_guess` means "might be here,
    # not claiming it" -- a fuzzy result must not carry both under one value.
    assert projection.best_guess is None


# ---------------------------------------------------------------------------
# Orphaned -- recoverable band, historical heading still present at HEAD
# ---------------------------------------------------------------------------


def test_reworded_passage_below_guess_threshold_with_heading_intact_resolves_orphaned_at_section():
    quote = "the model has no persistent notion of the goal it is optimizing for"
    heading = "## Working memory"
    historical_text = (
        "# Agent memory\n\n"
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon, spanning a whole extra sentence for length.\n"
    )
    heavier_reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    head_text = historical_text.replace(quote, heavier_reword)
    assert heading in head_text, "fixture must actually keep the heading at HEAD"
    anchor = _anchor(quote=quote, heading="Working memory")

    oracle = _score_the_real_way(historical_text, quote, head_text)
    assert oracle is not None, "fixture must share seed words with the reworded page"
    _oracle_span, oracle_score = oracle
    guess_threshold = 0.60
    assert oracle_score < guess_threshold, (
        "fixture's real score must fall short of guess_threshold, or this collapses "
        "into the fuzzy case instead of exercising the graded-recovery band"
    )

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=0.35,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "section"
    assert projection.score is not None
    assert projection.score < guess_threshold, (
        "an orphaned/section score must be clamped strictly below guess_threshold -- "
        "a boundary-value bug here would silently reclassify this note as fuzzy"
    )
    # Structural evidence, not a similarity window: the surviving section's own
    # span, not the scorer's argmax candidate (that belongs to the page-fidelity
    # sibling below, which has no heading match to lean on).
    assert projection.best_guess == _section_span(head_text, "Working memory")


# ---------------------------------------------------------------------------
# The historical enclosing heading, at its edges: nested levels, and none at
# all. Both cases below are backfill coverage for already-shipped, confirmed-
# correct behavior -- they pass on arrival, and exist to pin it against a
# future regression, not to change anything.
# ---------------------------------------------------------------------------


def test_nested_heading_uses_the_innermost_enclosing_heading_not_a_surviving_ancestor():
    """The quote sits under two heading levels (``# Top`` then ``## Sub``).
    Only ``## Sub`` -- the *innermost* enclosing heading, and the one the
    historical offset is actually nearest to -- is renamed at HEAD; ``# Top``
    survives untouched. Resolution must not fall back to the surviving
    ancestor heading as if it were "close enough" -- the enclosing heading is
    ``## Sub``, it did not survive, so rung 7 cannot fire and this must land
    at ``page`` fidelity, not ``section``.
    """
    quote = "the model has no persistent notion of the goal it is optimizing for"
    historical_text = (
        "# Top\n\n"
        "Some framing prose under the top-level heading before the inner section starts.\n\n"
        "## Sub\n\n"
        f"{quote}\n\n"
        "Trailing remarks closing out this note.\n"
    )
    reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    head_text = historical_text.replace(quote, reword).replace("## Sub", "## Renamed sub-section")
    assert "# Top" in head_text, "fixture must keep the ancestor heading at HEAD"
    assert "## Sub" not in head_text, "fixture must actually rename the innermost heading"

    # Thresholds chosen to straddle this reword's real score, so the fixture
    # falls past the fuzzy rung and reaches the heading check this test is about.
    projection = resolve_anchor(
        historical_text,
        head_text,
        _anchor(quote=quote, heading="Sub"),
        guess_threshold=0.60,
        complete_orphan_threshold=0.20,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"


def test_quote_with_no_enclosing_heading_falls_through_to_page_fidelity():
    """The quote sits above *any* heading on the page -- the only heading
    appears later, well after the historical offset. There is no enclosing
    heading to check for survival at all, so rung 7 can never fire here
    regardless of what happens to the later heading; resolution must fall
    straight through to ``page`` fidelity.
    """
    quote = "the model has no persistent notion of the goal it is optimizing for"
    historical_text = (
        "Some opening prose that appears before any heading exists on this page at all.\n\n"
        f"{quote}\n\n"
        "## Some later heading\n\n"
        "Trailing prose that lives inside the section opened by the later heading above.\n"
    )
    reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    head_text = historical_text.replace(quote, reword)
    assert "## Some later heading" in head_text, "fixture must keep the later heading intact"

    # Thresholds chosen to straddle this reword's real score, so the fixture
    # falls past the fuzzy rung and reaches the heading check this test is about.
    projection = resolve_anchor(
        historical_text,
        head_text,
        _anchor(quote=quote, heading=""),
        guess_threshold=0.60,
        complete_orphan_threshold=0.20,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"


# ---------------------------------------------------------------------------
# Orphaned -- section fidelity, but candidate scoring never ran at all
# ---------------------------------------------------------------------------


def test_orphaned_section_reports_the_clamp_floor_when_no_candidate_scored():
    """The heading may survive even when the quote shares *no* vocabulary at
    all with the page -- keyword-candidate generation then returns nothing to
    score, so there is no raw similarity score to report. The heading match
    is still structural evidence the passage is somewhere in that section, so
    resolution must still land on ``orphaned``/``section`` -- and, since
    ``score`` may never be ``None`` outside ``fidelity in (None, "topic")``,
    it reports the clamp floor itself (``guess_threshold - CLAMP_EPSILON``)
    rather than leaving a ``best_guess`` with no accompanying score.
    """
    quote = "reward hacking undermines interpretability"
    heading = "## Failure modes"
    historical_text = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    head_text = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        "a brief note about scheduling logistics for next quarter's planning cycle\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    assert heading in head_text, "fixture must actually keep the heading at HEAD"
    assert candidates.generate_candidates(quote, head_text) == (), (
        "fixture must share zero vocabulary with the page so candidate generation "
        "returns nothing and scoring never runs -- otherwise this collapses into "
        "the ordinary (has-a-raw-score) section case above"
    )
    anchor = _anchor(quote=quote, heading="Failure modes")
    guess_threshold = 0.52

    from knotica.core.notes.resolve import CLAMP_EPSILON

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=0.35,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "section"
    assert projection.score == guess_threshold - CLAMP_EPSILON
    assert projection.best_guess == _section_span(head_text, "Failure modes")


def test_orphaned_section_clamp_floor_never_goes_negative_at_a_zero_guess_threshold():
    """``guess_threshold = 0.0`` is a value the ``[notes]`` config accepts on
    its own (the pair-coherence check lives at the config layer, not here --
    ``resolve_anchor`` is deliberately config-free and takes raw floats from
    any caller). The clamp ceiling ``guess_threshold - CLAMP_EPSILON`` would
    then be ``-0.01``, outside the ``[0.0, 1.0]`` range every other rung
    honours -- the clamp must floor at ``0.0`` rather than reporting a
    negative score.

    Same fixture shape as the no-raw-score clamp case above (heading
    survives, quote shares no vocabulary with the page at all), only
    ``guess_threshold`` differs -- isolating the floor as the one thing under
    test.
    """
    quote = "reward hacking undermines interpretability"
    heading = "## Failure modes"
    historical_text = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    head_text = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        "a brief note about scheduling logistics for next quarter's planning cycle\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    assert candidates.generate_candidates(quote, head_text) == (), (
        "fixture must share zero vocabulary with the page so scoring never runs"
    )
    anchor = _anchor(quote=quote, heading="Failure modes")

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=0.0,
        complete_orphan_threshold=0.0,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "section"
    assert projection.score == 0.0
    # The L1 invariant this floor exists to preserve, stated explicitly: a
    # `section` fidelity must never carry a `None` score, and must never carry
    # a value outside the range every other rung's score respects.
    assert projection.score is not None
    assert 0.0 <= projection.score <= 1.0


# ---------------------------------------------------------------------------
# Orphaned -- page fidelity, no candidate scored, and no surviving heading
# either. Contrast with the section-fidelity case immediately above: same
# "zero candidates" precondition, opposite structural evidence (no heading
# match instead of a heading match), and a different outcome as a result.
# ---------------------------------------------------------------------------


def test_orphaned_page_reports_the_floor_score_when_no_candidate_scored_and_no_heading_survives():
    """The page-fidelity sibling of the clamp-floor case above: the quote
    shares no vocabulary with the page at all (candidate generation returns
    nothing, so scoring never runs), and unlike the section-fidelity case
    the historical heading does not survive at HEAD either -- there is no
    structural evidence at all, not even a section to point at. Resolution
    lands on ``orphaned``/``page`` with ``best_guess: None``.

    ``page`` fidelity must still carry a score (the nullability contract is
    ``None`` only for ``fidelity in (None, "topic")``), so ``0.0`` is
    reported -- the honest floor, not a placeholder: zero candidates *means*
    no lexical overlap survives anywhere on the page, and keeping that value
    distinct from a low-but-nonzero rejected match preserves a real signal
    a collapse to ``None`` would destroy.
    """
    quote = "reward hacking undermines interpretability"
    heading = "## Failure modes"
    historical_text = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    head_text = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        "## Known issues (renamed)\n\n"
        "a brief note about scheduling logistics for next quarter's planning cycle\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    assert heading not in head_text, "fixture must actually remove the historical heading"
    assert candidates.generate_candidates(quote, head_text) == (), (
        "fixture must share zero vocabulary with the page so candidate generation "
        "returns nothing and scoring never runs"
    )
    anchor = _anchor(quote=quote, heading="Failure modes")

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"
    assert projection.best_guess is None
    assert projection.score == 0.0
    # The invariant this floor value exists to preserve, stated explicitly:
    # a `page` fidelity must never carry a `None` score.
    assert projection.fidelity != "topic" and projection.fidelity is not None
    assert projection.score is not None


# ---------------------------------------------------------------------------
# Orphaned -- recoverable band, heading gone too (page fidelity, with a guess)
# ---------------------------------------------------------------------------


def test_same_recoverable_band_score_with_heading_gone_resolves_orphaned_at_page_with_a_guess():
    quote = "the model has no persistent notion of the goal it is optimizing for"
    heading = "## Working memory"
    historical_text = (
        "# Agent memory\n\n"
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon, spanning a whole extra sentence for length.\n"
    )
    heavier_reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    head_text = historical_text.replace(quote, heavier_reword).replace(
        heading, "## Memory architecture (renamed)"
    )
    assert heading not in head_text, "fixture must actually remove the historical heading"
    anchor = _anchor(quote=quote, heading="Working memory")

    oracle = _score_the_real_way(historical_text, quote, head_text)
    assert oracle is not None, "fixture must share seed words with the reworded page"
    oracle_span, oracle_score = oracle
    guess_threshold = 0.60
    complete_orphan_threshold = 0.20
    assert complete_orphan_threshold <= oracle_score < guess_threshold, (
        "fixture's real score must sit inside the graded-recovery band"
    )

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"
    assert projection.best_guess == oracle_span
    assert projection.score == oracle_score


# ---------------------------------------------------------------------------
# Orphaned -- below complete_orphan_threshold (page fidelity, no guess) --
# a score is still computed and reported even though no guess is shown.
# ---------------------------------------------------------------------------


def test_score_below_complete_orphan_threshold_resolves_orphaned_at_page_with_no_guess():
    quote = "the model has no persistent notion of the goal it is optimizing for"
    heading = "## Working memory"
    historical_text = (
        "# Agent memory\n\n"
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon, spanning a whole extra sentence for length.\n"
    )
    heavier_reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    head_text = historical_text.replace(quote, heavier_reword).replace(
        heading, "## Memory architecture (renamed)"
    )
    anchor = _anchor(quote=quote, heading="Working memory")

    oracle = _score_the_real_way(historical_text, quote, head_text)
    assert oracle is not None, "fixture must share seed words with the reworded page"
    _oracle_span, oracle_score = oracle
    guess_threshold = 0.70
    complete_orphan_threshold = 0.60
    assert oracle_score < complete_orphan_threshold < guess_threshold, (
        "fixture's real score must fall below complete_orphan_threshold for this to "
        "exercise the no-guess rung"
    )

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"
    assert projection.best_guess is None
    # The critical assertion: a withheld guess does not mean a withheld score.
    # A future "tidy-up" that nulls this out on the no-guess path would throw
    # away the real-note score distribution the block-ID decision depends on.
    assert projection.score is not None
    assert projection.score == oracle_score


# ---------------------------------------------------------------------------
# Anchor-invalid -- the historical resolution step itself fails
#
# This is a distinct data-integrity outcome, never an exception and never
# reported as "orphaned": orphaned means the wiki moved on since capture;
# anchor-invalid means the anchor was never valid to begin with (hand-edited
# or forged), and the historical blob itself never contained the quote.
# ---------------------------------------------------------------------------


def test_quote_absent_from_both_historical_and_head_text_is_anchor_invalid_not_orphaned():
    historical_text = "This page never mentioned anything about reward hacking at all."
    head_text = "Nor does the current version of this page mention it."
    anchor = _anchor(quote="the model learns to satisfy the metric rather than the goal")

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "anchor-invalid"
    assert projection.status != "orphaned"
    assert projection.span is None
    assert projection.score is None
    assert projection.best_guess is None


def test_quote_present_at_head_but_absent_from_historical_text_is_still_anchor_invalid():
    """A forged or hand-edited anchor could claim a quote that happens to be
    real at HEAD even though it was never in the pinned historical blob. The
    ladder checks historical resolution first, so this must not be mistaken
    for an ``exact`` or ``shifted`` match just because the string exists
    somewhere in the current page.
    """
    quote = "the model learns to satisfy the metric rather than the goal"
    historical_text = "This page, at the time of pinning, discussed something unrelated."
    head_text = f"The page was later rewritten to include: {quote}"
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(
        historical_text,
        head_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "anchor-invalid"
    assert projection.status != "exact"
    assert projection.status != "shifted"
    assert projection.span is None
    assert projection.score is None
    assert projection.best_guess is None


def test_resolving_an_invalid_anchor_never_raises():
    historical_text = "Nothing here matches anything the anchor claims."
    anchor = _anchor(quote="a quote that was never written on this page")

    # A crash here (an uncaught KeyError/ValueError/etc.) would itself be the
    # failure -- data-integrity problems in hand-edited files are reported as
    # data, never propagated as exceptions.
    projection = resolve_anchor(
        historical_text,
        historical_text,
        anchor,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert projection.status == "anchor-invalid"


# ---------------------------------------------------------------------------
# Multiple anchors resolve independently
# ---------------------------------------------------------------------------


def test_two_anchors_on_the_same_note_resolve_independently_regardless_of_call_order():
    quote_a = "the model has no persistent notion of the goal it is optimizing for"
    quote_b = "this observation about incentives has aged surprisingly well over time"
    historical_text = (
        "Intro paragraph describing the general theme of the reflection at hand.\n\n"
        f"{quote_a}\n\n"
        "Middle paragraph bridging two separate ideas explored in this note.\n\n"
        f"{quote_b}\n\n"
        "Closing remarks wrapping up the reflection for now."
    )
    head_text = historical_text.replace(
        quote_b,
        "a rewritten paragraph that no longer contains the original wording at all",
    )
    anchor_a = _anchor(quote=quote_a)
    anchor_b = _anchor(quote=quote_b)

    first_pass_a = resolve_anchor(
        historical_text,
        head_text,
        anchor_a,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )
    first_pass_b = resolve_anchor(
        historical_text,
        head_text,
        anchor_b,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )
    second_pass_b = resolve_anchor(
        historical_text,
        head_text,
        anchor_b,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )
    second_pass_a = resolve_anchor(
        historical_text,
        head_text,
        anchor_a,
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert first_pass_a.status == "exact"
    assert first_pass_b.status == "orphaned"
    assert first_pass_b.fidelity == "page"
    assert first_pass_a == second_pass_a
    assert first_pass_b == second_pass_b


def test_two_anchors_on_the_same_note_resolve_independently_across_fuzzy_and_orphaned_statuses():
    """The same independence guarantee, exercised across the graded-recovery
    statuses this phase adds: one anchor lands on ``fuzzy``, the other on a
    scored-but-guess-withheld ``orphaned``, and resolving one must not leak
    into or depend on resolving the other.
    """
    quote_a = "the model has no persistent notion of the goal it is optimizing for"
    quote_b = "this observation about incentives has aged surprisingly well over time"
    historical_text = (
        "Intro paragraph describing the general theme of the reflection at hand.\n\n"
        f"{quote_a}\n\n"
        "Middle paragraph bridging two separate ideas explored in this note.\n\n"
        f"{quote_b}\n\n"
        "Closing remarks wrapping up the reflection for now."
    )
    paraphrase_a = "the model retains no persistent notion of the goal it is optimizing for"
    unrelated_b = "the weather forecast for the coming weekend looks unusually calm and mild"
    head_text = historical_text.replace(quote_a, paraphrase_a).replace(quote_b, unrelated_b)
    anchor_a = _anchor(quote=quote_a)
    anchor_b = _anchor(quote=quote_b)
    guess_threshold = 0.52
    complete_orphan_threshold = 0.35

    oracle_a = _score_the_real_way(historical_text, quote_a, head_text)
    oracle_b = _score_the_real_way(historical_text, quote_b, head_text)
    assert oracle_a is not None and oracle_b is not None, "both fixtures need a candidate"
    assert oracle_a[1] >= guess_threshold, "anchor A's fixture must clear guess_threshold"
    assert oracle_b[1] < complete_orphan_threshold, (
        "anchor B's fixture must fall below complete_orphan_threshold"
    )

    first_pass_a = resolve_anchor(
        historical_text,
        head_text,
        anchor_a,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    first_pass_b = resolve_anchor(
        historical_text,
        head_text,
        anchor_b,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    second_pass_b = resolve_anchor(
        historical_text,
        head_text,
        anchor_b,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    second_pass_a = resolve_anchor(
        historical_text,
        head_text,
        anchor_a,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )

    assert first_pass_a.status == "fuzzy"
    assert first_pass_a.span == oracle_a[0]
    assert first_pass_b.status == "orphaned"
    assert first_pass_b.fidelity == "page"
    assert first_pass_b.best_guess is None
    assert first_pass_b.score == oracle_b[1]
    assert first_pass_a == second_pass_a
    assert first_pass_b == second_pass_b


# ---------------------------------------------------------------------------
# The score-nullability contract, pinned as a contract rather than an
# incidental fact of whichever fixtures the other tests happen to use.
# ---------------------------------------------------------------------------


def test_score_is_none_exactly_when_fidelity_is_none_or_topic():
    """The nullability contract lives on ``fidelity``, not on an enumeration
    of statuses: ``score is None`` if and only if ``fidelity is None`` (the
    historical lookup itself failed, ``anchor-invalid``) or ``fidelity ==
    "topic"`` (no page was ever claimed, or the page is gone at HEAD -- in
    both cases no candidate scoring could possibly have run).

    ``orphaned``/``topic`` (the page-deleted case) is included here
    deliberately -- an earlier, status-enumerated version of this contract
    left it out, and it is exactly the case that formulation got wrong: it
    also runs no scoring, yet its status is ``orphaned``, not one of the two
    statuses that formulation named.
    """
    anchor_invalid = resolve_anchor(
        "nothing here matches anything the anchor claims",
        "nor does the current version of this page mention it",
        _anchor(quote="a quote never written on this page"),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )
    unanchored = resolve_anchor(
        "",
        None,
        _anchor(page="", quote=""),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )
    topic_quote = "the metric quietly became the goal itself"
    orphaned_topic = resolve_anchor(
        f"The page used to say: {topic_quote}",
        None,
        _anchor(quote=topic_quote),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )
    exact_quote = "reward hacking is just goodhart with extra steps"
    exact = resolve_anchor(
        exact_quote,
        exact_quote,
        _anchor(quote=exact_quote),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    assert anchor_invalid.fidelity is None
    assert anchor_invalid.score is None
    assert anchor_invalid.best_guess is None
    assert unanchored.fidelity == "topic"
    assert unanchored.score is None
    assert unanchored.best_guess is None
    assert orphaned_topic.status == "orphaned"
    assert orphaned_topic.fidelity == "topic"
    assert orphaned_topic.score is None
    assert orphaned_topic.best_guess is None
    assert exact.fidelity == "span"
    assert exact.score is not None


def test_a_populated_best_guess_always_implies_a_populated_score():
    """``best_guess is not None`` implies ``score is not None`` -- a guess is
    never offered without a score to accompany it, even on the branch where
    scoring produced no raw measurement at all (the clamp floor still counts
    as a score for this purpose).
    """
    quote = "the model has no persistent notion of the goal it is optimizing for"
    heading = "## Working memory"
    historical_text = (
        "# Agent memory\n\n"
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{heading}\n\n"
        f"{quote}\n\n"
        "Closing thoughts on the phenomenon, spanning a whole extra sentence for length.\n"
    )
    heavier_reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    head_text = historical_text.replace(quote, heavier_reword)
    # guess_threshold sits above this reword's real score so the fixture lands on
    # a guess-bearing rung. A projection that resolves `fuzzy` carries `span` and
    # deliberately no `best_guess`, which would make the invariant vacuous here.
    guessed = resolve_anchor(
        historical_text,
        head_text,
        _anchor(quote=quote, heading="Working memory"),
        guess_threshold=0.60,
        complete_orphan_threshold=0.35,
    )

    assert guessed.best_guess is not None, (
        "fixture must reach a rung that actually offers a guess, or this test asserts "
        "the implication on a projection that could never have violated it"
    )
    assert guessed.score is not None


def test_every_non_null_score_lies_within_the_unit_interval_across_the_whole_ladder():
    """Pinned once for the whole ladder rather than per-rung: whatever rung a
    projection resolves at, a non-``None`` ``score`` must lie in ``[0.0,
    1.0]``. This is exactly the contract a negative clamp score broke -- one
    property, checked across a representative sample of every rung that
    computes a score, rather than trusted to hold implicitly.
    """
    exact_quote = "a tidy little quote used only for this range check"
    exact_text = f"Prelude.\n\n{exact_quote}\n\nPostscript."
    exact = resolve_anchor(
        exact_text,
        exact_text,
        _anchor(quote=exact_quote),
        guess_threshold=0.5,
        complete_orphan_threshold=0.2,
    )

    shifted_quote = "a tidy little quote used only for this range check"
    shifted_historical = f"Prelude.\n\n{shifted_quote}\n\nPostscript."
    shifted_head = (
        "Prelude.\n\nAn inserted paragraph moves everything below it further along.\n\n"
        f"{shifted_quote}\n\nPostscript."
    )
    shifted = resolve_anchor(
        shifted_historical,
        shifted_head,
        _anchor(quote=shifted_quote),
        guess_threshold=0.5,
        complete_orphan_threshold=0.2,
    )

    fuzzy_quote = "the model has no persistent notion of the goal it is optimizing for"
    fuzzy_historical = (
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{fuzzy_quote}\n\n"
        "Closing thoughts on the phenomenon."
    )
    fuzzy_paraphrase = "the model retains no persistent notion of the goal it is optimizing for"
    fuzzy_head = fuzzy_historical.replace(fuzzy_quote, fuzzy_paraphrase)
    fuzzy = resolve_anchor(
        fuzzy_historical,
        fuzzy_head,
        _anchor(quote=fuzzy_quote, heading=""),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    section_quote = "the model has no persistent notion of the goal it is optimizing for"
    section_heading = "## Working memory"
    section_historical = (
        "# Agent memory\n\n"
        "Preface paragraph about incentives and Goodhart's law in machine learning.\n\n"
        f"{section_heading}\n\n"
        f"{section_quote}\n\n"
        "Closing thoughts on the phenomenon, spanning a whole extra sentence for length.\n"
    )
    heavier_reword = (
        "researchers have observed that models often lack any lasting sense of what "
        "objective they are actually pursuing"
    )
    section_head = section_historical.replace(section_quote, heavier_reword)
    section = resolve_anchor(
        section_historical,
        section_head,
        _anchor(quote=section_quote, heading="Working memory"),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    page_guessed_head = section_head.replace(section_heading, "## Memory architecture (renamed)")
    page_guessed = resolve_anchor(
        section_historical,
        page_guessed_head,
        _anchor(quote=section_quote, heading="Working memory"),
        guess_threshold=0.52,
        complete_orphan_threshold=0.20,
    )

    floor_quote = "reward hacking undermines interpretability"
    floor_heading = "## Failure modes"
    floor_historical = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        f"{floor_heading}\n\n"
        f"{floor_quote}\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    floor_head = (
        "# Agent notes\n\n"
        "Some preface text unrelated to anything specific happening later on here.\n\n"
        "## Known issues (renamed)\n\n"
        "a brief note about scheduling logistics for next quarter's planning cycle\n\n"
        "Trailing remarks closing out this particular section for now.\n"
    )
    floor = resolve_anchor(
        floor_historical,
        floor_head,
        _anchor(quote=floor_quote, heading="Failure modes"),
        guess_threshold=0.52,
        complete_orphan_threshold=0.35,
    )

    # Every fixture above is chosen to produce a non-`None` score (verified
    # against the real scorer while designing each one), so each assertion
    # below checks the range directly rather than guarding on nullness first.
    assert exact.score is not None and 0.0 <= exact.score <= 1.0
    assert shifted.score is not None and 0.0 <= shifted.score <= 1.0
    assert fuzzy.score is not None and 0.0 <= fuzzy.score <= 1.0
    assert section.score is not None and 0.0 <= section.score <= 1.0
    assert page_guessed.score is not None and 0.0 <= page_guessed.score <= 1.0
    assert floor.score is not None and 0.0 <= floor.score <= 1.0


# ---------------------------------------------------------------------------
# Quote shape must not decide recoverability.
#
# ``fuzzy`` is the only rung that re-places a note automatically, so whether it
# can fire is what the whole ladder is for. Whether it fires must depend on how
# much the page drifted, not on whether the captured quote happened to be a
# whole sentence, a clause inside one, or a run of several. The three fixtures
# below apply the *identical* single-character edit to the *identical*
# paragraph and differ only in the shape of the anchored quote.
# ---------------------------------------------------------------------------

_SHAPE_LEAD_IN = "Long-horizon agents cannot retain every interaction at full fidelity."
_SHAPE_FIRST = (
    "Episodic traces are compressed into semantic summaries during idle periods, "
    "trading recall precision for storage economy."
)
_SHAPE_SECOND = "That tradeoff stays invisible until the corpus grows past a few thousand pages."
_SHAPE_THIRD = "Most teams discover it only when retrieval quality quietly degrades."
_SHAPE_TRAILER = "The result is a store whose size grows sublinearly with elapsed time."
_SHAPE_CLAUSE = "trading recall precision for storage economy"
_SHAPE_HISTORICAL = (
    "# Agent memory\n\n"
    "## Consolidation\n\n"
    + " ".join([_SHAPE_LEAD_IN, _SHAPE_FIRST, _SHAPE_SECOND, _SHAPE_THIRD, _SHAPE_TRAILER])
    + "\n\n## Retrieval\n\nRetrieval over consolidated summaries is cheaper but coarser.\n"
)
_SHAPE_TYPO_HEAD = _SHAPE_HISTORICAL.replace("precision", "precisian")


def _resolve_shape(quote: str, head_text: str) -> Projection:
    """Resolve ``quote`` against the shared shape fixture at shipped thresholds."""
    return resolve_anchor(
        _SHAPE_HISTORICAL,
        head_text,
        _anchor(quote=quote, heading="Consolidation", start=_SHAPE_HISTORICAL.index(quote)),
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )


def test_a_whole_sentence_quote_with_a_single_character_edit_resolves_fuzzy():
    """The control: the one quote shape already known to recover. It pins the
    baseline the other two shapes must reach, so a regression that lowers this
    case shows up here rather than making the comparisons below vacuous.
    """
    projection = _resolve_shape(_SHAPE_FIRST, _SHAPE_TYPO_HEAD)

    assert projection.status == "fuzzy"
    assert projection.fidelity == "span"
    assert projection.score is not None and projection.score > 0.9


def test_a_sub_clause_quote_with_a_single_character_edit_resolves_fuzzy():
    """A quote that is a clause *inside* a sentence rather than the whole of
    it. The window proposed for it is its host sentence -- roughly twice its
    own length -- and scoring the quote against that whole width caps the
    similarity ratio near 0.67 however small the edit, which is what put this
    case permanently out of reach of ``guess_threshold``.
    """
    projection = _resolve_shape(_SHAPE_CLAUSE, _SHAPE_TYPO_HEAD)

    assert projection.status == "fuzzy", (
        f"resolved {projection.status}/{projection.fidelity} at score {projection.score} -- "
        "one changed character must be recoverable whether the captured quote was a whole "
        "sentence or a clause within one"
    )
    assert projection.fidelity == "span"
    assert projection.score is not None and projection.score > 0.9


def test_a_multi_sentence_quote_with_a_single_character_edit_resolves_fuzzy():
    """A quote spanning three consecutive sentences -- what a client passes
    when it displayed a paragraph rather than a line. A window bounded by one
    sentence can never cover it, so this case was unreachable at any threshold.
    """
    quote = f"{_SHAPE_FIRST} {_SHAPE_SECOND} {_SHAPE_THIRD}"

    projection = _resolve_shape(quote, _SHAPE_TYPO_HEAD)

    assert projection.status == "fuzzy", (
        f"resolved {projection.status}/{projection.fidelity} at score {projection.score} -- "
        "a multi-sentence quote must be recoverable from a one-character edit, not left "
        "unmatchable because no proposed window was ever wide enough to hold it"
    )
    assert projection.fidelity == "span"
    assert projection.score is not None and projection.score > 0.9


def test_a_fuzzy_span_is_the_aligned_placement_not_the_wider_window_it_was_found_in():
    """``fuzzy`` claims a location; the span it reports is rendered to a human
    and highlighted in the drift queue. It must therefore be the sub-span the
    score was actually computed for, not the wider candidate window that
    sub-span was located inside.
    """
    projection = _resolve_shape(_SHAPE_CLAUSE, _SHAPE_TYPO_HEAD)

    assert projection.status == "fuzzy"
    assert projection.span is not None
    start, end = projection.span
    assert _SHAPE_TYPO_HEAD[start:end] == _SHAPE_CLAUSE.replace("precision", "precisian")


def test_a_passage_replaced_by_unrelated_prose_never_resolves_fuzzy():
    """The band-sharpening guard, at ladder level. Making sub-span quotes
    recoverable raises every score the scorer produces; the change is only
    correct if it leaves prose that shares nothing with the quote below
    ``guess_threshold``, so unrelated text is never auto-placed.
    """
    unrelated = (
        "Tool schemas are validated against the declared JSON Schema before dispatch, "
        "and a mismatch is rejected at the boundary."
    )
    head_text = _SHAPE_HISTORICAL.replace(_SHAPE_FIRST, unrelated)

    projection = _resolve_shape(_SHAPE_CLAUSE, head_text)

    assert projection.status == "orphaned", (
        f"unrelated prose resolved {projection.status} at score {projection.score} -- "
        "a passage that no longer exists must never be silently re-placed"
    )


# ---------------------------------------------------------------------------
# Zero writes -- a type-level guarantee, not a runtime assertion
# ---------------------------------------------------------------------------


def test_resolve_anchor_signature_still_takes_no_vault_handle_after_gaining_thresholds():
    """Resolution gained two threshold parameters this phase but must remain
    expressible as a pure function -- no store, no vault handle, no config
    object, nothing that would make the call anything other than free and
    re-runnable against any HEAD.
    """
    parameters = set(inspect.signature(resolve_anchor).parameters)

    assert parameters == {
        "historical_text",
        "head_text",
        "anchor",
        "guess_threshold",
        "complete_orphan_threshold",
    }


def test_projection_is_an_immutable_value_type():
    quote = "a quote used only to build a projection instance"
    projection = resolve_anchor(
        quote,
        quote,
        _anchor(quote=quote),
        guess_threshold=_STANDARD_GUESS_THRESHOLD,
        complete_orphan_threshold=_STANDARD_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert dataclasses.is_dataclass(Projection)
    with_frozen_error = dataclasses.FrozenInstanceError
    try:
        projection.status = "exact"  # type: ignore[misc]
    except with_frozen_error:
        pass
    else:
        raise AssertionError("Projection must be frozen -- resolution results cannot be mutated")
