"""Behavioral contract of the keyword candidate generator.

Derived from the notes-overlay resolution ladder's keyword-candidate-generation
rung, not from any implementation. This rung only ever runs after both the
exact and the shifted rungs have already failed to find the anchor's ``quote``
verbatim in the current page text -- every fixture in this file deliberately
constructs a ``quote`` that does not occur verbatim in ``head_text`` at all,
because that absence is this rung's ordinary, expected input, not an edge
case.

Given ``(quote, head_text)``, candidate generation must:

- seed on the page-rarest words of the quote -- the words that occur least
  often in ``head_text``, starting the search at frequency 1 and relaxing to
  2, 3, ... until at least three seed words are found;
- treat a proper noun as eligible regardless of how frequently it occurs,
  bypassing the ordinary rarity search entirely for that word. This module
  has no ground truth for what counts as a proper noun, so these tests pin
  only the one case the notes-overlay design calls out explicitly -- a
  capitalized token that is *not* at the start of its sentence -- and stay
  silent on any implementation's finer heuristics beyond that;
- seed a window at *every* occurrence of a chosen seed word, not merely its
  first, and extend each window to the boundaries of the sentence it falls
  in;
- cap every window's length at twice the length of the quote, so that a
  seed word buried in an unusually long run of prose does not produce an
  unbounded candidate.

Candidate generation is pure and stdlib-only: it takes no store, no vault
handle, no config, and produces no writes -- a leaf module under
``core/notes/`` consumed only by the resolution ladder.

**Structural boundary regression.** The sentence-extension rule above is
qualified: a window's backward extension must also stop at a heading line
or a blank line, whichever comes first, rather than running past them all
the way to document start. A quote whose seed words sit right after a
page-opening heading is otherwise scored against a window that is mostly
heading chrome rather than the passage itself, purely because of where the
passage happens to sit on the page -- see the "Structural boundary" tests
below, which pin the fix at both the candidate-window level and, in the
headline case, the resulting match score.
"""

import inspect

import pytest

from knotica.core.notes.candidates import generate_candidates
from knotica.core.notes.scoring import CONTEXT_WINDOW, score_candidates

# ---------------------------------------------------------------------------
# Shared fixture: a quote whose five words span every rarity band this rung
# must handle -- two occurring once each, one occurring twice (reachable only
# by relaxing the frequency search), and two occurring often enough that the
# rarity search must never reach them because three rarer words are already
# found first.
# ---------------------------------------------------------------------------

_PRECEDING = "Introductory remarks set the scene without touching anything relevant at all."
_GLIMMER_SENTENCE = "A faint glimmer crossed the water at dusk."
_FOLLOWING = "Later notes continue on a wholly different subject entirely."
_AUROCH_SENTENCE = "The auroch grazed alone near the ridge."
_CANTICLE_FIRST = "A canticle rose from the chapel bell."
_CANTICLE_SECOND = "The canticle faded as the choir dispersed slowly."
_DISTRACTORS = [
    "Rain falls quietly across the empty courtyard.",
    "Snow falls quietly on the mountain path.",
    "Dust falls quietly through the broken window.",
    "Ash falls quietly over the abandoned town.",
]
_RARITY_QUOTE = "glimmer auroch canticle falls quietly"
_RARITY_HEAD_TEXT = "\n\n".join(
    [
        _PRECEDING,
        _GLIMMER_SENTENCE,
        _FOLLOWING,
        _AUROCH_SENTENCE,
        _CANTICLE_FIRST,
        _CANTICLE_SECOND,
        *_DISTRACTORS,
    ]
)


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


def _valid_windows(candidates: tuple[tuple[int, int], ...], head_text: str) -> bool:
    """Every returned window is a proper, in-bounds ``(start, end)`` range."""
    return all(
        isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(head_text)
        for start, end in candidates
    )


# ---------------------------------------------------------------------------
# Rarity-based seeding
# ---------------------------------------------------------------------------


