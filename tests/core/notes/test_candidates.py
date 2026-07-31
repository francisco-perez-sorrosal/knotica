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
"""

import inspect

from knotica.core.notes.candidates import generate_candidates

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
