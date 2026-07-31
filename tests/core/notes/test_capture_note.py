"""Behavioral contract of ``capture_note`` -- the one-shot capture write path.

The governing invariant, restated because every test below exists to defend
it: **``capture_note`` fails only for reasons unrelated to anchoring.** A
quote that cannot be found, a page that does not exist, several pages that
all match, or no quote at all -- none of these fail the call. Each degrades
the recorded fidelity and rides back as an ``ANCHOR_DEGRADED`` warning on a
*success* envelope. The user's reflection is durable before any anchor
quality is discussed. The only hard failures are an unknown topic, empty
note text, and an invalid ``intent`` -- none of which touch anchoring at all.

``pages`` is plural and client-supplied best-first: the caller is a model
that has just synthesized a passage and often cannot say which single page
it came from, so its honest claim is a ranked list, not a single guess. The
matching rule this file pins:

- quote found in exactly one supplied page -> span fidelity, pinned there.
- quote found in more than one supplied page -> topic fidelity, no page
  pinned (a guessed pin would be the silent-wrong-anchor failure the whole
  design exists to avoid), warning names the match count.
- quote found in none of the supplied pages, but at least one exists -> page
  fidelity, pinned to the first existing page, warning.
- none of the supplied pages exist -> topic fidelity, warning names the
  missing page(s).
- empty ``pages`` -> topic fidelity, no warning (nothing was claimed).

These tests call ``core.operations.capture_note`` directly (the MCP wire
contract, including any singular/plural adaptation at that boundary, is a
separate, later concern) and verify behavior against two sources of truth:
the envelope returned to the caller, and the persisted note file read back
through the already-landed ``core.notes.anchor``/``store`` modules -- never
against ``capture_note``'s own internals.
"""

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from knotica.core.loop import LoopRunner
from knotica.core.notes.anchor import parse_note
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_head_sha,
    git_status_porcelain,
    parse_knotica_commit,
    run_git,
)

TOPIC = "agentic-systems"
_CAPTURE_OP = "note_capture"
_LINKED_PAGE = f"{TOPIC}/agent-memory"
_EMBEDDED_PAGE = f"{TOPIC}/diagram"
_NOTE_ID_RE = re.compile(r"^\d{8}-\d{6}(-[a-z0-9-]+)?$")


# ---------------------------------------------------------------------------
# Deferred-import call helper (the RED trigger for this whole file)
# ---------------------------------------------------------------------------


def _capture(vault: Path, topic: str, note: str, **fields: object) -> Mapping[str, object]:
    """Invoke the operation under test; imported lazily so collection succeeds.

    ``capture_note`` is config-agnostic like every other operation: it takes
    an already-resolved ``store``, ``vault_root`` and ``vcs``, so the test
    constructs them directly on the throwaway vault. ``pages`` defaults to an
    empty sequence -- a note may claim no page at all.
    """
    from knotica.core.operations.capture_note import capture_note

    fields.setdefault("pages", ())
    result = capture_note(LocalFSStore(vault), vault, VaultVcs(vault), topic, note, **fields)
    assert isinstance(result, Mapping), f"expected an envelope mapping, got {result!r}"
    return result


def _success(result: Mapping[str, object]) -> Mapping[str, object]:
    assert "error" not in result, f"expected success, got an error envelope: {result!r}"
    return result


def _error_code(result: Mapping[str, object]) -> str:
    assert "error" in result, f"expected a failure envelope, got success: {result!r}"
    error = result["error"]
    assert isinstance(error, Mapping)
    return str(error["code"])


def _warnings(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    warnings = result.get("warnings", [])
    assert isinstance(warnings, list)
    return list(warnings)  # type: ignore[arg-type]


def _warning_codes(result: Mapping[str, object]) -> list[str]:
    return [str(w["code"]) for w in _warnings(result)]


def _warning_messages(result: Mapping[str, object]) -> list[str]:
    return [str(w["message"]) for w in _warnings(result)]


def _seed_page(vault: Path, relpath: str, content: str, message: str) -> str:
    """Write and commit a page, returning the resulting HEAD sha."""
    target = vault / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)
    return git_head_sha(vault)


