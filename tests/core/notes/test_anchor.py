"""Behavioral contract of the note anchor/document format.

Derived from the notes-overlay grammar (frontmatter shape, the ``## Anchors``
bullet grammar, filename/id derivation), not from any implementation:

- a document built from :class:`NoteDocument`/:class:`AnchorRecord` survives a
  serialize/parse round trip field-for-field;
- id/slug derivation is ASCII-safe, length-bounded, and idempotent under a
  same-second collision (``-b``, ``-c``, ... suffixing);
- a note may carry multiple independent anchors, and an anchor may target a
  page filed under a different topic than the note itself;
- a hand-authored file (typed by a human in Obsidian, not produced by
  ``serialize_note``) parses identically to a tool-captured one: extra
  whitespace around separators, an absent ``at=`` disambiguator, and an
  absent ``#Heading`` are all accepted, and a genuinely malformed anchor
  bullet is counted and skipped rather than raised -- the note stays
  readable and any valid bullets survive alongside the broken one;
- a note missing a required frontmatter field is reported as a parse failure
  for that file (data, not an exception), mirroring the page model's
  ``(value, error)`` contract; every other field defaults per the grammar;
- a fidelity value from a later grammar generation is carried through as an
  opaque string rather than rejected, so a note written by a newer knotica
  version does not break an older reader.
"""

from pathlib import Path

import pytest
from knotica.core.notes.anchor import (
    AnchorRecord,
    NoteDocument,
    anchor_of_record,
    derive_note_id,
    escape_anchors_heading,
    parse_note,
    serialize_note,
)

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "notes"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _anchor(**overrides: object) -> AnchorRecord:
    """A minimal, valid span-fidelity anchor; override only what a test needs."""
    defaults: dict[str, object] = {
        "page": "agentic-systems/agent-memory.md",
        "heading": "Working memory",
        "fidelity": "span",
        "pinned_at": "9f1a3c0",
        "quote": 'the model has no persistent notion of "the goal"',
        "start": None,
    }
    defaults.update(overrides)
    return AnchorRecord(**defaults)


def _note(**overrides: object) -> NoteDocument:
    """A minimal, valid note; override only what a test needs."""
    defaults: dict[str, object] = {
        "id": "20260703-081500-goodhart-is-inevitable",
        "topic": "agentic-systems",
        "intent": "reflection",
        "created": "2026-07-03T08:15:00Z",
        "updated": "2026-07-03T08:15:00Z",
        "status": "active",
        "tags": ("metrics", "incentives"),
        "body": "Optimizing the eval and optimizing the goal quietly diverge.",
        "anchors": (_anchor(),),
        "skipped_anchor_count": 0,
    }
    defaults.update(overrides)
    return NoteDocument(**defaults)