def test_words_occurring_once_in_the_page_seed_windows_at_their_occurrence():
    candidates = generate_candidates(_RARITY_QUOTE, _RARITY_HEAD_TEXT)

    assert _valid_windows(candidates, _RARITY_HEAD_TEXT)
    glimmer_offset = _RARITY_HEAD_TEXT.index("glimmer")
    auroch_offset = _RARITY_HEAD_TEXT.index("auroch")
    assert any(start <= glimmer_offset < end for start, end in candidates), (
        "'glimmer' occurs only once on the page and must seed a window there"
    )
    assert any(start <= auroch_offset < end for start, end in candidates), (
        "'auroch' occurs only once on the page and must seed a window there"
    )


def test_a_word_reached_only_by_relaxing_the_frequency_threshold_is_seeded_at_every_occurrence():
    candidates = generate_candidates(_RARITY_QUOTE, _RARITY_HEAD_TEXT)

    canticle_offsets = _occurrences(_RARITY_HEAD_TEXT, "canticle")
    assert len(canticle_offsets) == 2, "fixture must repeat the relaxed-threshold word twice"
    for offset in canticle_offsets:
        assert any(start <= offset < end for start, end in candidates), (
            f"occurrence at {offset} must have its own seeded window -- 'canticle' only "
            "qualifies once the frequency search relaxes past 1, and every occurrence of "
            "a qualifying word must be seeded, not just its first"
        )


def test_words_more_frequent_than_the_pages_three_rarest_are_never_seeded():
    candidates = generate_candidates(_RARITY_QUOTE, _RARITY_HEAD_TEXT)

    for distractor in _DISTRACTORS:
        sentence_start = _RARITY_HEAD_TEXT.index(distractor)
        sentence_end = sentence_start + len(distractor)
        overlapping = [
            (start, end)
            for start, end in candidates
            if start < sentence_end and end > sentence_start
        ]
        assert not overlapping, (
            "'falls'/'quietly' occur far more often on the page than the three words "
            "the rarity search already found (glimmer, auroch, canticle), so they must "
            "never be used as seeds of their own"
        )


def test_a_seeded_window_extends_to_the_whole_sentence_and_no_further():
    candidates = generate_candidates(_RARITY_QUOTE, _RARITY_HEAD_TEXT)

    sentence_start = _RARITY_HEAD_TEXT.index(_GLIMMER_SENTENCE)
    sentence_end = sentence_start + len(_GLIMMER_SENTENCE)
    covering = [
        (start, end) for start, end in candidates if start <= sentence_start and end >= sentence_end
    ]
    assert covering, "the window must extend to cover the whole sentence, not a bare word"
    start, end = covering[0]
    window_text = _RARITY_HEAD_TEXT[start:end]
    assert "Introductory remarks" not in window_text, (
        "extension must stop at the sentence boundary, not swallow the preceding sentence"
    )
    assert "Later notes continue" not in window_text, (
        "extension must stop at the sentence boundary, not swallow the following sentence"
    )


def test_a_capitalized_mid_sentence_token_is_eligible_as_a_seed_regardless_of_frequency():
    """A pragmatic proper-noun heuristic has no ground truth to validate
    against yet -- this test pins only the one contract the notes-overlay
    design calls out explicitly (capitalized, not sentence-initial) and does
    not assume any particular implementation of that heuristic beyond it.
    """
    quote = "hollow reverie starlight Meridian"
    head_text = "\n\n".join(
        [
            "A hollow ache settled over the empty hall that evening.",
            "A quiet reverie lingered long after the fire had died down.",
            "Faint starlight scattered across the frozen lake at dusk.",
            "The chart showed Meridian climbing steadily through the quarter.",
            "Analysts again pointed to Meridian as the figure driving the trend.",
            "By spring, Meridian had become the name everyone repeated.",
            "Investors kept watching Meridian long after the initial surge.",
        ]
    )

    candidates = generate_candidates(quote, head_text)

    meridian_offsets = _occurrences(head_text, "Meridian")
    assert len(meridian_offsets) == 4, "fixture must repeat the proper noun several times"
    assert any(start <= offset < end for offset in meridian_offsets for start, end in candidates), (
        "'Meridian' is far more frequent than the three ordinary words that already "
        "satisfy the rarity search on their own (hollow, reverie, starlight), yet a "
        "capitalized, mid-sentence token must still be eligible as a seed"
    )