def _read_captured_note(vault: Path, path: object) -> object:
    """Read a captured note file back through the real parser (ground truth)."""
    assert isinstance(path, str), f"expected a note path string, got {path!r}"
    document, error = parse_note((vault / path).read_text(encoding="utf-8"))
    assert error is None, f"the captured note must parse cleanly, got error: {error!r}"
    assert document is not None
    return document


# ---------------------------------------------------------------------------
# The happy path: a findable quote in one page pins at span fidelity.
# ---------------------------------------------------------------------------


def test_a_findable_quote_captures_at_span_fidelity_with_no_warning(template_vault: Path):
    page_relpath = f"{TOPIC}/reward-hacking.md"
    quote = "the model learns to satisfy the metric rather than the goal"
    _seed_page(
        template_vault,
        page_relpath,
        f"# Reward hacking\n\n{quote}, which is Goodhart's law with extra steps.\n",
        "test: seed reward-hacking page",
    )
    before_sha = git_head_sha(template_vault)

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "just Goodhart with extra steps",
            quote=quote,
            pages=[page_relpath],
        )
    )

    assert _warning_codes(result) == [], "an exact match must carry no anchor warning"
    note_id = result["note_id"]
    assert isinstance(note_id, str) and _NOTE_ID_RE.match(note_id), (
        f"note_id must follow the <timestamp>[-slug] grammar, got {note_id!r}"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert document.id == note_id
    assert document.topic == TOPIC
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == page_relpath
    assert anchor.quote == quote
    assert anchor.fidelity == "span"
    assert anchor.pinned_at == before_sha


def test_the_anchor_pins_at_the_head_sha_from_before_the_capture_commit(template_vault: Path):
    """A capture creates its own commit; the anchor must not describe that commit."""
    page_relpath = f"{TOPIC}/pinning-target.md"
    quote = "an unremarkable but exact sentence"
    _seed_page(
        template_vault,
        page_relpath,
        f"# Pinning target\n\n{quote}.\n",
        "test: seed pinning-target page",
    )
    before_sha = git_head_sha(template_vault)

    result = _success(
        _capture(template_vault, TOPIC, "worth remembering", quote=quote, pages=[page_relpath])
    )

    after_sha = git_head_sha(template_vault)
    document = _read_captured_note(template_vault, result["path"])
    assert document.anchors[0].pinned_at == before_sha, (
        "the anchor must record the vault state the user actually saw, not the "
        "capture's own new commit"
    )
    assert after_sha != before_sha, "sanity: the capture must have made a new commit"


# ---------------------------------------------------------------------------
# A single claimed page, degraded.
# ---------------------------------------------------------------------------


def test_an_unfindable_quote_in_the_only_claimed_page_still_captures_at_page_fidelity(
    template_vault: Path,
):
    page_relpath = f"{TOPIC}/eval-design.md"
    _seed_page(
        template_vault,
        page_relpath,
        "# Eval design\n\nNone of this text matches the quote below.\n",
        "test: seed eval-design page",
    )

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "I never bought that argument",
            quote="a passage that does not occur on this page",
            pages=[page_relpath],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    assert document.anchors[0].fidelity == "page"
    assert document.anchors[0].page == page_relpath


def test_when_the_only_claimed_page_does_not_exist_the_capture_pins_at_topic_fidelity(
    template_vault: Path,
):
    missing_page = f"{TOPIC}/does-not-exist.md"

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "filed against a page that turned out not to exist",
            quote="whatever the passage was",
            pages=[missing_page],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    assert any(missing_page in message for message in _warning_messages(result)), (
        "the warning must name the missing page"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == "", "no page could be verified -- the wikilink is omitted, not guessed"
    assert anchor.fidelity == "topic"
    assert anchor.quote == "whatever the passage was", (
        "the passage the user was reacting to must survive the degradation verbatim, even "
        "though no page could be verified"
    )


# ---------------------------------------------------------------------------
# Ruling: no quote supplied
# ---------------------------------------------------------------------------


def test_no_quote_with_at_least_one_existing_supplied_page_pins_at_page_fidelity(
    template_vault: Path,
):
    page_relpath = f"{TOPIC}/no-quote-target.md"
    _seed_page(
        template_vault,
        page_relpath,
        "# No quote target\n\nSome text that is never quoted.\n",
        "test: seed no-quote-target page",
    )

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "a reflection with no specific passage",
            quote="",
            pages=[page_relpath],
        )
    )

    assert _warning_codes(result) == [], (
        "naming a page that is verified to exist is a true statement even with no quote"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    assert document.anchors[0].fidelity == "page"
    assert document.anchors[0].page == page_relpath


def test_no_quote_and_no_pages_pins_at_topic_fidelity_without_a_warning(template_vault: Path):
    result = _success(
        _capture(template_vault, TOPIC, "a purely topical reflection", quote="", pages=())
    )

    assert _warning_codes(result) == [], "nothing was claimed, so nothing degraded"
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == ""
    assert anchor.fidelity == "topic"
    assert anchor.quote == "", "nothing was ever supplied to preserve"


# ---------------------------------------------------------------------------
# Repeated quote *within one matched page* -- distinct from the multi-page
# match case below; collapsing the two is the easy mistake.
# ---------------------------------------------------------------------------


def test_a_quote_repeated_within_a_single_matched_page_pins_the_first_occurrence(
    template_vault: Path,
):
    matching_page = f"{TOPIC}/repeated-quote.md"
    other_page = f"{TOPIC}/unrelated-page.md"
    quote = "the metric is not the goal"
    page_text = f"# Repeated quote\n\nFirst mention: {quote}. Later, restated: {quote} once more.\n"
    _seed_page(template_vault, matching_page, page_text, "test: seed repeated-quote page")
    _seed_page(
        template_vault,
        other_page,
        "# Unrelated page\n\nNothing here matches the quote.\n",
        "test: seed unrelated page",
    )
    expected_offset = page_text.find(quote)
    assert page_text.count(quote) > 1, "test setup must actually repeat the quote"

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "ambiguous within one page but should not error",
            quote=quote,
            pages=[matching_page, other_page],
        )
    )

    assert _warning_codes(result) == [], (
        "a within-page repeat resolved to a single matched page is not a degradation"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1, "exactly one page matched -- not the multi-page case"
    anchor = document.anchors[0]
    assert anchor.page == matching_page
    assert anchor.fidelity == "span"
    assert anchor.start == expected_offset


# ---------------------------------------------------------------------------
# Multi-page matching rule (several pages supplied, best-first)
# ---------------------------------------------------------------------------


def test_a_quote_found_in_exactly_one_of_several_claimed_pages_pins_span_fidelity(
    template_vault: Path,
):
    quote = "an eval that never sees the metric-as-goal case"
    page_a = f"{TOPIC}/candidate-a.md"
    page_b = f"{TOPIC}/candidate-b.md"
    page_c = f"{TOPIC}/candidate-c.md"
    _seed_page(template_vault, page_a, "# Candidate A\n\nNo match here.\n", "test: seed A")
    _seed_page(template_vault, page_b, f"# Candidate B\n\n{quote}.\n", "test: seed B (matches)")
    _seed_page(template_vault, page_c, "# Candidate C\n\nNo match here either.\n", "test: seed C")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "narrowed down to exactly one page",
            quote=quote,
            pages=[page_a, page_b, page_c],
        )
    )

    assert _warning_codes(result) == [], (
        "exactly one match among several candidates is not a degradation"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    assert document.anchors[0].page == page_b
    assert document.anchors[0].fidelity == "span"


def test_a_quote_found_in_more_than_one_claimed_page_pins_at_topic_fidelity(template_vault: Path):
    quote = "specification gaming shows up whenever the metric is proxy for the goal"
    page_a = f"{TOPIC}/duplicate-a.md"
    page_b = f"{TOPIC}/duplicate-b.md"
    _seed_page(template_vault, page_a, f"# Duplicate A\n\n{quote}.\n", "test: seed duplicate A")
    _seed_page(template_vault, page_b, f"# Duplicate B\n\n{quote}.\n", "test: seed duplicate B")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "the passage turned out to live in two places",
            quote=quote,
            pages=[page_a, page_b],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    assert any("2" in message for message in _warning_messages(result)), (
        "the warning must name how many pages matched"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == "", (
        "a guessed pin among ambiguous matches is the silent-wrong-anchor failure the design "
        "exists to avoid -- hold at topic level instead, never pick page_a by construction order"
    )
    assert anchor.fidelity == "topic"
    assert anchor.quote == quote, (
        "the passage the user was reacting to must survive even though it could not be pinned "
        "to a single page"
    )


def test_a_quote_found_in_none_of_several_existing_pages_pins_to_the_first_existing_page(
    template_vault: Path,
):
    quote = "a passage that appears on none of the claimed pages"
    page_a = f"{TOPIC}/no-match-a.md"
    page_b = f"{TOPIC}/no-match-b.md"
    _seed_page(template_vault, page_a, "# No match A\n\nUnrelated text.\n", "test: seed A")
    _seed_page(template_vault, page_b, "# No match B\n\nAlso unrelated.\n", "test: seed B")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "provenance claimed but unverifiable on either page",
            quote=quote,
            pages=[page_a, page_b],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1, "both claimed pages exist, so a page-level pin is honest"
    anchor = document.anchors[0]
    assert anchor.page == page_a, "pin to the first existing page in the caller's best-first order"
    assert anchor.fidelity == "page"
    assert anchor.quote == quote


def test_when_none_of_several_claimed_pages_exist_the_capture_pins_at_topic_fidelity(
    template_vault: Path,
):
    missing_a = f"{TOPIC}/missing-a.md"
    missing_b = f"{TOPIC}/missing-b.md"

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "every claimed page turned out to be wrong",
            quote="does not matter, no page exists to check it against",
            pages=[missing_a, missing_b],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    assert any(
        missing_a in message or missing_b in message for message in _warning_messages(result)
    ), "the warning must name at least one of the missing pages"
    document = _read_captured_note(template_vault, result["path"])
    assert len(document.anchors) == 1
    anchor = document.anchors[0]
    assert anchor.page == ""
    assert anchor.fidelity == "topic"
    assert anchor.quote == "does not matter, no page exists to check it against", (
        "the claimed passage must survive verbatim even though no page could verify it"
    )


# ---------------------------------------------------------------------------
# Structured alternatives: `_plan_anchor`'s multi-page-match branch already
# knows exactly which claimed pages matched -- today that knowledge is spent
# on building the prose degradation warning and then discarded. These pin
# that it also survives on the envelope as data, and *only* for the genuine
# ambiguity: several claimed pages matching the quote verbatim. Every other
# degraded path (one match, no match, no readable page, no quote) must keep
# reporting no alternatives -- a later change over-populating those paths is
# exactly what these guard against.
# ---------------------------------------------------------------------------


def _alternatives(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    """The capture envelope's structured runners-up, as `{page, heading}` mappings.

    Deliberately not bare page strings: the operation matched the quote against
    page text it already held, so the enclosing heading is derived from that same
    text. Enriching at the tool boundary instead would re-read the page against a
    HEAD that may have moved since the capture commit.
    """
    alternatives = result.get("alternatives", [])
    assert isinstance(alternatives, (list, tuple)), (
        f"alternatives must be a sequence, got {alternatives!r}"
    )
    for entry in alternatives:
        assert isinstance(entry, Mapping), f"each alternative must be a mapping, got {entry!r}"
    return [entry for entry in alternatives]


def test_a_quote_matched_in_several_claimed_pages_surfaces_them_as_alternatives(
    template_vault: Path,
):
    quote = "the reward signal and the intended goal quietly diverge"
    page_a = f"{TOPIC}/multi-match-a.md"
    page_b = f"{TOPIC}/multi-match-b.md"
    page_c = f"{TOPIC}/multi-match-c.md"
    _seed_page(template_vault, page_a, f"# Match A\n\n{quote}.\n", "test: seed multi-match A")
    _seed_page(
        template_vault, page_b, "# No Match B\n\nUnrelated text.\n", "test: seed multi-match B"
    )
    _seed_page(template_vault, page_c, f"# Match C\n\n{quote}.\n", "test: seed multi-match C")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "this reward signal argument keeps recurring",
            quote=quote,
            pages=[page_a, page_b, page_c],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result), (
        "the existing prose degradation warning must survive unchanged alongside the new data"
    )
    assert [entry["page"] for entry in _alternatives(result)] == [page_a, page_c], (
        "the non-matching middle page must be excluded and the claimed order preserved"
    )
    assert all("heading" in entry for entry in _alternatives(result)), (
        "the operation emits the full {page, heading} shape rather than bare paths: it matched "
        "the quote against page text it already holds, so the heading comes from that same text "
        "-- re-reading the page at the tool boundary would spend I/O against a HEAD that may "
        "have moved since the capture commit"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert document.anchors[0].fidelity == "topic", (
        "structured alternatives ride alongside the existing topic-fidelity degradation -- "
        "they do not replace it"
    )


def test_a_single_matched_page_among_several_claimed_returns_no_alternatives(
    template_vault: Path,
):
    quote = "an argument that only lives on one of the candidate pages"
    page_a = f"{TOPIC}/single-match-alt-a.md"
    page_b = f"{TOPIC}/single-match-alt-b.md"
    _seed_page(template_vault, page_a, f"# Single Match A\n\n{quote}.\n", "test: seed A")
    _seed_page(template_vault, page_b, "# Single Match B\n\nUnrelated text.\n", "test: seed B")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "not ambiguous, just one real match",
            quote=quote,
            pages=[page_a, page_b],
        )
    )

    assert _alternatives(result) == [], "exactly one match is the happy path, not an ambiguity"


def test_no_claimed_page_matching_the_quote_returns_no_alternatives(template_vault: Path):
    quote = "a passage that appears on none of the claimed pages"
    page_a = f"{TOPIC}/no-match-alt-a.md"
    page_b = f"{TOPIC}/no-match-alt-b.md"
    _seed_page(template_vault, page_a, "# No Match Alt A\n\nUnrelated.\n", "test: seed A")
    _seed_page(template_vault, page_b, "# No Match Alt B\n\nAlso unrelated.\n", "test: seed B")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "provenance claimed but unverifiable anywhere",
            quote=quote,
            pages=[page_a, page_b],
        )
    )

    assert _alternatives(result) == [], (
        "a page-level degradation (nothing matched) is not the multi-match ambiguity"
    )


