"""Keyword candidate generation -- resolution ladder rung 4.

A pure function of two strings: no store, no vault handle, no config, no
I/O. This module only ever runs after the exact and shifted rungs have
already failed to find an anchor's ``quote`` verbatim in the current page
text -- its job is to propose a bounded set of plausible spans for
:mod:`knotica.core.notes.scoring` to rank, not to rank them itself.

Given ``(quote, head_text)``, :func:`generate_candidates`:

- picks the page-rarest words of ``quote`` -- starting the search at
  page-frequency 1 and relaxing to 2, 3, ... until at least
  :data:`MIN_SEED_WORDS` words qualify, then keeping *every* word at or
  below the frequency the search stopped at, never trimming back to
  exactly three. Relaxing broadens the net; words already collected at a
  tighter threshold stay in. This is a deliberate choice: any rule for
  trimming ties back down to three (alphabetical, first occurrence, quote
  order) would freeze an arbitrary tie-break into behaviour with nothing to
  justify it, and a slightly larger seed set costs a few extra candidate
  windows, not correctness;
- treats a proper noun as eligible regardless of frequency, seeding it
  unconditionally and letting it count toward the minimum like any other
  seed word;
- seeds a window at *every* occurrence of a chosen seed word, not merely
  its first, and extends each window to the boundaries of the sentence it
  falls in;
- caps every window's length at twice the character length of ``quote``,
  centred on the occurrence that seeded it, so a seed word buried in an
  unusually long run of prose does not produce an unbounded candidate. The
  cap is measured in characters -- ``generate_candidates`` returns
  character offsets, and every other length in the resolution ladder is
  character-based; a word-based cap would be the only unit discontinuity
  in the whole path.

A quote sharing no words at all with the page is a normal, expected input
here (not an error): it returns an empty tuple. Downstream, an empty
candidate set makes :func:`knotica.core.notes.scoring.score_candidates`
return ``None``, which the ladder's final rung consumes as "orphaned, no
guess" -- there is nothing for this module to raise about.

**Proper-noun heuristic -- no ground truth, a pragmatic guess.** A word
qualifies if at least one of its page occurrences is capitalized and is
*not* the first word of its sentence. This gets real things wrong: a
proper noun that only ever appears at the start of a sentence is missed
entirely; a common word that happens to be capitalized mid-sentence (title
case, emphasis, a heading fragment) is misclassified as one; the check is
ASCII/Latin-script capitalization only, so it says nothing about scripts
without a case distinction. Treat it as a cheap net that catches the
common case, not a linguistic classifier.

**Sentence-boundary heuristic -- also pragmatic.** A sentence is text
between one ``.``/``!``/``?`` followed by whitespace and the next. This
breaks on abbreviations ("Dr. Smith arrived." splits after "Dr."), and on
any other mid-sentence period followed by whitespace. It does *not* break
on decimals ("a rate of 3.14 percent") since the period there is not
followed by whitespace, and it treats an ellipsis as an ordinary sentence
end, which is usually close enough for a window-extension heuristic that
only needs a plausible boundary, not a linguistically exact one.
"""

from __future__ import annotations

import re

__all__ = ["CAP_MULTIPLIER", "MIN_SEED_WORDS", "generate_candidates"]

# Rung 4 stops relaxing the frequency ceiling once at least this many seed
# words are held -- see the module docstring for why the set is never
# trimmed back down to exactly this number.
MIN_SEED_WORDS = 3

# A seeded window is capped at this many times the quote's own character
# length.
CAP_MULTIPLIER = 2

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def generate_candidates(quote: str, head_text: str) -> tuple[tuple[int, int], ...]:
    """Candidate ``(start, end)`` windows into ``head_text`` for ``quote``.

    See the module docstring for the seeding, extension, and capping rules.
    Returns an empty tuple, never raises, when ``quote`` shares no words at
    all with ``head_text``.
    """
    sentence_spans = _sentence_spans(head_text)
    page_words, proper_noun_words = _index_page_words(head_text, sentence_spans)

    quote_words = _unique_words(quote)
    frequencies = {word: len(page_words.get(word, ())) for word in quote_words}
    seed_words = _select_seed_words(quote_words, frequencies, proper_noun_words)

    cap = CAP_MULTIPLIER * len(quote)
    windows: set[tuple[int, int]] = set()
    for word in seed_words:
        for word_start, _word_end in page_words[word]:
            sentence_start, sentence_end = _sentence_containing(word_start, sentence_spans)
            windows.add(_clip_to_cap(sentence_start, sentence_end, word_start, cap))
    return tuple(sorted(windows))


def _select_seed_words(
    quote_words: list[str],
    frequencies: dict[str, int],
    proper_noun_words: set[str],
) -> set[str]:
    """The rarity search: relax the frequency ceiling until at least
    :data:`MIN_SEED_WORDS` words qualify, then stop -- keeping every word at
    or below the stopping ceiling. Proper nouns are added unconditionally
    and count toward the minimum from the start.
    """
    present = [word for word in quote_words if frequencies.get(word, 0) > 0]
    seed = {word for word in present if word in proper_noun_words}
    if not present:
        return seed

    highest_frequency = max(frequencies[word] for word in present)
    ceiling = 1
    while len(seed) < MIN_SEED_WORDS and ceiling <= highest_frequency:
        seed |= {word for word in present if frequencies[word] <= ceiling}
        ceiling += 1
    return seed


def _index_page_words(
    head_text: str, sentence_spans: tuple[tuple[int, int], ...]
) -> tuple[dict[str, list[tuple[int, int]]], set[str]]:
    """Every alphanumeric token in ``head_text``, grouped by lowercased word
    and paired with its ``(start, end)`` occurrences, plus the subset of
    words eligible under the proper-noun heuristic.
    """
    occurrences: dict[str, list[tuple[int, int]]] = {}
    proper_noun_words: set[str] = set()
    for sentence_start, sentence_end in sentence_spans:
        sentence_initial = True
        for match in _WORD_RE.finditer(head_text, sentence_start, sentence_end):
            token = match.group(0)
            word = token.lower()
            occurrences.setdefault(word, []).append((match.start(), match.end()))
            if not sentence_initial and token[:1].isupper():
                proper_noun_words.add(word)
            sentence_initial = False
    return occurrences, proper_noun_words


def _unique_words(text: str) -> list[str]:
    """Every distinct word in ``text``, lowercased, in first-appearance order."""
    seen: set[str] = set()
    words: list[str] = []
    for match in _WORD_RE.finditer(text):
        word = match.group(0).lower()
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """``text`` split into sentence ``(start, end)`` spans. See the module
    docstring for the boundary heuristic and where it breaks.
    """
    if not text:
        return ()
    spans = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return tuple(spans)


def _sentence_containing(
    offset: int, sentence_spans: tuple[tuple[int, int], ...]
) -> tuple[int, int]:
    """The span of the sentence containing character ``offset``."""
    for start, end in sentence_spans:
        if start <= offset < end:
            return start, end
    return sentence_spans[-1]


def _clip_to_cap(
    sentence_start: int, sentence_end: int, word_start: int, cap: int
) -> tuple[int, int]:
    """Clip a sentence-bounded window to at most ``cap`` characters,
    centred on the occurrence at ``word_start`` -- a seed word buried deep
    in an otherwise-uncapped sentence still gets a bounded window around
    where it actually occurs, not an arbitrary slice of the sentence.
    """
    if sentence_end - sentence_start <= cap:
        return sentence_start, sentence_end
    half = cap // 2
    start = max(sentence_start, word_start - half)
    end = min(sentence_end, start + cap)
    start = max(sentence_start, end - cap)
    return start, end