# ---------------------------------------------------------------------------
# The 2x-quote-length cap
# ---------------------------------------------------------------------------


def test_a_seeded_window_is_capped_at_twice_the_quote_length():
    quote = "hollow murmuring recollected"
    long_sentence = (
        "The travelers moved slowly across a wide and hollow valley, passing old stone "
        "markers and half-buried ruins, tracing a path that wound through dry riverbeds "
        "and scattered groves before climbing toward a distant ridge that promised "
        "shelter long before the coming storm finally arrived without warning at all."
    )
    murmuring_sentence = "A soft murmuring rose from the camp at nightfall."
    recollected_sentence = "She recollected the melody only after everyone else had gone."
    head_text = "\n\n".join([long_sentence, murmuring_sentence, recollected_sentence])
    cap = 2 * len(quote)
    assert len(long_sentence) > cap, "fixture must actually exceed the cap to prove clipping"

    candidates = generate_candidates(quote, head_text)

    assert _valid_windows(candidates, head_text)
    hollow_offset = head_text.index("hollow")
    assert any(start <= hollow_offset < end for start, end in candidates), (
        "'hollow' is a legitimate rare seed word and must still produce a window"
    )
    assert all(end - start <= cap for start, end in candidates), (
        "no window may exceed twice the quote length, even when its containing "
        "sentence, uncapped, would run far longer than that"
    )


def test_a_seeded_window_is_not_shortened_when_the_whole_page_is_under_the_cap():
    quote = (
        "notwithstanding thicket that lingered hitherto, a lantern and a reckoning "
        "arrived somewhere beyond an otherwise unremembered evening"
    )
    sentence = "At dawn the thicket rustled quietly."
    head_text = f"{sentence} Soon a lantern flickered nearby. Later a reckoning came at last."
    cap = 2 * len(quote)
    assert len(head_text) < cap, "fixture must actually be shorter than the cap"

    candidates = generate_candidates(quote, head_text)

    assert _valid_windows(candidates, head_text)
    thicket_offset = head_text.index("thicket")
    covering = [(start, end) for start, end in candidates if start <= thicket_offset < end]
    assert covering, "'thicket' is a legitimate rare seed word and must produce a window"
    start, end = covering[0]
    assert sentence in head_text[start:end], (
        "the window must cover the whole sentence, not a fragment shorter than it -- "
        "the cap has nothing to clip when the whole page is already under it"
    )


# ---------------------------------------------------------------------------
# Graceful degradation -- the frequency search exhausts the page
# ---------------------------------------------------------------------------


def test_relaxation_uses_whatever_rare_words_are_available_when_fewer_than_three_exist():
    quote = "brindle vesper vellum vantage"
    head_text = (
        "A brindle cat slept curled beside the cold hearth all afternoon.\n\n"
        "Every evening the vesper bell rang once across the empty square."
    )
    assert "vellum" not in head_text and "vantage" not in head_text, (
        "fixture must leave two of the quote's four words entirely absent from the "
        "page, so the frequency search can never accumulate the usual three seeds"
    )

    candidates = generate_candidates(quote, head_text)

    assert _valid_windows(candidates, head_text)
    brindle_offset = head_text.index("brindle")
    vesper_offset = head_text.index("vesper")
    assert any(start <= brindle_offset < end for start, end in candidates), (
        "a rare word that does appear on the page must still seed a window even "
        "though relaxation could never reach the usual three seed words"
    )
    assert any(start <= vesper_offset < end for start, end in candidates)


def test_never_raises_when_none_of_the_quotes_words_appear_on_the_page_at_all():
    quote = "zephyrine quokka umbrageous"
    head_text = "This page discusses an entirely unrelated subject in unrelated prose."

    # A crash here would itself be the failure: this rung runs precisely when the
    # quote has already failed to match verbatim, so a page sharing none of the
    # quote's vocabulary is a legitimate, expected input, not an error condition.
    candidates = generate_candidates(quote, head_text)

    assert isinstance(candidates, tuple)