def test_no_readable_claimed_page_returns_no_alternatives(template_vault: Path):
    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "every claimed page turns out to be missing",
            quote="does not matter, no page exists to check it against",
            pages=[f"{TOPIC}/missing-alt-a.md", f"{TOPIC}/missing-alt-b.md"],
        )
    )

    assert _alternatives(result) == [], "nothing was readable, so there is nothing to offer"


def test_no_quote_supplied_returns_no_alternatives_even_with_several_claimed_pages(
    template_vault: Path,
):
    page_a = f"{TOPIC}/no-quote-alt-a.md"
    page_b = f"{TOPIC}/no-quote-alt-b.md"
    _seed_page(template_vault, page_a, "# No Quote Alt A\n\nSome text.\n", "test: seed A")
    _seed_page(template_vault, page_b, "# No Quote Alt B\n\nSome other text.\n", "test: seed B")

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "a reflection with no specific passage",
            quote="",
            pages=[page_a, page_b],
        )
    )

    assert _alternatives(result) == [], "no quote means nothing could ever have matched"


# ---------------------------------------------------------------------------
# Unusable page paths: `pages` is the model's provenance guess arriving straight
# off the wire, so a directory name, an empty string, or a path escaping the
# vault is ordinary input -- not adversarial. None of it may raise, and none of
# it may be silently accepted as a page-level pin either: a page that cannot be
# read is not a candidate at all.
# ---------------------------------------------------------------------------

