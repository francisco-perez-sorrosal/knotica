"""Behavioral contract of the Hypothesis-weighted candidate scorer.

Derived from the notes-overlay resolution ladder's scoring rung, not from any
implementation. ``score_candidate`` scores one candidate span (from the MSR
keyword generator) against the anchor's own recorded quote/prefix/suffix and
its historical offset, combining four ``difflib.SequenceMatcher.ratio()`` and
position terms into one weighted sum. ``score_candidates`` argmaxes that score
over a whole candidate set.

**Pinned contract** (this module does not exist yet -- these choices are the
test-engineer's, made explicit here and in the handoff report rather than
resolved silently):

- ``score_candidate(candidate, head_text, quote, prefix, suffix,
  historical_offset) -> float``. ``candidate`` is a ``(start, end)`` span into
  ``head_text``, matching the keyword-candidate generator's output shape.
  ``quote``/``prefix``/``suffix`` are the anchor's own already-extracted
  historical strings (the caller's job -- resolution rung 0 -- not this
  module's); this module derives the candidate's *own* quote/prefix/suffix
  from ``head_text`` at the candidate span.
- ``score_candidates(candidates, head_text, quote, prefix, suffix,
  historical_offset) -> tuple[tuple[int, int], float] | None``. Returns
  ``None`` for an empty candidate set rather than raising: an empty candidate
  set is a normal, anticipated input (the keyword generator's own tests cover
  a quote made entirely of stopwords, which can starve candidate generation),
  not a programming error, and this module already has an established
  Optional-returning sibling for "nothing found" (``resolve.py``'s
  ``_nearest_occurrence``).
- The weights (50/20/20/2) and normaliser (92) are named constants the
  implementation must export -- tests import them rather than hardcoding the
  literals, so a magic-number slip in the implementation shows up as an
  import failure, not a silently-wrong score.

Several formula details are genuinely open and are deliberately **not**
resolved here -- see the handoff report for the full list (what ``text``
means in the position term's ``len(text)``; the exact context-window width
used to extract a candidate's own prefix/suffix from ``head_text``; behavior
when the anchor's historical prefix/suffix were themselves truncated near a
page boundary). Tests below are written to hold under either plausible
reading of those questions -- via zero offset-delta (silences the position
term's ambiguity, since 0 divided by anything is 0), matching-context
fixtures (silences the window-width ambiguity, since there is nothing else in
the surrounding text a differently-sized window could disagree about), and
threshold assertions in place of exact expected values where the two are not
fully separable.
"""

import difflib

import pytest

from knotica.core.notes.scoring import (
    NORMALISER,
    POSITION_WEIGHT,
    PREFIX_WEIGHT,
    QUOTE_WEIGHT,
    SUFFIX_WEIGHT,
    score_candidate,
    score_candidates,
)


def _ratio(a: str, b: str) -> float:
    """The same similarity primitive the scorer is specified to use."""
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# The constants are the published formula -- pin their values, not just their
# existence, since they come from an external, cited source (Hypothesis),
# not an implementation detail free to drift.
# ---------------------------------------------------------------------------


def test_weight_and_normaliser_constants_match_the_published_hypothesis_formula():
    assert QUOTE_WEIGHT == 50
    assert PREFIX_WEIGHT == 20
    assert SUFFIX_WEIGHT == 20
    assert POSITION_WEIGHT == 2
    assert NORMALISER == 92
    assert QUOTE_WEIGHT + PREFIX_WEIGHT + SUFFIX_WEIGHT + POSITION_WEIGHT == NORMALISER


# ---------------------------------------------------------------------------
# score_candidate -- one candidate against the anchor's recorded context
# ---------------------------------------------------------------------------


def test_score_candidate_combines_the_four_ratio_terms_per_the_weighted_formula():
    """A candidate placed at exactly the historical offset, with head_text
    containing nothing but the anchor's own prefix/quote/suffix concatenated,
    removes every extraction ambiguity: the candidate's own prefix/suffix can
    only ever equal the anchor's (there is nothing else in the text to
    extract), and a zero offset-delta makes the position term exactly 1.0
    under any reading of ``len(text)``. What remains is a direct check that
    the four terms are combined via the named weights and normaliser, not
    some other combination.
    """
    prefix = "before the passage"
    quote = "the exact quote text that was pinned to this page"
    suffix = "continues right after it"
    head_text = prefix + quote + suffix
    candidate = (len(prefix), len(prefix) + len(quote))
    historical_offset = candidate[0]

    score = score_candidate(candidate, head_text, quote, prefix, suffix, historical_offset)

    expected = (
        QUOTE_WEIGHT * _ratio(quote, quote)
        + PREFIX_WEIGHT * _ratio(prefix, prefix)
        + SUFFIX_WEIGHT * _ratio(suffix, suffix)
        + POSITION_WEIGHT * 1.0
    ) / NORMALISER
    assert score == pytest.approx(expected)
    assert score == pytest.approx(1.0)


def test_score_candidate_tolerates_empty_prefix_and_suffix_at_a_text_boundary():
    """The anchor's quote sat at the very start of a page with nothing
    before or after it -- the degenerate case of a truncated context window.
    ``difflib.SequenceMatcher`` defines the ratio of two empty sequences as
    1.0, so this remains a full-formula, non-degenerate check rather than a
    crash-only smoke test.
    """
    quote = "a quote that opens the page with nothing preceding it"
    head_text = quote
    candidate = (0, len(quote))
    historical_offset = 0

    score = score_candidate(candidate, head_text, quote, "", "", historical_offset)

    assert score == pytest.approx(1.0)


