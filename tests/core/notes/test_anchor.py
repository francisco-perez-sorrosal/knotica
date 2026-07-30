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
    derive_note_id,
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


def test_anchor_bullet_missing_its_quote_line_is_malformed_not_an_exception():
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
    assert document.anchors == ()
    assert document.skipped_anchor_count == 1


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