_ESCAPING_PAGE = "../../../etc/passwd"
_DIRECTORY_PAGE = "sources"


def test_a_page_path_escaping_the_vault_degrades_instead_of_raising(template_vault: Path):
    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "the model guessed a path outside the vault",
            quote="a passage no vault page can confirm",
            pages=[_ESCAPING_PAGE],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    document = _read_captured_note(template_vault, result["path"])
    anchor = document.anchors[0]
    assert anchor.page == ""
    assert anchor.fidelity == "topic"
    assert anchor.quote == "a passage no vault page can confirm", (
        "an unusable path must cost the pin, never the user's reflection or its quote"
    )


def test_a_directory_supplied_as_a_page_degrades_instead_of_raising(template_vault: Path):
    assert (template_vault / _DIRECTORY_PAGE).is_dir(), "test setup: must be a real directory"

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "the model named a folder, not a page",
            quote="a passage that lives in no single file",
            pages=[_DIRECTORY_PAGE],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    document = _read_captured_note(template_vault, result["path"])
    assert document.anchors[0].page == ""
    assert document.anchors[0].fidelity == "topic"


def test_a_directory_supplied_as_a_page_is_not_a_page_level_pin_when_no_quote_is_given(
    template_vault: Path,
):
    """The quiet face of the same defect: with no quote to fail the match, an
    unreadable path used to sail through as a valid page-level pin, leaving the
    note claiming to point at something that is not a file.
    """
    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "no quote, and the only claimed page is a directory",
            quote="",
            pages=[_DIRECTORY_PAGE],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    document = _read_captured_note(template_vault, result["path"])
    assert document.anchors[0].page == "", "a directory is not a page and must never be pinned"
    assert document.anchors[0].fidelity == "topic"


