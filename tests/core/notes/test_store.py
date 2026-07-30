"""Behavioral contract of note read/list -- read-only, resolves projections.

``core.notes.store`` is the read side of the notes overlay: it enumerates
``notes/<topic>/`` (a reserved, unscored folder family), parses each file with
:func:`~knotica.core.notes.anchor.parse_note`, and resolves every anchor
against the vault's git history via
:func:`~knotica.core.notes.resolve.resolve_anchor`. Three properties are
load-bearing and are what these tests are built to catch a regression in:

- **Reading never writes.** No commit, no lock, no git-mutating call --
  listing a topic full of notes must be exactly as cheap and side-effect-free
  as reading a single page.
- **A malformed note file is data, not an exception.** It disappears from the
  listing and is counted; a note whose *anchor bullet* is malformed is a
  different, milder case -- the note stays listed with its readable anchors
  intact.
- **Drift is discovered by resolution, not by re-scanning.** A note captured
  against one commit keeps resolving correctly after the page it anchors
  moves on, purely by re-running the resolution ladder against HEAD -- no
  stored placement is ever updated.
"""

from pathlib import Path

import pytest
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_head_sha, git_status_porcelain, run_git

TOPIC = "agentic-systems"


def _anchor(**overrides: object) -> AnchorRecord:
    defaults: dict[str, object] = {
        "page": f"{TOPIC}/notes-target.md",
        "heading": "",
        "fidelity": "span",
        "pinned_at": "0000000",
        "quote": "a quote that will not matter for this test",
        "start": None,
    }
    defaults.update(overrides)
    return AnchorRecord(**defaults)


def _note(note_id: str, **overrides: object) -> NoteDocument:
    defaults: dict[str, object] = {
        "id": note_id,
        "topic": TOPIC,
        "intent": "reflection",
        "created": "2026-01-01T09:00:00Z",
        "updated": "2026-01-01T09:00:00Z",
        "status": "active",
        "tags": (),
        "body": "A loose thought.",
        "anchors": (_anchor(),),
        "skipped_anchor_count": 0,
    }
    defaults.update(overrides)
    return NoteDocument(**defaults)


def _write_note(vault: Path, note_id: str, document: NoteDocument) -> Path:
    """Write ``document`` under ``notes/<topic>/<id>.md`` -- no commit."""
    path = vault / "notes" / document.topic / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    return path


def _write_raw_note(vault: Path, topic: str, note_id: str, text: str) -> Path:
    """Write raw (possibly malformed) note text -- bypasses ``serialize_note``."""
    path = vault / "notes" / topic / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _commit_all(vault: Path, message: str) -> str:
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)
    return git_head_sha(vault)


def _write_and_commit_page(vault: Path, relpath: str, content: str, message: str) -> str:
    (vault / relpath).parent.mkdir(parents=True, exist_ok=True)
    (vault / relpath).write_text(content, encoding="utf-8")
    return _commit_all(vault, message)


# ---------------------------------------------------------------------------
# Empty listings
# ---------------------------------------------------------------------------