def _always_available(_stem: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_serialize_then_parse_round_trips_every_field_of_a_single_anchor_note():
    original = _note()

    reparsed, error = parse_note(serialize_note(original))

    assert error is None
    assert reparsed == original


def test_serialize_then_parse_round_trips_a_note_with_no_anchors():
    original = _note(anchors=(), skipped_anchor_count=0)

    reparsed, error = parse_note(serialize_note(original))

    assert error is None
    assert reparsed is not None
    assert reparsed.anchors == ()


def test_serialize_then_parse_round_trips_multiple_independent_anchors():
    first = _anchor(
        page="agentic-systems/agent-memory.md",
        heading="Working memory",
        quote="the model has no persistent notion of the goal",
    )
    second = _anchor(
        page="agentic-systems/alignment-failures.md",
        heading="Reward hacking",
        pinned_at="a3f9c21",
        quote="the model learns to satisfy the metric rather than the goal",
        start=12,
    )
    original = _note(anchors=(first, second), skipped_anchor_count=0)

    reparsed, error = parse_note(serialize_note(original))

    assert error is None
    assert reparsed is not None
    assert reparsed.anchors == (first, second)


# ---------------------------------------------------------------------------
# id / slug derivation
# ---------------------------------------------------------------------------


def test_derive_note_id_starts_with_a_compact_utc_timestamp():
    note_id = derive_note_id(
        "Reward hacking is just Goodhart with extra steps",
        "2026-07-29T14:22:11Z",
        existing=_always_available,
    )

    assert note_id.startswith("20260729-142211-")


def test_derive_note_id_produces_an_ascii_hyphenated_lowercase_slug():
    note_id = derive_note_id(
        "Café résumé — naïve façade???",
        "2026-07-29T14:22:11Z",
        existing=_always_available,
    )
    slug = note_id.removeprefix("20260729-142211-")

    assert slug == slug.lower()
    assert slug.isascii()
    assert " " not in slug


def test_derive_note_id_truncates_the_slug_to_forty_characters():
    long_text = "This reflection has an extremely long first line that goes on and on and on"
    note_id = derive_note_id(long_text, "2026-07-29T14:22:11Z", existing=_always_available)
    slug = note_id.removeprefix("20260729-142211-")

    assert len(slug) <= 40


def test_derive_note_id_prefers_the_note_own_first_heading_over_body_text():
    text = "# Reward hacking is just Goodhart\n\nSome unrelated opening body sentence here."
    note_id = derive_note_id(text, "2026-07-29T14:22:11Z", existing=_always_available)

    assert "reward-hacking" in note_id


def test_derive_note_id_appends_a_suffix_on_a_same_second_collision():
    def base_stem_already_taken(stem: str) -> bool:
        return not stem.endswith("-b") and not stem.endswith("-c")

    note_id = derive_note_id(
        "Reward hacking is just Goodhart with extra steps",
        "2026-07-29T14:22:11Z",
        existing=base_stem_already_taken,
    )

    assert note_id.endswith("-b")


def test_derive_note_id_advances_the_suffix_when_the_first_fallback_also_collides():
    def base_and_b_taken(stem: str) -> bool:
        return not stem.endswith("-c")

    note_id = derive_note_id(
        "Reward hacking is just Goodhart with extra steps",
        "2026-07-29T14:22:11Z",
        existing=base_and_b_taken,
    )

    assert note_id.endswith("-c")


# ---------------------------------------------------------------------------
# Cross-topic anchors
# ---------------------------------------------------------------------------


def test_anchor_target_topic_may_differ_from_the_note_own_filing_topic():
    document, error = parse_note(_read_fixture("cross_topic_anchor.md"))

    assert error is None
    assert document is not None
    assert document.topic == "agentic-systems"
    assert len(document.anchors) == 1
    assert document.anchors[0].page == "ml-fundamentals/loss-landscapes.md"
    assert document.anchors[0].fidelity == "span"


# ---------------------------------------------------------------------------
# Hand-authored tolerance (the fourth capture surface: a human typing in Obsidian)
# ---------------------------------------------------------------------------


def test_hand_authored_clean_note_reads_identically_to_a_tool_captured_one():
    document, error = parse_note(_read_fixture("clean_note.md"))

    assert error is None
    assert document is not None
    assert document.id == "20260703-081500-goodhart-is-inevitable"
    assert document.topic == "agentic-systems"
    assert len(document.anchors) == 1
    assert document.anchors[0].fidelity == "span"
    assert document.anchors[0].heading == "Working memory"
    assert document.skipped_anchor_count == 0


def test_hand_authored_anchor_tolerates_irregular_separator_whitespace_and_missing_heading():
    document, error = parse_note(_read_fixture("tolerant_grammar.md"))

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == "agentic-systems/agent-memory.md"
    assert anchor.heading == ""
    assert anchor.fidelity == "page"
    assert anchor.pinned_at == "44b1d02"
    assert anchor.start is None
    assert document.skipped_anchor_count == 0


def test_hand_authored_broken_anchor_is_skipped_while_the_valid_one_survives():
    document, error = parse_note(_read_fixture("broken_anchor.md"))

    assert error is None
    assert document is not None
    assert document.skipped_anchor_count == 1
    assert len(document.anchors) == 1
    assert document.anchors[0].heading == "Working memory"
    assert document.anchors[0].pinned_at == "7cc90ab"


def test_hand_authored_bare_anchors_heading_with_zero_bullets_is_a_valid_note():
    document, error = parse_note(_read_fixture("bare_anchors_heading.md"))

    assert error is None
    assert document is not None
    assert document.anchors == ()
    assert document.skipped_anchor_count == 0


def test_note_with_no_anchors_section_at_all_is_a_valid_topic_fidelity_note():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260708-080000-no-anchors-section\n"
        "topic: agentic-systems\n"
        "created: 2026-07-08T08:00:00Z\n"
        "---\n"
        "\n"
        "Just a loose thought, never even opened the Anchors section.\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.anchors == ()
    assert document.skipped_anchor_count == 0


def test_anchor_bullet_with_no_quote_line_at_all_is_valid_with_an_empty_quote():
    """Correction to the frozen grammar: the blockquote is optional, not required.

    A bullet naming a page and a fidelity but supplying no quote records that
    "no quote was supplied" honestly (``quote == ""``) instead of being
    discarded as malformed -- the earlier grammar made this shape
    unconstructible, which is a hole, not a valid rejection.
    """
    text = (
        "---\n"
        "type: note\n"
        "id: 20260709-080000-forgot-the-quote\n"
        "topic: agentic-systems\n"
        "created: 2026-07-09T08:00:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.skipped_anchor_count == 0
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == "agentic-systems/agent-memory.md"
    assert anchor.heading == "Working memory"
    assert anchor.fidelity == "span"
    assert anchor.pinned_at == "9f1a3c0"
    assert anchor.quote == ""


def test_anchor_line_at_disambiguator_is_parsed_into_the_start_field():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260710-080000-quote-repeats-on-the-page\n"
        "topic: agentic-systems\n"
        "created: 2026-07-10T08:00:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · "
        "pinned@`9f1a3c0` · at=142\n"
        "  > the model has no persistent notion of the goal\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    assert document.anchors[0].start == 142


def test_forward_generation_fidelity_value_is_carried_through_as_an_opaque_string():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260711-080000-written-by-a-newer-knotica\n"
        "topic: agentic-systems\n"
        "created: 2026-07-11T08:00:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `block` · pinned@`9f1a3c0`\n"
        "  > a future-generation fidelity value this reader has never heard of\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    assert document.anchors[0].fidelity == "block"

    reparsed, reparse_error = parse_note(serialize_note(document))

    assert reparse_error is None
    assert reparsed is not None
    assert reparsed.anchors[0].fidelity == "block"


# ---------------------------------------------------------------------------
# Relaxed anchor-line grammar: the wikilink and the blockquote are each
# independently optional. The (fidelity, pinned@) pair is what remains
# required -- it is the signature that distinguishes an anchor bullet from an
# ordinary list item, and it must not weaken alongside the two relaxations.
# ---------------------------------------------------------------------------


def test_anchor_bullet_without_a_wikilink_pins_at_topic_fidelity_with_the_quote_preserved():
    """No page could be verified, but the passage the user reacted to must
    never be lost to a degraded capture -- the wikilink is optional, the
    quote is what stays.
    """
    text = (
        "---\n"
        "type: note\n"
        "id: 20260730-080000-linkless-topic-anchor\n"
        "topic: agentic-systems\n"
        "created: 2026-07-30T08:00:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- `topic` · pinned@`a3f9c21`\n"
        "  > the passage the user was reacting to, preserved verbatim\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.skipped_anchor_count == 0
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == ""
    assert anchor.heading == ""
    assert anchor.fidelity == "topic"
    assert anchor.pinned_at == "a3f9c21"
    assert anchor.quote == "the passage the user was reacting to, preserved verbatim"

    reparsed, reparse_error = parse_note(serialize_note(document))

    assert reparse_error is None
    assert reparsed is not None
    assert reparsed.anchors == document.anchors


def test_anchor_bullet_without_a_blockquote_pins_with_an_empty_quote_not_malformed():
    """No quote was supplied at all -- the bullet still names the page it
    pins; it must not be confused with a malformed bullet.
    """
    text = (
        "---\n"
        "type: note\n"
        "id: 20260730-081500-quoteless-page-anchor\n"
        "topic: agentic-systems\n"
        "created: 2026-07-30T08:15:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory]] — `page` · pinned@`a3f9c21`\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.skipped_anchor_count == 0
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == "agentic-systems/agent-memory.md"
    assert anchor.heading == ""
    assert anchor.fidelity == "page"
    assert anchor.pinned_at == "a3f9c21"
    assert anchor.quote == ""

    reparsed, reparse_error = parse_note(serialize_note(document))

    assert reparse_error is None
    assert reparsed is not None
    assert reparsed.anchors == document.anchors


def test_a_bullet_with_neither_a_fidelity_nor_a_pinned_token_stays_malformed():
    """Regression probe: relaxing the wikilink and the blockquote must not
    also relax the (fidelity, pinned@) signature pair -- an ordinary list
    item under the heading is still not an anchor.
    """
    text = (
        "---\n"
        "type: note\n"
        "id: 20260730-083000-not-an-anchor-at-all\n"
        "topic: agentic-systems\n"
        "created: 2026-07-30T08:30:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- just a plain list item that happens to live under the heading\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.anchors == ()
    assert document.skipped_anchor_count == 1


def test_a_bullet_with_a_fidelity_token_but_no_pinned_token_stays_malformed():
    """Regression probe: half of the signature pair is still not enough."""
    text = (
        "---\n"
        "type: note\n"
        "id: 20260730-084500-fidelity-without-pin\n"
        "topic: agentic-systems\n"
        "created: 2026-07-30T08:45:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- `span` some prose that mentions a fidelity token but never pins anything\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.anchors == ()
    assert document.skipped_anchor_count == 1


def test_a_bullet_with_a_pinned_token_but_no_fidelity_token_stays_malformed():
    """Regression probe: the other half of the signature pair, alone, is
    also still not enough.
    """
    text = (
        "---\n"
        "type: note\n"
        "id: 20260730-090000-pin-without-fidelity\n"
        "topic: agentic-systems\n"
        "created: 2026-07-30T09:00:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- pinned@`a3f9c21` but with no fidelity token at all\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.anchors == ()
    assert document.skipped_anchor_count == 1


# ---------------------------------------------------------------------------
# Required vs. defaultable frontmatter fields
# ---------------------------------------------------------------------------

_MINIMAL_FRONTMATTER_LINES: dict[str, str] = {
    "type": "type: note",
    "id": "id: 20260712-080000-minimal-frontmatter",
    "topic": "topic: agentic-systems",
    "created": "created: 2026-07-12T08:00:00Z",
}


def _note_text_missing(field: str) -> str:
    lines = [line for key, line in _MINIMAL_FRONTMATTER_LINES.items() if key != field]
    return "---\n" + "\n".join(lines) + "\n---\n\nBody text with no anchors at all.\n"


@pytest.mark.parametrize("missing_field", ["type", "id", "topic", "created"])
def test_missing_required_frontmatter_field_is_a_parse_failure_not_an_exception(missing_field):
    document, error = parse_note(_note_text_missing(missing_field))

    assert document is None
    assert error is not None
    assert missing_field in error


def test_minimal_frontmatter_fills_every_defaultable_field():
    text = "---\n" + "\n".join(_MINIMAL_FRONTMATTER_LINES.values()) + "\n---\n\nNo anchors yet.\n"

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.intent == "reflection"
    assert document.updated == document.created
    assert document.status == "active"
    assert document.tags == ()


# ---------------------------------------------------------------------------
# Structural ambiguity around the "## Anchors" sentinel
#
# The bullet grammar's tolerance (above) covers malformed *lines*. These cover
# a different surface entirely: a body whose free-text prose or heading
# structure confuses where the anchors section starts, ends, or how many
# there are. The invariant every case below is held to is the same one the
# whole module is built on -- a valid anchor is never lost, and no user prose
# ever disappears without a trace -- not any one parser mechanism. Where a
# shape is genuinely ambiguous, the assertions below check only the guarantee
# and deliberately leave the resolution mechanism to the implementer.
# ---------------------------------------------------------------------------


def test_a_literal_anchors_heading_line_in_body_prose_does_not_swallow_the_real_section():
    """A hand-authored note *about* the anchor format itself: its explanatory
    prose contains a line that is, verbatim, an ``## Anchors`` heading, well
    before the note's actual anchors section. The real section -- and its one
    valid bullet -- must still be found, and neither prose paragraph around
    the false heading may vanish.
    """
    document, error = parse_note(_read_fixture("anchors_heading_in_prose.md"))

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    assert document.anchors[0].heading == "Working memory"
    assert document.anchors[0].pinned_at == "9f1a3c0"
    assert document.skipped_anchor_count == 0
    assert "I keep forgetting exactly how the anchors section" in document.body
    assert "That's literally it, just the two words" in document.body


def test_anchors_are_recovered_from_every_anchors_section_present_not_just_the_first():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260714-080000-two-separate-trains-of-thought\n"
        "topic: agentic-systems\n"
        "created: 2026-07-14T08:00:00Z\n"
        "---\n"
        "\n"
        "# Two separate trains of thought\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
        "  > the model has no persistent notion of the goal\n"
        "\n"
        "A second thought struck me later in the same sitting.\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/alignment-failures#Reward hacking]] — `span` · "
        "pinned@`a3f9c21`\n"
        "  > the model learns to satisfy the metric rather than the goal\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.skipped_anchor_count == 0
    assert {anchor.heading for anchor in document.anchors} == {"Working memory", "Reward hacking"}
    assert "A second thought struck me later in the same sitting" in document.body


def test_prose_after_a_later_heading_that_follows_the_anchors_section_is_not_discarded():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260714-081500-something-occurred-to-me-afterward\n"
        "topic: agentic-systems\n"
        "created: 2026-07-14T08:15:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
        "  > the model has no persistent notion of the goal\n"
        "\n"
        "## Later thoughts\n"
        "\n"
        "I realized something else afterward that deserves to survive too.\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    assert "I realized something else afterward that deserves to survive too" in document.body


def test_prose_between_two_valid_anchor_bullets_is_not_silently_discarded():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260714-083000-a-connecting-thought-between-two-anchors\n"
        "topic: agentic-systems\n"
        "created: 2026-07-14T08:30:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
        "  > the model has no persistent notion of the goal\n"
        "\n"
        "Some connecting prose sentence here before the next one.\n"
        "\n"
        "- [[agentic-systems/alignment-failures#Reward hacking]] — `span` · "
        "pinned@`a3f9c21`\n"
        "  > the model learns to satisfy the metric rather than the goal\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert {anchor.heading for anchor in document.anchors} == {"Working memory", "Reward hacking"}
    assert document.skipped_anchor_count == 0
    assert "Some connecting prose sentence here before the next one" in document.body


def test_anchors_heading_as_the_very_first_line_needs_no_preceding_body():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260714-084500-straight-to-the-point\n"
        "topic: agentic-systems\n"
        "created: 2026-07-14T08:45:00Z\n"
        "---\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
        "  > the model has no persistent notion of the goal\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    assert document.anchors[0].heading == "Working memory"


def test_a_wrong_level_anchors_heading_is_not_treated_as_the_anchors_section():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260714-090000-not-actually-a-section\n"
        "topic: agentic-systems\n"
        "created: 2026-07-14T09:00:00Z\n"
        "---\n"
        "\n"
        "### Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
        "  > this looks like an anchor bullet but the heading above it is one level "
        "too deep\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.anchors == ()
    assert document.skipped_anchor_count == 0
    assert "### Anchors" in document.body


def test_trailing_whitespace_on_the_anchors_heading_line_is_tolerated():
    text = (
        "---\n"
        "type: note\n"
        "id: 20260714-091500-typed-a-trailing-space-by-accident\n"
        "topic: agentic-systems\n"
        "created: 2026-07-14T09:15:00Z\n"
        "---\n"
        "\n"
        "## Anchors   \n"
        "\n"
        "- [[agentic-systems/agent-memory#Working memory]] — `span` · pinned@`9f1a3c0`\n"
        "  > the model has no persistent notion of the goal\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert len(document.anchors) == 1
    assert document.anchors[0].heading == "Working memory"


# ---------------------------------------------------------------------------
# The anchors-sentinel escape: what it may touch, and what it may not
#
# The escape exists so untrusted prose cannot forge an anchor record. That
# makes it a filter standing in front of *every* note body ever written, so
# its blast radius is the contract under test here: it must neutralize a line
# that would genuinely be read back as the sentinel, and leave every other
# byte exactly as the user typed it. A note body is the user's own words; a
# capture that quietly rewrites them fails the feature's whole premise.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("form feed", "before\x0cafter"),
        ("next line", "before\x85after"),
        ("line separator", "before\u2028after"),
        ("paragraph separator", "before\u2029after"),
        ("vertical tab", "before\x0bafter"),
        ("trailing newline", "a paragraph pasted with its trailing newline\n"),
        ("windows line endings", "first line\r\nsecond line\r\n"),
        ("blank", ""),
    ],
)
def test_escaping_prose_with_no_sentinel_leaves_every_byte_untouched(label: str, body: str):
    """Text pasted from a PDF, a Word document, or a JS-serialized source
    routinely carries separators that ``str.splitlines()`` treats as line
    breaks -- exactly the provenance a "capture the passage that provoked
    you" feature invites. The escape has business with one line shape and no
    other, so a body that contains no sentinel must survive it byte for byte.
    """
    assert escape_anchors_heading(body) == body, f"the escape rewrote a body containing a {label}"


def test_an_unfenced_anchors_heading_is_still_neutralized():
    """Negative control for the fidelity assertions above: the escape must
    still do the one job it exists for.
    """
    escaped = escape_anchors_heading("thinking out loud\n\n## Anchors\n\nand then some more")

    assert "\n## Anchors\n" not in f"\n{escaped}\n"
    assert "Anchors" in escaped, "the user's own word must still be readable in the line"


def test_an_anchors_heading_inside_a_fenced_code_block_is_left_verbatim():
    """A note *about* the anchor format, fenced the way anyone would fence it.

    Inside a fence a backslash is not an escape character, so escaping there
    would show the reader the literal characters ``\\#\\# Anchors`` in place of
    the heading they deliberately quoted.
    """
    body = "The section looks like this:\n\n```\n## Anchors\n\n- `topic` · pinned@`a3f9c21`\n```\n"

    assert escape_anchors_heading(body) == body


def test_a_dangling_fence_does_not_shield_a_later_anchors_heading():
    """An unterminated fence is not a code block -- treating it as one would
    let a stray triple-backtick anywhere above the sentinel disarm the escape,
    and would also hide the real anchors section from the parser on read-back.
    """
    escaped = escape_anchors_heading("```\nthe fence I never closed\n\n## Anchors\n")

    assert "\n## Anchors\n" not in escaped


def test_a_fenced_anchor_bullet_is_read_back_as_prose_not_as_an_anchor():
    """The read side of the same rule: what the escape declines to touch, the
    parser must decline to honor. Otherwise a fenced example would be escaped
    on write and promoted on read, and the two would disagree about the same
    line.
    """
    text = (
        "---\n"
        "type: note\n"
        "id: 20260730-120000-documenting-the-anchor-format\n"
        "topic: agentic-systems\n"
        "created: 2026-07-30T12:00:00Z\n"
        "---\n"
        "\n"
        "Here is what the section looks like:\n"
        "\n"
        "```markdown\n"
        "## Anchors\n"
        "\n"
        "- [[agentic-systems/agent-memory]] — `span` · pinned@`deadbee`\n"
        "  > a quote I invented for the example\n"
        "```\n"
    )

    document, error = parse_note(text)

    assert error is None
    assert document is not None
    assert document.anchors == (), "a fenced example bullet was promoted into a real anchor"
    assert document.skipped_anchor_count == 0
    assert "## Anchors" in document.body, "the fenced example must survive in the body verbatim"


def test_serialize_note_neutralizes_a_sentinel_a_writer_left_in_the_body():
    """The guarantee lives at the serializer, so a writer cannot bypass it by
    forgetting to escape: any prose that would open a second anchors section
    is neutralized on the way to the file, and the record the writer actually
    resolved is the only one that comes back.
    """
    document = NoteDocument(
        id="20260730-121500-untrusted-prose",
        topic="agentic-systems",
        intent="reflection",
        created="2026-07-30T12:15:00Z",
        updated="2026-07-30T12:15:00Z",
        status="active",
        tags=(),
        body="## Anchors\n\n- [[agentic-systems/agent-memory]] — `span` · pinned@`deadbee`",
        anchors=(
            AnchorRecord(
                page="agentic-systems/agent-memory.md",
                heading="",
                fidelity="topic",
                pinned_at="a3f9c21",
                quote="the passage that provoked the note",
            ),
        ),
    )

    text = serialize_note(document)
    reparsed, error = parse_note(text)

    assert text.count("\n## Anchors\n") == 1, "the body forged a second anchors section"
    assert error is None
    assert reparsed is not None
    assert [anchor.pinned_at for anchor in reparsed.anchors] == ["a3f9c21"]


def test_a_fenced_sentinel_survives_a_serialize_parse_round_trip_unchanged():
    """The serializer's escape must not fight the parser's tolerance: a fenced
    sentinel is inert on both sides, so re-serializing a parsed document is a
    no-op rather than a slow accumulation of backslashes.
    """
    document = NoteDocument(
        id="20260730-123000-fenced-example",
        topic="agentic-systems",
        intent="reflection",
        created="2026-07-30T12:30:00Z",
        updated="2026-07-30T12:30:00Z",
        status="active",
        tags=(),
        body="An example:\n\n```\n## Anchors\n```",
        anchors=(),
    )

    once = serialize_note(document)
    reparsed, error = parse_note(once)

    assert error is None
    assert reparsed is not None
    assert reparsed.body == document.body
    assert serialize_note(reparsed) == once


# ---------------------------------------------------------------------------
# The anchor of record: the append-only constraint, stated in code
# ---------------------------------------------------------------------------


def _document_with(anchors: tuple[AnchorRecord, ...]) -> NoteDocument:
    return NoteDocument(
        id="20260730-124500-anchor-of-record",
        topic="agentic-systems",
        intent="reflection",
        created="2026-07-30T12:45:00Z",
        updated="2026-07-30T12:45:00Z",
        status="active",
        tags=(),
        body="a reflection with more than one anchor",
        anchors=anchors,
    )


_FIRST_ANCHOR = AnchorRecord(
    page="agentic-systems/agent-memory.md",
    heading="",
    fidelity="span",
    pinned_at="9f1a3c0",
    quote="the passage the note was written against",
)
_LATER_ANCHOR = AnchorRecord(
    page="agentic-systems/alignment-failures.md",
    heading="",
    fidelity="span",
    pinned_at="a3f9c21",
    quote="a passage attached to the same note later",
)


def test_a_note_with_no_anchors_has_no_anchor_of_record():
    assert anchor_of_record(_document_with(())) is None


def test_appending_an_anchor_leaves_the_anchor_of_record_unchanged():
    """Capture idempotency fingerprints a note on its anchor of record, so a
    writer that appends must not disturb it -- otherwise every note captured
    before the append stops matching its own fingerprint and the next
    re-capture of unchanged text writes a duplicate file.
    """
    before = anchor_of_record(_document_with((_FIRST_ANCHOR,)))
    after = anchor_of_record(_document_with((_FIRST_ANCHOR, _LATER_ANCHOR)))

    assert before == _FIRST_ANCHOR
    assert after == before


def test_prepending_an_anchor_changes_the_anchor_of_record():
    """The failure this constraint forbids, pinned so it is visible rather than
    inferred: a writer that puts a new anchor first silently re-identifies the
    note.
    """
    assert anchor_of_record(_document_with((_LATER_ANCHOR, _FIRST_ANCHOR))) == _LATER_ANCHOR