def test_an_empty_string_page_is_not_a_page_level_pin(template_vault: Path):
    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "the model emitted an empty provenance string",
            quote="",
            pages=[""],
        )
    )

    assert "ANCHOR_DEGRADED" in _warning_codes(result)
    document = _read_captured_note(template_vault, result["path"])
    assert document.anchors[0].page == ""
    assert document.anchors[0].fidelity == "topic"


def test_one_usable_page_among_unusable_ones_still_pins_correctly(template_vault: Path):
    """The mixed list is the case most likely to regress: dropping the unusable
    candidates must not disturb the matching rule applied to what survives.
    """
    page_relpath = f"{TOPIC}/mixed-list-target.md"
    quote = "the one passage that is actually findable"
    _seed_page(
        template_vault,
        page_relpath,
        f"# Mixed list target\n\n{quote}.\n",
        "test: seed mixed-list-target page",
    )

    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "two junk paths and one real one",
            quote=quote,
            pages=[_DIRECTORY_PAGE, _ESCAPING_PAGE, page_relpath],
        )
    )

    assert _warning_codes(result) == [], (
        "exactly one usable page matched, so the anchor is as good as claimed -- "
        "the discarded junk paths cost nothing"
    )
    document = _read_captured_note(template_vault, result["path"])
    assert document.anchors[0].page == page_relpath
    assert document.anchors[0].fidelity == "span"


