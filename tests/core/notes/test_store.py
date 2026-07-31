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
- **Reading one note costs one note.** ``read_note`` derives its path
  directly from ``note_id`` (the frozen Phase 1 contract: filename stem *is*
  frontmatter ``id``, files are never renamed) rather than scanning and
  resolving every note in the topic just to return one of them. A ``note_id``
  arriving unvalidated from the MCP boundary must never turn that direct path
  construction into a read outside ``notes/<topic>/``.
"""

from pathlib import Path

import pytest
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.notes_config import DEFAULT_COMPLETE_ORPHAN_THRESHOLD, DEFAULT_GUESS_THRESHOLD
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore, VaultStore
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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert listing.notes == ()
    assert listing.skipped_malformed == 0


def test_listing_an_existing_but_empty_notes_directory_returns_an_empty_listing(
    template_vault: Path,
):
    from knotica.core.notes.store import list_notes

    (template_vault / "notes" / TOPIC).mkdir(parents=True)
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    resolved = read_note(
        store,
        vcs,
        TOPIC,
        "20260101-090000-findable-note",
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert resolved is not None
    assert resolved.document.id == "20260101-090000-findable-note"


def test_read_note_returns_none_for_an_id_that_does_not_exist(template_vault: Path):
    from knotica.core.notes.store import read_note

    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    resolved = read_note(
        store,
        vcs,
        TOPIC,
        "20260101-090000-never-captured",
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
        anchored_page=f"{TOPIC}/page-a.md",
    )

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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

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

    list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )


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

    list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )
    read_note(
        store,
        vcs,
        TOPIC,
        "20260101-090000-a-note",
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert git_commit_count(template_vault) == commits_before
    assert git_status_porcelain(template_vault) == ""


# ---------------------------------------------------------------------------
# read_note is a single-file lookup, not list_notes-in-disguise
# ---------------------------------------------------------------------------
#
# ``read_note`` used to delegate entirely to ``list_notes`` -- reading one
# note paid for every note in the topic, each anchor resolution included (a
# ``git show`` via ``vcs.read_file_at``). The frozen Phase 1 contract makes
# the path derivable without a scan: note files live at
# ``notes/<topic>/<YYYYMMDD-HHMMSS>-<slug>.md``, frontmatter ``id`` *is* the
# filename stem, and files are never renamed (``capture_note`` guarantees
# it). These tests pin the fixed cost, the path-traversal guard the derived
# path needs (today's scan is safe only by accident -- it never uses
# ``note_id`` to build a path at all), and the "path is the address" choice:
# the file found at the derived path is trusted as-is, its frontmatter
# ``id`` is never cross-checked against the ``note_id`` that located it.


class _CountingStore:
    """Wraps a real ``VaultStore`` and records every ``read_text`` call.

    A spy, not a fake: every method delegates to ``delegate`` unchanged --
    including exceptions -- so behaviour is identical to the real store.
    Only ``read_text`` calls are additionally recorded, by path, in call
    order.
    """

    def __init__(self, delegate: VaultStore) -> None:
        self._delegate = delegate
        self.read_text_paths: list[str] = []

    @property
    def read_text_calls(self) -> int:
        return len(self.read_text_paths)

    def read_text(self, path: str) -> str:
        self.read_text_paths.append(str(path))
        return self._delegate.read_text(path)

    def write_text_atomic(self, path: str, content: str) -> None:
        self._delegate.write_text_atomic(path, content)

    def exists(self, path: str) -> bool:
        return self._delegate.exists(path)

    def list_dir(self, path: str = "") -> list[str]:
        return self._delegate.list_dir(path)

    def delete(self, path: str) -> None:
        self._delegate.delete(path)


class _CountingVcs(VaultVcs):
    """A ``VaultVcs`` that counts ``read_file_at`` calls -- the ``git show`` cost."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.read_file_at_calls = 0

    def read_file_at(self, ref: str, path: str) -> str | None:
        self.read_file_at_calls += 1
        return super().read_file_at(ref, path)