def test_listing_a_topic_with_no_notes_directory_at_all_returns_an_empty_listing(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    assert listing.notes == ()
    assert listing.skipped_malformed == 0


def test_listing_an_existing_but_empty_notes_directory_returns_an_empty_listing(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    (template_vault / "notes" / TOPIC).mkdir(parents=True)
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    assert listing.notes == ()
    assert listing.skipped_malformed == 0


# ---------------------------------------------------------------------------
# Well-formed notes, resolved projections
# ---------------------------------------------------------------------------


def test_listing_well_formed_notes_returns_each_with_its_resolved_projection(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    quote = "the metric quietly became the target"
    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        f"# Notes target\n\n{quote} once optimization pressure was applied.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        "20260101-090000-first-note",
        _note(
            "20260101-090000-first-note",
            anchors=(_anchor(pinned_at=page_sha, quote=quote),),
        ),
    )
    _write_note(
        template_vault,
        "20260101-091500-second-note",
        _note(
            "20260101-091500-second-note",
            anchors=(_anchor(pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture two notes")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    assert listing.skipped_malformed == 0
    ids = {resolved.document.id for resolved in listing.notes}
    assert ids == {"20260101-090000-first-note", "20260101-091500-second-note"}
    for resolved in listing.notes:
        assert len(resolved.resolved_anchors) == 1
        anchor, projection = resolved.resolved_anchors[0]
        assert anchor.quote == quote
        assert projection.status == "exact"
        assert projection.fidelity == "span"


def test_read_note_returns_the_matching_note_by_id(template_vault: Path):
    from knotica.core.notes.store import read_note

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nAn unremarkable sentence.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        "20260101-090000-findable-note",
        _note(
            "20260101-090000-findable-note",
            anchors=(_anchor(pinned_at=page_sha, quote="An unremarkable sentence."),),
        ),
    )
    _commit_all(template_vault, "test: capture findable note")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    resolved = read_note(store, vcs, TOPIC, "20260101-090000-findable-note")

    assert resolved is not None
    assert resolved.document.id == "20260101-090000-findable-note"


def test_read_note_returns_none_for_an_id_that_does_not_exist(template_vault: Path):
    from knotica.core.notes.store import read_note

    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    resolved = read_note(store, vcs, TOPIC, "20260101-090000-never-captured")

    assert resolved is None


# ---------------------------------------------------------------------------
# Malformed note file vs. malformed anchor bullet -- distinct outcomes
# ---------------------------------------------------------------------------


def test_a_note_file_missing_required_frontmatter_is_excluded_and_counted(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    _write_note(template_vault, "20260101-090000-good-note", _note("20260101-090000-good-note"))
    _write_raw_note(
        template_vault,
        TOPIC,
        "20260101-091500-broken-frontmatter",
        "---\ntype: note\nid: 20260101-091500-broken-frontmatter\n---\n\nNo topic, no created.\n",
    )
    _commit_all(template_vault, "test: one good note, one malformed note")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    assert listing.skipped_malformed == 1
    ids = {resolved.document.id for resolved in listing.notes}
    assert ids == {"20260101-090000-good-note"}


def test_a_note_with_one_malformed_anchor_bullet_still_lists_its_readable_anchors(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nThe valid anchor points here.\n",
        "test: seed notes-target page",
    )
    text = (
        "---\n"
        "type: note\n"
        "id: 20260101-093000-partially-broken-anchors\n"
        f"topic: {TOPIC}\n"
        "created: 2026-01-01T09:30:00Z\n"
        "---\n"
        "\n"
        "## Anchors\n"
        "\n"
        f"- [[{TOPIC}/notes-target]] — `span` · pinned@`{page_sha}`\n"
        "  > The valid anchor points here.\n"
        "\n"
        # No fidelity/pinned@ signature pair: the one shape the relaxed anchor
        # grammar still refuses, so this bullet is genuinely unreadable.
        f"- [[{TOPIC}/notes-target]] but nothing that pins it to a commit\n"
    )
    _write_raw_note(template_vault, TOPIC, "20260101-093000-partially-broken-anchors", text)
    _commit_all(template_vault, "test: capture note with one broken anchor bullet")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    assert listing.skipped_malformed == 0
    resolved = next(
        r for r in listing.notes if r.document.id == "20260101-093000-partially-broken-anchors"
    )
    assert resolved.document.skipped_anchor_count == 1
    assert len(resolved.resolved_anchors) == 1
    anchor, projection = resolved.resolved_anchors[0]
    assert anchor.quote == "The valid anchor points here."
    assert projection.status == "exact"


# ---------------------------------------------------------------------------
# Filtering by anchored page
# ---------------------------------------------------------------------------


def test_filtering_by_anchored_page_returns_only_notes_that_anchor_it(template_vault: Path):
    from knotica.core.notes.store import list_notes

    head_sha = git_head_sha(template_vault)
    for note_id, page in [
        ("20260101-090000-anchors-page-a", f"{TOPIC}/page-a.md"),
        ("20260101-091500-anchors-page-b", f"{TOPIC}/page-b.md"),
        ("20260101-093000-also-anchors-page-a", f"{TOPIC}/page-a.md"),
    ]:
        _write_note(
            template_vault,
            note_id,
            _note(note_id, anchors=(_anchor(pinned_at=head_sha, page=page),)),
        )
    _commit_all(template_vault, "test: capture three notes across two pages")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC, anchored_page=f"{TOPIC}/page-a.md")

    ids = {resolved.document.id for resolved in listing.notes}
    assert ids == {"20260101-090000-anchors-page-a", "20260101-093000-also-anchors-page-a"}


# ---------------------------------------------------------------------------
# Drift: end-to-end through anchor + resolve + store against real git history
# ---------------------------------------------------------------------------


def test_a_note_captured_then_the_page_edited_around_it_reports_shifted_with_no_extra_commits(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    quote = "optimization pressure finds the cheapest path to a metric"
    page_relpath = f"{TOPIC}/scratch-page.md"
    original_page = f"# Scratch Page\n\nThe kernel of the idea was that {quote}.\n"
    page_sha = _write_and_commit_page(
        template_vault, page_relpath, original_page, "test: seed scratch page"
    )
    _write_note(
        template_vault,
        "20260101-090000-drift-test-note",
        _note(
            "20260101-090000-drift-test-note",
            anchors=(
                _anchor(page=page_relpath, heading="Scratch Page", pinned_at=page_sha, quote=quote),
            ),
        ),
    )
    _commit_all(template_vault, "test: capture drift-test-note")
    rewritten_page = (
        "# Scratch Page\n\n"
        "A new opening paragraph, added later, has nothing to do with the original text.\n\n"
        f"The kernel of the idea was that {quote}.\n"
    )
    (template_vault / page_relpath).write_text(rewritten_page, encoding="utf-8")
    _commit_all(template_vault, "test: prepend a paragraph to the scratch page")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    commits_before = git_commit_count(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    commits_after = git_commit_count(template_vault)
    assert commits_after == commits_before, "reading notes must never create a commit"
    resolved = next(r for r in listing.notes if r.document.id == "20260101-090000-drift-test-note")
    anchor, projection = resolved.resolved_anchors[0]
    assert projection.status == "shifted"
    assert projection.fidelity == "span"
    expected_offset = rewritten_page.index(quote)
    assert projection.span == (expected_offset, expected_offset + len(quote))


# ---------------------------------------------------------------------------
# anchor-invalid: distinct from drift, and from a missing/renamed page
# ---------------------------------------------------------------------------


def test_an_anchor_the_quote_never_matched_even_historically_resolves_anchor_invalid_not_orphaned(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nNone of this text matches the anchor's quote.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        "20260101-090000-forged-anchor",
        _note(
            "20260101-090000-forged-anchor",
            anchors=(
                _anchor(
                    page=f"{TOPIC}/notes-target.md",
                    pinned_at=page_sha,
                    quote="a quote that was never in the historical blob",
                ),
            ),
        ),
    )
    _commit_all(template_vault, "test: capture a note with a forged anchor")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(store, vcs, TOPIC)

    resolved = next(r for r in listing.notes if r.document.id == "20260101-090000-forged-anchor")
    _, projection = resolved.resolved_anchors[0]
    assert projection.status == "anchor-invalid"
    assert projection.fidelity is None


# ---------------------------------------------------------------------------
# The zero-writes guarantee
# ---------------------------------------------------------------------------


def test_listing_notes_never_acquires_the_vault_lock(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
):
    from knotica.core.notes.store import list_notes

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nA quote to anchor against.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        "20260101-090000-a-note",
        _note(
            "20260101-090000-a-note",
            anchors=(_anchor(pinned_at=page_sha, quote="A quote to anchor against."),),
        ),
    )
    _commit_all(template_vault, "test: capture a note")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("list_notes must never acquire the vault lock")

    monkeypatch.setattr("knotica.core.lock.vault_lock", _boom)
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    list_notes(store, vcs, TOPIC)


def test_listing_and_reading_notes_leaves_the_working_tree_and_history_untouched(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes, read_note

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nA quote to anchor against.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        "20260101-090000-a-note",
        _note(
            "20260101-090000-a-note",
            anchors=(_anchor(pinned_at=page_sha, quote="A quote to anchor against."),),
        ),
    )
    _commit_all(template_vault, "test: capture a note")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    commits_before = git_commit_count(template_vault)

    list_notes(store, vcs, TOPIC)
    read_note(store, vcs, TOPIC, "20260101-090000-a-note")

    assert git_commit_count(template_vault) == commits_before
    assert git_status_porcelain(template_vault) == ""