# ---------------------------------------------------------------------------
# Exactly one commit, frozen grammar, idempotency
# ---------------------------------------------------------------------------


def test_capture_makes_exactly_one_commit_following_the_frozen_grammar(template_vault: Path):
    commits_before = git_commit_count(template_vault)

    _success(_capture(template_vault, TOPIC, "a note worth one commit"))

    assert git_commit_count(template_vault) == commits_before + 1
    assert git_status_porcelain(template_vault) == ""
    parsed = parse_knotica_commit(git_commit_subjects(template_vault)[0])
    assert parsed is not None, "the commit subject must follow the frozen grammar"
    assert parsed["op"] == "note_capture"
    assert parsed["topic"] == TOPIC


def test_capturing_the_same_note_twice_makes_exactly_one_commit_total(template_vault: Path):
    page_relpath = f"{TOPIC}/idempotency-target.md"
    quote = "a stable sentence for the idempotency check"
    _seed_page(
        template_vault,
        page_relpath,
        f"# Idempotency target\n\n{quote}.\n",
        "test: seed idempotency-target page",
    )
    commits_before = git_commit_count(template_vault)
    fields: dict[str, object] = {"quote": quote, "pages": [page_relpath]}

    _success(_capture(template_vault, TOPIC, "captured once", **fields))
    commits_after_first = git_commit_count(template_vault)
    assert commits_after_first == commits_before + 1

    second = _success(_capture(template_vault, TOPIC, "captured once", **fields))

    assert git_commit_count(template_vault) == commits_after_first, (
        "an identical (topic, note, quote, pages) re-capture must be a no-op, not a second commit"
    )
    assert git_status_porcelain(template_vault) == ""
    assert second.get("duplicate") is True or second.get("appended") is False, (
        "the envelope must mark the second call as the no-op it is"
    )