def test_reading_one_note_does_not_pay_for_every_other_note_in_the_topic(
    template_vault: Path,
):
    """``read_note``'s cost must be bounded by the ONE note it returns -- its
    own file, its own anchor -- never by how many sibling notes share the
    topic. This is the test that would have caught ``read_note`` delegating
    to ``list_notes`` (which enumerates and resolves every note, each anchor
    resolution costing a ``git show``, just to return one of them), and is
    the one that must not be weakened later.
    """
    from knotica.core.notes.store import read_note

    quote = "the target note's own anchored sentence sits here"
    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        f"# Notes target\n\n{quote}.\n",
        "test: seed notes-target page",
    )
    target_id = "20260101-090000-target-note"
    _write_note(
        template_vault,
        target_id,
        _note(target_id, anchors=(_anchor(pinned_at=page_sha, quote=quote),)),
    )
    for index in range(9):
        sibling_id = f"20260101-0915{index:02d}-sibling-note"
        _write_note(
            template_vault,
            sibling_id,
            _note(
                sibling_id,
                anchors=(_anchor(pinned_at=page_sha, quote=f"a quote sibling {index} never had"),),
            ),
        )
    _commit_all(template_vault, "test: capture the target note plus nine siblings")
    store = _CountingStore(LocalFSStore(template_vault))
    vcs = _CountingVcs(template_vault)

    resolved = read_note(
        store,
        vcs,
        TOPIC,
        target_id,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert resolved is not None
    assert resolved.document.id == target_id
    # The target note owns exactly one anchor: one read for the note file
    # itself, one read for that anchor's live page text. The nine siblings --
    # and their own nine anchors -- must contribute nothing to this count.
    assert store.read_text_calls == 2, (
        f"read_note touched {store.read_text_calls} files for a one-anchor note "
        f"among ten -- it is still paying for the other nine: {store.read_text_paths}"
    )
    # Exactly one anchor to resolve means exactly one git show, regardless of
    # how many anchors the nine siblings carry between them.
    assert vcs.read_file_at_calls == 1, (
        f"read_note resolved {vcs.read_file_at_calls} anchors' history for a "
        "note that owns exactly one anchor"
    )


@pytest.mark.parametrize(
    "hostile_note_id",
    [
        "../../../etc/passwd",
        "..",
        "a/b",
        "/etc/passwd",
        "",
    ],
    ids=[
        "many-dotdot-traversal",
        "bare-dotdot",
        "nested-path-segment",
        "absolute-path",
        "empty-id",
    ],
)
def test_a_hostile_note_id_is_rejected_without_reading_any_file(
    template_vault: Path, hostile_note_id: str
):
    """``note_id`` arrives from the MCP boundary unvalidated. Deriving the
    path directly (``notes/<topic>/<note_id>.md``) removes the accidental
    immunity the old ``list_notes``-based scan had -- it never used
    ``note_id`` to build a path, so a hostile value could only ever fail to
    match. The fixed lookup must reject a shape that cannot be a real note id
    -- containing a path separator, escaping upward, absolute, or empty --
    before any file is touched: the outcome is the existing not-found
    ``None``, never an exception and never a read.

    A real note is seeded first so a scan-shaped implementation (the one
    this test guards against) has something to read on its way to finding no
    match -- an empty topic would let that regression pass by accident.
    """
    from knotica.core.notes.store import read_note

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nAn unremarkable sentence.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        "20260101-090000-real-note",
        _note(
            "20260101-090000-real-note",
            anchors=(_anchor(pinned_at=page_sha, quote="An unremarkable sentence."),),
        ),
    )
    _commit_all(template_vault, "test: capture one real note")
    store = _CountingStore(LocalFSStore(template_vault))
    vcs = _CountingVcs(template_vault)

    resolved = read_note(
        store,
        vcs,
        TOPIC,
        hostile_note_id,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert resolved is None
    assert store.read_text_calls == 0, (
        f"a hostile note_id must be rejected before any file is read, not "
        f"discovered by attempting one: {store.read_text_paths}"
    )
    assert vcs.read_file_at_calls == 0


def test_read_note_returns_the_file_at_the_derived_path_even_when_its_frontmatter_id_disagrees(
    template_vault: Path,
):
    """The path IS the identity: ``read_note(topic, stem)`` reads
    ``notes/<topic>/<stem>.md`` and returns whatever parses there -- it does
    not verify ``document.id == note_id``. The frozen contract guarantees the
    two agree at capture time and that files are never renamed, so the path
    alone is a sufficient address. A strict-verify design would make a
    hand-renamed note unreachable by *both* its old id (the file is gone) and
    its new stem (the frontmatter still disagrees) -- strictly worse than
    trusting the path.
    """
    from knotica.core.notes.store import read_note

    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nAn unremarkable sentence.\n",
        "test: seed notes-target page",
    )
    stem = "20260101-090000-the-actual-filename"
    mismatched_document = _note(
        "20260101-080000-a-stale-frontmatter-id",  # deliberately != stem
        anchors=(_anchor(pinned_at=page_sha, quote="An unremarkable sentence."),),
    )
    _write_note(template_vault, stem, mismatched_document)
    _commit_all(
        template_vault, "test: capture a note whose frontmatter id disagrees with its filename"
    )
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    resolved = read_note(
        store,
        vcs,
        TOPIC,
        stem,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert resolved is not None
    assert resolved.path == f"notes/{TOPIC}/{stem}.md"
    assert resolved.document.id == "20260101-080000-a-stale-frontmatter-id"


def test_read_note_returns_none_when_the_file_at_the_derived_path_is_malformed(
    template_vault: Path,
):
    """A malformed note file is data, not an exception, along the single-file
    lookup too: ``read_note`` must not raise when ``parse_note`` reports an
    error for the file living at ``notes/<topic>/<note_id>.md`` -- ``None``
    still covers both missing and malformed.
    """
    from knotica.core.notes.store import read_note

    note_id = "20260101-091500-malformed-note"
    _write_raw_note(
        template_vault,
        TOPIC,
        note_id,
        f"---\ntype: note\nid: {note_id}\n---\n\nNo topic, no created.\n",
    )
    _commit_all(template_vault, "test: capture a malformed note at a known path")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    resolved = read_note(
        store,
        vcs,
        TOPIC,
        note_id,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert resolved is None


def test_read_note_matches_the_corresponding_entry_from_list_notes(template_vault: Path):
    """The single-file lookup must produce a result identical to the matching
    entry a full-topic enumeration would have returned: same document, same
    path, same resolved anchors. The optimization changes how many files get
    touched to produce the answer, never what the answer is.
    """
    from knotica.core.notes.store import list_notes, read_note

    quote = "the metric quietly became the target"
    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        f"# Notes target\n\n{quote} once optimization pressure was applied.\n",
        "test: seed notes-target page",
    )
    note_id = "20260101-090000-parity-check-note"
    _write_note(
        template_vault,
        note_id,
        _note(note_id, anchors=(_anchor(pinned_at=page_sha, quote=quote),)),
    )
    _commit_all(template_vault, "test: capture the parity-check note")
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)

    listing = list_notes(
        store,
        vcs,
        TOPIC,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )
    expected = next(r for r in listing.notes if r.document.id == note_id)

    actual = read_note(
        store,
        vcs,
        TOPIC,
        note_id,
        guess_threshold=DEFAULT_GUESS_THRESHOLD,
        complete_orphan_threshold=DEFAULT_COMPLETE_ORPHAN_THRESHOLD,
    )

    assert actual is not None
    assert actual.document == expected.document
    assert actual.path == expected.path
    assert actual.resolved_anchors == expected.resolved_anchors