def test_a_quote_of_only_common_words_still_produces_a_candidate_set():
    quote = "the and of in with"
    head_text = "\n\n".join(
        [
            "The story of the harvest and the long winter that followed it began with "
            "a quiet morning in the valley below.",
            "In the years that came after, the story was retold again and again with "
            "new details added each time it was shared.",
            "Of all the tales told in the valley, the one about the harvest and the "
            "winter was remembered the longest, told with real affection.",
        ]
    )

    candidates = generate_candidates(quote, head_text)

    assert candidates, (
        "the module has no stopword filter -- even a quote built entirely from "
        "common function words must still produce something for the scorer to "
        "evaluate, chosen from among the least-frequent of them on this page"
    )
    assert _valid_windows(candidates, head_text)


# ---------------------------------------------------------------------------
# Purity contract
# ---------------------------------------------------------------------------


def test_generate_candidates_signature_accepts_only_quote_and_head_text():
    """Candidate generation must be expressible as a pure function of the
    anchor's quote and the current page text -- no store, no vault handle,
    no config, nothing that would make it anything other than a stdlib-only
    leaf consumed by the resolution ladder.
    """
    parameters = list(inspect.signature(generate_candidates).parameters)

    assert parameters == ["quote", "head_text"]


# ---------------------------------------------------------------------------
# Structural boundary: a backward extension must stop at a heading line or a
# blank line, whichever comes first, rather than running to document start.
#
# Every fixture below edits exactly one word of the target sentence (mirrors
# the ordinary "the page moved on slightly since the note was captured"
# case) and places the identical passage, edit, and immediate surrounding
# context either right after page-opening heading chrome or buried deep in
# ordinary prose -- so the only thing that can differ between the two is
# where the passage sits on the page.
# ---------------------------------------------------------------------------

_PAGE_HEADING = "# Page Title\n\n## Section Heading"
_UNPUNCTUATED_OPENING_PARAGRAPH = (
    "Market commentary from earlier in the week, spanning several loosely related "
    "topics, and continuing without ever reaching a proper stopping point"
)
_LEAD_IN_PARAGRAPH = (
    "Analysts have been tracking the model's behaviour across several recent "
    "evaluation runs conducted throughout the quarter."
)
_ORIGINAL_TARGET_SENTENCE = (
    "Reward hacking is the model satisfying the metric rather than the intended goal."
)
_EDITED_TARGET_SENTENCE = (
    "Reward hacking is the model satisfying the metric rather than the desired goal."
)
_TRAILING_PARAGRAPH = "Investors reacted quickly to the announcement soon afterward."
_MID_PAGE_FILLER_PARAGRAPHS = [
    "The quarterly report opened with a summary of macroeconomic conditions across the region.",
    "Supply chain disruptions continued to affect delivery times for several major manufacturers.",
    "Regulators signalled openness to further review before any new guidance would be issued.",
]


def _near_top_head_text() -> str:
    """A heading, a blank line, then the edited target sentence -- nothing
    with sentence-ending punctuation intervenes between document start and
    the sentence itself, so a backward extension that only respects
    ``.``/``!``/``?`` boundaries has nothing to stop it before offset 0.
    """
    return "\n\n".join([_PAGE_HEADING, _EDITED_TARGET_SENTENCE, _TRAILING_PARAGRAPH])


def _blank_line_only_head_text() -> str:
    """No heading at all -- an ordinary paragraph with no terminating
    punctuation of its own, a blank line, then the edited target sentence.
    Pins that a blank line alone is a structural boundary, not only a
    heading line.
    """
    return "\n\n".join(
        [_UNPUNCTUATED_OPENING_PARAGRAPH, _EDITED_TARGET_SENTENCE, _TRAILING_PARAGRAPH]
    )


def _mid_page_head_text() -> str:
    """The identical lead-in paragraph and edited sentence as the near-top
    fixture's own control counterpart, buried under several ordinary filler
    paragraphs instead of sitting right after a heading -- the passage, the
    edit, and its immediate surrounding context are otherwise unchanged;
    only its position on the page differs.
    """
    return "\n\n".join(
        [
            *_MID_PAGE_FILLER_PARAGRAPHS,
            _LEAD_IN_PARAGRAPH,
            _EDITED_TARGET_SENTENCE,
            _TRAILING_PARAGRAPH,
        ]
    )