# ---------------------------------------------------------------------------
# Hard errors -- the only failure modes, none of them anchor-shaped
# ---------------------------------------------------------------------------


def test_an_unknown_topic_is_refused_with_topic_not_found(template_vault: Path):
    commits_before = git_commit_count(template_vault)

    result = _capture(template_vault, "no-such-topic", "a note with nowhere to land")

    assert _error_code(result) == "TOPIC_NOT_FOUND"
    assert git_commit_count(template_vault) == commits_before, "a hard error must make no commit"


@pytest.mark.parametrize("blank_note", ["", "   "])
def test_empty_note_text_is_refused_with_invalid_argument(template_vault: Path, blank_note: str):
    commits_before = git_commit_count(template_vault)

    result = _capture(template_vault, TOPIC, blank_note, quote="anything", pages=())

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert git_commit_count(template_vault) == commits_before, (
        "an empty note must fail before anchoring is ever attempted"
    )


@pytest.mark.parametrize("intent", ["reflection", "dispute", "gap", "question"])
def test_every_locked_intent_value_is_accepted(template_vault: Path, intent: str):
    result = _success(_capture(template_vault, TOPIC, "an intent-bearing note", intent=intent))

    document = _read_captured_note(template_vault, result["path"])
    assert document.intent == intent


def test_an_unrecognized_intent_value_is_refused_with_invalid_argument(template_vault: Path):
    commits_before = git_commit_count(template_vault)

    result = _capture(template_vault, TOPIC, "a note with a typo'd intent", intent="curous")

    assert _error_code(result) == "INVALID_ARGUMENT"
    assert git_commit_count(template_vault) == commits_before, (
        "an invalid intent is a tool-boundary validation failure, not an anchoring problem, "
        "and must make no commit -- anchor.py itself deliberately does not validate intent"
    )


