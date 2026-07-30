"""Behavioral contract of the anchor resolution ladder (steps 0-3 only).

Derived from the notes-overlay resolution ladder, not from any implementation.
``resolve_anchor`` is a pure function of two text blobs and an anchor of
record -- it takes no store, no vault handle, no lock, and produces no writes.
Given ``(historical_text, head_text, anchor)`` it answers one of four
outcomes:

- the quote is found in ``head_text`` at the same offset it held in
  ``historical_text`` -- ``exact``, ``span`` fidelity;
- the quote is found in ``head_text`` at a different offset (the nearest
  occurrence to the historical offset wins when the quote repeats) --
  ``shifted``, ``span`` fidelity;
- the page itself is gone at HEAD (an absent or empty ``head_text``, standing
  in for "this page was deleted or renamed") -- ``orphaned``, ``topic``
  fidelity, no guessed span;
- the page still exists but the quote cannot be found in it at all --
  ``orphaned``, ``page`` fidelity, no guessed span. Phase 1 stops the ladder
  here -- there is no fuzzy matching, no keyword scoring, no best-guess span
  to fall back to yet.

A fifth outcome exists for a different failure entirely: the quote is not
even in the *historical* text the anchor claims to pin. That is not "the
wiki moved on" -- it is "this anchor was never valid," a hand-corrupted or
forged record. It is reported as ``anchor-invalid``, is checked before any
comparison against ``head_text`` is even attempted, and must never collapse
into the ``orphaned`` outcomes above: the two mean opposite things to a
reader deciding whether to trust their own reflection.
"""

import dataclasses
import inspect

from knotica.core.notes.anchor import AnchorRecord
from knotica.core.notes.resolve import Projection, resolve_anchor


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

    projection = resolve_anchor(text, text, anchor)

    assert projection.status == "exact"
    assert projection.fidelity == "span"
    assert projection.span == (offset, offset + len(quote))


def test_quote_at_the_very_start_of_the_page_resolves_exact():
    quote = "reward hacking is just goodhart with extra steps"
    text = f"{quote}\n\nA page that opens directly with the pinned quote, no preamble."
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(text, text, anchor)

    assert projection.status == "exact"
    assert projection.span == (0, len(quote))


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

    projection = resolve_anchor(historical_text, historical_text, anchor)

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

    projection = resolve_anchor(historical_text, head_text, anchor)

    assert projection.status == "shifted"
    assert projection.fidelity == "span"
    assert projection.span == (head_offset, head_offset + len(quote))


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

    projection = resolve_anchor(historical_text, head_text, anchor)

    assert projection.status == "shifted"
    assert projection.span == (nearest_offset, nearest_offset + len(quote))


# ---------------------------------------------------------------------------
# Orphaned -- page gone at HEAD
# ---------------------------------------------------------------------------


def test_deleted_page_signalled_by_none_resolves_orphaned_at_topic_fidelity_with_no_span():
    historical_text = "The page used to say: the metric quietly became the goal itself."
    anchor = _anchor(quote="the metric quietly became the goal itself")

    projection = resolve_anchor(historical_text, None, anchor)

    assert projection.status == "orphaned"
    assert projection.fidelity == "topic"
    assert projection.span is None


def test_deleted_page_signalled_by_empty_text_resolves_orphaned_at_topic_fidelity():
    historical_text = "The page used to say: the metric quietly became the goal itself."
    anchor = _anchor(quote="the metric quietly became the goal itself")

    projection = resolve_anchor(historical_text, "", anchor)

    assert projection.status == "orphaned"
    assert projection.fidelity == "topic"
    assert projection.span is None


# ---------------------------------------------------------------------------
# Orphaned -- page present, quote gone (Phase 1's ceiling: no best-guess span)
# ---------------------------------------------------------------------------


def test_quote_absent_from_an_otherwise_intact_page_resolves_orphaned_at_page_fidelity():
    quote = "the model learns to satisfy the metric rather than the goal"
    historical_text = f"Preface.\n\n{quote}\n\nClosing thoughts."
    head_text = (
        "Preface.\n\nThis paragraph was rewritten entirely and no longer contains "
        "anything resembling the original wording.\n\nClosing thoughts."
    )
    anchor = _anchor(quote=quote)

    projection = resolve_anchor(historical_text, head_text, anchor)

    assert projection.status == "orphaned"
    assert projection.fidelity == "page"
    assert projection.span is None


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

    projection = resolve_anchor(historical_text, head_text, anchor)

    assert projection.status == "anchor-invalid"
    assert projection.status != "orphaned"
    assert projection.span is None


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

    projection = resolve_anchor(historical_text, head_text, anchor)

    assert projection.status == "anchor-invalid"
    assert projection.status != "exact"
    assert projection.status != "shifted"
    assert projection.span is None


def test_resolving_an_invalid_anchor_never_raises():
    historical_text = "Nothing here matches anything the anchor claims."
    anchor = _anchor(quote="a quote that was never written on this page")

    # A crash here (an uncaught KeyError/ValueError/etc.) would itself be the
    # failure -- data-integrity problems in hand-edited files are reported as
    # data, never propagated as exceptions.
    projection = resolve_anchor(historical_text, historical_text, anchor)

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

    first_pass_a = resolve_anchor(historical_text, head_text, anchor_a)
    first_pass_b = resolve_anchor(historical_text, head_text, anchor_b)
    second_pass_b = resolve_anchor(historical_text, head_text, anchor_b)
    second_pass_a = resolve_anchor(historical_text, head_text, anchor_a)

    assert first_pass_a.status == "exact"
    assert first_pass_b.status == "orphaned"
    assert first_pass_b.fidelity == "page"
    assert first_pass_a == second_pass_a
    assert first_pass_b == second_pass_b


# ---------------------------------------------------------------------------
# Zero writes -- a type-level guarantee, not a runtime assertion
# ---------------------------------------------------------------------------


def test_resolve_anchor_signature_accepts_only_the_two_text_blobs_and_the_anchor():
    """Resolution must be expressible as a pure function of two strings and
    an anchor of record -- no vault handle, no transaction, no lock, nothing
    that would make the call anything other than free and re-runnable against
    any HEAD.
    """
    parameters = list(inspect.signature(resolve_anchor).parameters)

    assert parameters == ["historical_text", "head_text", "anchor"]


def test_projection_is_an_immutable_value_type():
    quote = "a quote used only to build a projection instance"
    projection = resolve_anchor(quote, quote, _anchor(quote=quote))

    assert dataclasses.is_dataclass(Projection)
    with_frozen_error = dataclasses.FrozenInstanceError
    try:
        projection.status = "exact"  # type: ignore[misc]
    except with_frozen_error:
        pass
    else:
        raise AssertionError("Projection must be frozen -- resolution results cannot be mutated")