def _windows_overlapping_the_target_sentence(
    candidates: tuple[tuple[int, int], ...], head_text: str
) -> list[tuple[int, int]]:
    """Every candidate window that overlaps the edited target sentence's own
    span in ``head_text``.
    """
    true_start = head_text.index(_EDITED_TARGET_SENTENCE)
    true_end = true_start + len(_EDITED_TARGET_SENTENCE)
    return [(start, end) for start, end in candidates if start < true_end and end > true_start]


def test_a_quote_near_page_start_does_not_extend_the_window_back_to_document_start():
    head_text = _near_top_head_text()

    candidates = generate_candidates(_ORIGINAL_TARGET_SENTENCE, head_text)
    overlapping = _windows_overlapping_the_target_sentence(candidates, head_text)

    assert overlapping, "at least one window must cover the target sentence"
    assert all(start > 0 for start, _end in overlapping), (
        "a window covering a passage that sits right after a heading and a blank line "
        "must not extend all the way back to document start -- the heading is a "
        "structural boundary the backward extension must respect"
    )


def test_a_blank_line_alone_stops_backward_extension_even_without_a_heading():
    head_text = _blank_line_only_head_text()
    true_start = head_text.index(_EDITED_TARGET_SENTENCE)

    candidates = generate_candidates(_ORIGINAL_TARGET_SENTENCE, head_text)
    overlapping = _windows_overlapping_the_target_sentence(candidates, head_text)

    assert overlapping, "at least one window must cover the target sentence"
    assert all(start >= true_start for start, _end in overlapping), (
        "an unpunctuated opening paragraph followed by a blank line must still stop the "
        "backward extension there -- a heading is not the only structural boundary that "
        "must be respected"
    )


def test_a_heading_line_is_never_swallowed_into_a_candidate_window():
    head_text = _near_top_head_text()

    candidates = generate_candidates(_ORIGINAL_TARGET_SENTENCE, head_text)

    for start, end in candidates:
        window_text = head_text[start:end]
        assert "Page Title" not in window_text
        assert "Section Heading" not in window_text


def _best_score_for_the_target_sentence(head_text: str) -> float:
    """Score the winning candidate for the edited target sentence in
    ``head_text`` against its own already-extracted local context, so the
    only thing that can vary between two pages is the quality of the
    candidate window itself, not the surrounding historical context fed to
    the scorer.
    """
    true_start = head_text.index(_EDITED_TARGET_SENTENCE)
    true_end = true_start + len(_EDITED_TARGET_SENTENCE)
    historical_prefix = head_text[max(0, true_start - CONTEXT_WINDOW) : true_start]
    historical_suffix = head_text[true_end : true_end + CONTEXT_WINDOW]

    candidates = generate_candidates(_ORIGINAL_TARGET_SENTENCE, head_text)
    result = score_candidates(
        candidates,
        head_text,
        _ORIGINAL_TARGET_SENTENCE,
        historical_prefix,
        historical_suffix,
        true_start,
    )
    assert result is not None, "the target sentence must produce at least one scored candidate"
    _span, score = result
    return score


def test_the_same_small_edit_scores_materially_the_same_near_the_top_of_a_page_and_mid_page():
    """The headline regression, and the one test that would have caught the
    defect directly: a quote and a single-word edit to it, scored once with
    the passage sitting right after page-opening heading chrome and once
    with the identical passage buried deep in ordinary prose. Nothing about
    the passage, the edit, or its immediate surrounding context differs
    between the two pages -- only where the passage sits. If the candidate
    window generator lets the backward extension run past the heading, the
    near-top score collapses relative to the mid-page one for reasons that
    have nothing to do with match quality.
    """
    near_top_score = _best_score_for_the_target_sentence(_near_top_head_text())
    mid_page_score = _best_score_for_the_target_sentence(_mid_page_head_text())

    assert mid_page_score > 0.6, "the control case itself must be a good match"
    assert near_top_score == pytest.approx(mid_page_score, abs=0.05), (
        f"near-top={near_top_score:.3f} vs mid-page={mid_page_score:.3f} -- position on "
        "the page must not dominate the match score; the same edit to the same passage "
        "should score materially the same wherever on the page it sits"
    )