def test_a_wikilink_in_the_note_body_never_reaches_the_operation_log(template_vault: Path):
    """The mechanism behind the isolation guarantee, pinned directly.

    The log entry's title is derived from the note body, and ``log.md`` sits at
    the vault root, so its family is *scored* and its full text is scanned for
    ``[[...]]``. A wikilink surviving into it becomes a genuine inbound link
    that de-orphans the page the note merely mentioned -- moving the eval
    scalar. The note-family link filter cannot catch it, because by then the
    link's source really is ``log.md``.

    Scoped to the *note body's own* link targets: the log entry separately
    renders the written note path as a wikilink, which every operation does and
    which points at a note file rather than a knowledge page.
    """
    _success(
        _capture(
            template_vault,
            TOPIC,
            f"points at [[{_LINKED_PAGE}]] and ![[{_EMBEDDED_PAGE}]] on purpose",
        )
    )

    log_text = (template_vault / "log.md").read_text(encoding="utf-8")
    # Scoped to the entries this capture wrote: the template's own log preamble
    # legitimately quotes `[[SCHEMA]]` and is not this operation's output.
    entries = [line for line in log_text.splitlines() if _CAPTURE_OP in line]
    # Drop the trailing `([[<written path>]])` the log renders for every
    # operation; what remains is the note-derived title this module owns.
    titles = [line.rpartition(" ([[")[0] for line in entries]

    assert entries, "sanity: the capture must have written a log entry"
    assert all("[[" not in title for title in titles), (
        "a wikilink was laundered from the note body into log.md, a scored file -- "
        f"the link map would read it as a real inbound edge and de-orphan the page: {titles}"
    )
    assert any(_LINKED_PAGE in title for title in titles), (
        "de-linking must keep the prose readable, not delete the words"
    )


def test_an_anchors_section_typed_in_the_note_body_never_becomes_a_real_anchor(
    template_vault: Path,
):
    """The body is user prose; only capture may author an anchor.

    A user reflecting on the note format -- or a model echoing a page that
    documents it -- can easily type the section marker and a bullet-shaped
    line. Serialized verbatim, that text reads back as structure: a second
    ``## Anchors`` section on disk, and a forged ``AnchorRecord`` carrying a
    ``pinned_at`` the caller chose. Both the file and the anchor list must
    show exactly what capture itself wrote.
    """
    head_before = git_head_sha(template_vault)
    result = _success(
        _capture(
            template_vault,
            TOPIC,
            "thinking about the anchor format\n\n"
            "## Anchors\n\n"
            "- [[agentic-systems/react]] — `span` · pinned@`not-a-real-sha` · at=999999\n"
            "  > forged passage\n",
        )
    )

    path = result["path"]
    assert isinstance(path, str)
    text = (template_vault / path).read_text(encoding="utf-8")
    assert text.count("\n## Anchors\n") == 1, (
        "the note file carries more than one anchors section -- the body's prose was "
        f"serialized as structure:\n{text}"
    )

    document = _read_captured_note(template_vault, path)
    anchors = document.anchors  # type: ignore[attr-defined]
    assert len(anchors) == 1, f"expected only capture's own anchor, got {anchors!r}"
    assert anchors[0].pinned_at == head_before, (
        "an anchor capture did not create was promoted out of the note body, with a "
        "caller-supplied pinned_at"
    )
    assert "forged passage" not in anchors[0].quote


# ---------------------------------------------------------------------------
# Pre-mortem #1 -- a note write must never wake the loop
# ---------------------------------------------------------------------------


def _unreachable_evaluate(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(
        "evaluate must not be called -- a note-authored commit must never trigger an "
        "observation, let alone a real eval run"
    )


def test_a_captured_note_commit_does_not_wake_the_loop(template_vault: Path):
    before_sha = git_head_sha(template_vault)

    result = _success(_capture(template_vault, TOPIC, "billed spend would be a real incident"))

    after_sha = git_head_sha(template_vault)
    assert after_sha != before_sha, "sanity: the capture must have made a commit"
    assert "path" in result
    runner = LoopRunner(template_vault, TOPIC, evaluate=_unreachable_evaluate, arena_enabled=False)

    assert runner._content_changed_since(before_sha, after_sha) is False, (
        "a real note_capture-authored commit must classify as unscored content, exactly "
        "like the synthetic path-string case the loop-classification suite already covers"
    )