def test_score_candidate_identical_quote_at_a_shifted_position_scores_near_one():
    """A verbatim match whose recorded context still surrounds it, but whose
    position moved modestly from where it was pinned -- the paraphrase-free
    "just moved" case. Position carries only 2 of the 92 normaliser points,
    so even a nontrivial shift should barely dent a perfect quote/prefix/
    suffix match.
    """
    prefix = "context that precedes the passage in both versions"
    quote = "the passage itself, unchanged word for word"
    suffix = "context that follows the passage in both versions"
    filler = "an inserted paragraph that pushed the passage further down. "
    head_text = filler + prefix + quote + suffix
    candidate = (len(filler) + len(prefix), len(filler) + len(prefix) + len(quote))
    historical_offset = len(prefix)  # where it used to sit, before the insertion

    score = score_candidate(candidate, head_text, quote, prefix, suffix, historical_offset)

    assert score > 0.9


def test_score_candidate_heavily_reworded_passage_scores_low():
    """A candidate window that shares essentially no wording, phrasing, or
    surrounding context with the anchor -- MSR's justification for the
    orphaned/no-guess floor below ``complete_orphan_threshold``.
    """
    prefix = "some context before"
    quote = "the original pinned sentence about incentive structures"
    suffix = "some context after"
    reworded_prefix = "zzqx flarn wibble tock"
    reworded_quote = "grommet spindle laconic ossify pumice velvet"
    reworded_suffix = "yonder plinth acrid drab"
    head_text = reworded_prefix + reworded_quote + reworded_suffix
    candidate = (len(reworded_prefix), len(reworded_prefix) + len(reworded_quote))
    historical_offset = candidate[0]  # even with zero position penalty...

    score = score_candidate(candidate, head_text, quote, prefix, suffix, historical_offset)

    assert score < 0.3  # ...content dissimilarity alone must dominate the result


# ---------------------------------------------------------------------------
# score_candidates -- argmax over a candidate set
# ---------------------------------------------------------------------------


def test_score_candidates_returns_none_for_an_empty_candidate_set():
    score_result = score_candidates((), "some head text", "a quote", "pre", "post", 0)

    assert score_result is None


def test_score_candidates_with_one_candidate_trivially_returns_it():
    quote = "the only candidate in an otherwise unrelated page"
    prefix = "leading text "
    suffix = " trailing text"
    head_text = f"unrelated opening. {prefix}{quote}{suffix} unrelated closing."
    candidate = (head_text.index(quote), head_text.index(quote) + len(quote))

    result = score_candidates((candidate,), head_text, quote, prefix, suffix, candidate[0])

    assert result is not None
    winning_span, winning_score = result
    assert winning_span == candidate
    assert winning_score == score_candidate(
        candidate, head_text, quote, prefix, suffix, candidate[0]
    )


def test_score_candidates_selects_the_true_argmax_of_the_individual_candidate_scores():
    """The set-level function must be *the* argmax of the same per-candidate
    scorer callers can invoke directly -- not a separately-tuned shortcut.
    Checked against all three candidates' individually-computed scores rather
    than against one hand-picked expected winner, so the assertion holds
    regardless of exactly how any single candidate's score is computed.
    """
    quote = "the recorded phrase that was originally pinned"
    prefix = "the paragraph leading into it reads"
    suffix = "and the paragraph following it continues"
    near_exact = f"{prefix}{quote}{suffix}"
    partial_match = f"{prefix}a completely different middle sentence entirely{suffix}"
    unrelated = "nothing here resembles the pinned phrase or its context whatsoever"
    head_text = f"{near_exact} ||| {partial_match} ||| {unrelated}"
    candidates = (
        (0, len(near_exact)),
        (len(near_exact) + 5, len(near_exact) + 5 + len(partial_match)),
        (len(head_text) - len(unrelated), len(head_text)),
    )
    historical_offset = candidates[0][0]

    individual_scores = {
        span: score_candidate(span, head_text, quote, prefix, suffix, historical_offset)
        for span in candidates
    }
    best_span = max(individual_scores, key=lambda span: individual_scores[span])

    result = score_candidates(candidates, head_text, quote, prefix, suffix, historical_offset)

    assert result == (best_span, individual_scores[best_span])


def test_score_candidates_breaks_a_quote_similarity_tie_using_position_proximity():
    """Two candidates share byte-identical local context (so quote, prefix,
    and suffix ratios tie exactly under any window-extraction width, since
    there is nothing else nearby for a wider window to disagree about) but
    sit at different distances from the anchor's historical offset. Only the
    position term can break the tie, and it must favor the nearer one.
    """
    island = "steady lead-in text " + "the tied candidate phrase" + " steady trailing text"
    quote = "the tied candidate phrase"
    prefix = "steady lead-in text "
    suffix = " steady trailing text"
    pad = "z" * 60  # comfortably wider than any plausible extraction window
    head_text = pad + island + pad + island + pad
    first_start = len(pad) + len(prefix)
    first_candidate = (first_start, first_start + len(quote))
    second_start = len(pad) + len(island) + len(pad) + len(prefix)
    second_candidate = (second_start, second_start + len(quote))
    historical_offset = first_candidate[0]  # zero delta for the first, large for the second

    result = score_candidates(
        (first_candidate, second_candidate), head_text, quote, prefix, suffix, historical_offset
    )

    assert result is not None
    winning_span, _ = result
    assert winning_span == first_candidate