# ---------------------------------------------------------------------------
# Multi-sentence quotes: a window must be able to span consecutive sentences.
#
# A window extended to exactly one sentence can never cover a quote that spans
# two or three of them, so such a quote is unrecoverable at any threshold --
# no scorer can rank a window that was never proposed. The extension must
# therefore keep absorbing neighbouring sentences whenever the quote outruns
# the sentence its seed word fell in, while still respecting the very
# boundaries the single-sentence case respects: the structural block the seed
# occurrence sits in, and the length cap.
# ---------------------------------------------------------------------------

_MULTI_LEAD_IN = "Long-horizon agents cannot retain every interaction at full fidelity."
_MULTI_FIRST = (
    "Episodic traces are compressed into semantic summaries during idle periods, "
    "trading recall precision for storage economy."
)
_MULTI_SECOND = "That tradeoff stays invisible until the corpus grows past a few thousand pages."
_MULTI_THIRD = "Most teams discover it only when retrieval quality quietly degrades."
_MULTI_TRAILER = "The result is a store whose size grows sublinearly with elapsed time."
_MULTI_QUOTE = f"{_MULTI_FIRST} {_MULTI_SECOND} {_MULTI_THIRD}"
_MULTI_HEAD_TEXT = (
    "# Agent memory\n\n"
    "## Consolidation\n\n"
    + " ".join([_MULTI_LEAD_IN, _MULTI_FIRST, _MULTI_SECOND, _MULTI_THIRD, _MULTI_TRAILER]).replace(
        "precision", "precisian"
    )
    + "\n\n## Retrieval\n\nRetrieval over consolidated summaries is cheaper but coarser.\n"
)
_MULTI_EDITED_PASSAGE = _MULTI_QUOTE.replace("precision", "precisian")


def test_a_window_spans_consecutive_sentences_when_the_quote_outruns_one_sentence():
    """The three-sentence quote is longer than any single sentence of the
    paragraph it was captured from, so a window bounded by one sentence can
    never cover it. At least one proposed window must span the whole passage.
    """
    passage_start = _MULTI_HEAD_TEXT.index(_MULTI_EDITED_PASSAGE)
    passage_end = passage_start + len(_MULTI_EDITED_PASSAGE)
    assert len(_MULTI_FIRST) < len(_MULTI_QUOTE), (
        "fixture must actually make the quote outrun its own first sentence"
    )

    candidates = generate_candidates(_MULTI_QUOTE, _MULTI_HEAD_TEXT)

    assert _valid_windows(candidates, _MULTI_HEAD_TEXT)
    covering = [
        (start, end) for start, end in candidates if start <= passage_start and end >= passage_end
    ]
    assert covering, (
        "a quote spanning three consecutive sentences must produce at least one window "
        "that covers all three -- a window clipped to the single sentence its seed word "
        "fell in leaves the rest of the quote unmatchable at any threshold"
    )


def test_widening_across_sentences_never_crosses_a_structural_block_boundary():
    """Absorbing neighbouring sentences must not become a licence to absorb
    page chrome: the paragraph under study sits between two heading lines, and
    a window widened past either of them would spend the length cap on
    structure instead of prose -- the same failure the backward-boundary fix
    already ruled out for single-sentence windows.

    Scoped to the windows that actually cover the passage. A seed word that
    happens to occur *inside* a heading line legitimately seeds its own window
    there; that is the seeding rule, not the extension rule, and is not what
    this test is about.
    """
    passage_start = _MULTI_HEAD_TEXT.index(_MULTI_EDITED_PASSAGE)
    passage_end = passage_start + len(_MULTI_EDITED_PASSAGE)

    candidates = generate_candidates(_MULTI_QUOTE, _MULTI_HEAD_TEXT)

    chrome = [
        (start, end)
        for start, end in candidates
        if start < passage_end
        and end > passage_start
        and (
            "# Agent memory" in _MULTI_HEAD_TEXT[start:end]
            or "## Consolidation" in _MULTI_HEAD_TEXT[start:end]
            or "## Retrieval" in _MULTI_HEAD_TEXT[start:end]
        )
    ]
    assert not chrome, (
        "widening a window across sentence boundaries must still stop at the structural "
        f"block the seed occurrence sits in; these windows swallowed a heading: {chrome}"
    )
