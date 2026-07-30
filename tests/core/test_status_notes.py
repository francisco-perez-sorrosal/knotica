"""Behavioral contract for the ``notes`` summary on ``gather_wiki_status``.

Phase 1 teaches ``wiki_status`` how many notes exist and how many of them are
*drifted* -- worth a human's attention because the wiki moved on without them.

**Drifted counts ``orphaned`` only.** This is a deliberate narrowing, not an
oversight, and the reasoning is the load-bearing part of this file:

- ``shifted`` is not drift. The anchor re-resolved itself at a new offset in
  the same page -- the resolution ladder healed it automatically and there is
  nothing left for a human to do. Counting a self-healed anchor as "drifted"
  would train people to stop trusting the badge.
- ``unanchored`` is not drift. It means a note was pinned at topic level with
  no page at all -- produced by every quote-less capture and every degraded
  capture. Nothing was ever pointed at, so nothing was lost; flagging it as
  drift would punish a perfectly clean capture the moment it was written.
- ``anchor-invalid`` is not drift and is out of scope for this count. It means
  the anchor record itself is corrupt or hand-forged -- a data-integrity
  problem, not "the knowledge base moved on." It deserves its own surfacing
  eventually, but folding it into this number would conflate two different
  failure classes behind one badge.

A future resolver rung (e.g. Phase 2's fuzzy matching) may widen the set of
statuses this counts as drift -- but the *principle* stays the same:
drifted means "the resolver could not place this anchor and nothing healed
it," not "this anchor's status differs from ``exact``."
"""

from pathlib import Path

from knotica.core.notes.anchor import AnchorRecord, NoteDocument, serialize_note
from knotica.core.status import gather_wiki_status
from knotica.store import LocalFSStore
from support.vault import run_git

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


def _write_note(vault: Path, topic: str, note_id: str, document: NoteDocument) -> Path:
    path = vault / "notes" / topic / f"{note_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    return path


def _commit_all(vault: Path, message: str) -> None:
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)


def _write_and_commit_page(vault: Path, relpath: str, content: str, message: str) -> str:
    (vault / relpath).parent.mkdir(parents=True, exist_ok=True)
    (vault / relpath).write_text(content, encoding="utf-8")
    _commit_all(vault, message)
    from support.vault import git_head_sha

    return git_head_sha(vault)


def _summary(template_vault: Path, *, topic: str = "") -> dict[str, object]:
    store = LocalFSStore(template_vault)
    payload = gather_wiki_status(store, template_vault, topic=topic)
    return payload


# ---------------------------------------------------------------------------
# The absent-key contract: zero notes still reports the shape, never omits it
# ---------------------------------------------------------------------------


def test_a_vault_with_zero_notes_anywhere_reports_the_notes_key_with_zero_counts(
    template_vault: Path,
):
    payload = _summary(template_vault)

    assert payload["totals"]["notes"] == {"total": 0, "drifted": 0}, (
        "callers must never have to guess whether the notes feature is present -- "
        "the key is always there, even when there is nothing to count"
    )


def test_the_scope_view_never_counts_notes(template_vault: Path):
    """``view="scope"`` is the deliberately cheap path: topic names only.

    Seeding a note whose anchor would count as drifted proves the assertion is
    non-vacuous -- if the scope view *did* accidentally run the counting pass,
    a `"notes"` key or a per-topic notes cost would leak into this payload.
    """
    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/notes-target.md",
        "# Notes target\n\nThis text used to hold the quote.\n",
        "test: seed notes-target page",
    )
    _write_note(
        template_vault,
        TOPIC,
        "20260101-090000-orphaned-note",
        _note(
            "20260101-090000-orphaned-note",
            anchors=(_anchor(pinned_at=page_sha, quote="a quote nowhere in the page"),),
        ),
    )
    _commit_all(template_vault, "test: capture a note that will orphan")
    store = LocalFSStore(template_vault)

    payload = gather_wiki_status(store, template_vault, view="scope")

    assert "notes" not in payload["totals"], "the scope view must stay free of any notes counting"


# ---------------------------------------------------------------------------
# The mix: pins the "drifted counts orphaned only" ruling, unmistakably
# ---------------------------------------------------------------------------


def test_a_mix_of_exact_shifted_unanchored_and_orphaned_counts_only_the_orphaned_one_as_drifted(
    template_vault: Path,
):
    quote = "the mechanism that makes this claim true"
    page_relpath = f"{TOPIC}/mix-page.md"
    page_sha = _write_and_commit_page(
        template_vault,
        page_relpath,
        f"# Mix Page\n\n{quote} lives right here.\n",
        "test: seed mix page",
    )

    # exact: a page of its own that nothing below ever rewrites, so this note
    # still resolves at the offset it was captured against. It lives on a
    # separate page deliberately -- the shifted case below works by rewriting
    # its page, which would convert this note to `shifted` and leave `exact`
    # untested despite the name of this test.
    stable_page_relpath = f"{TOPIC}/stable-page.md"
    stable_sha = _write_and_commit_page(
        template_vault,
        stable_page_relpath,
        f"# Stable Page\n\n{quote} lives right here too.\n",
        "test: seed stable page",
    )
    _write_note(
        template_vault,
        TOPIC,
        "20260101-085000-exact-note",
        _note(
            "20260101-085000-exact-note",
            anchors=(_anchor(page=stable_page_relpath, pinned_at=stable_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture the exact note")

    # shifted: captured against the mix page, which then grows a paragraph
    # above the quote.
    _write_note(
        template_vault,
        TOPIC,
        "20260101-090000-shifted-note",
        _note(
            "20260101-090000-shifted-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture the note that will shift")

    # the page grows a new paragraph above the quote -- the quote is
    # still there, just at a different offset. The resolver heals this itself.
    rewritten_page = (
        "# Mix Page\n\n"
        "A new opening paragraph pushes everything below it down.\n\n"
        f"{quote} lives right here.\n"
    )
    (template_vault / page_relpath).write_text(rewritten_page, encoding="utf-8")
    _commit_all(template_vault, "test: prepend a paragraph to the mix page")

    # unanchored: no page was ever claimed -- a degraded, quote-less capture.
    _write_note(
        template_vault,
        TOPIC,
        "20260101-091000-unanchored-note",
        _note("20260101-091000-unanchored-note", anchors=(_anchor(page="", quote=""),)),
    )
    _commit_all(template_vault, "test: capture the unanchored note")

    # orphaned: a page was claimed, but the quote is gone from the page.
    orphan_page_relpath = f"{TOPIC}/orphan-page.md"
    orphan_sha = _write_and_commit_page(
        template_vault,
        orphan_page_relpath,
        "# Orphan Page\n\nsome text that will disappear before HEAD.\n",
        "test: seed orphan page",
    )
    _write_note(
        template_vault,
        TOPIC,
        "20260101-092000-orphaned-note",
        _note(
            "20260101-092000-orphaned-note",
            anchors=(
                _anchor(
                    page=orphan_page_relpath,
                    pinned_at=orphan_sha,
                    quote="some text that will disappear before HEAD",
                ),
            ),
        ),
    )
    (template_vault / orphan_page_relpath).write_text(
        "# Orphan Page\n\nEntirely different content now.\n", encoding="utf-8"
    )
    _commit_all(template_vault, "test: rewrite the orphan page, losing the quote")

    payload = _summary(template_vault)

    assert payload["totals"]["notes"] == {"total": 4, "drifted": 1}, (
        "only the genuinely orphaned note counts as drifted -- the exact note is "
        "untouched, the shifted note self-healed at a new offset, and the "
        "unanchored note never pointed at anything to lose"
    )


def test_an_anchor_invalid_note_is_neither_counted_as_drifted_nor_silently_dropped(
    template_vault: Path,
):
    """A corrupt/hand-forged anchor is a data-integrity problem, not drift.

    It still counts toward ``total`` (the note genuinely exists), but must not
    inflate ``drifted`` -- conflating "the wiki moved on" with "this record was
    never valid" would point a human at the wrong remediation.
    """
    page_sha = _write_and_commit_page(
        template_vault,
        f"{TOPIC}/forged-target.md",
        "# Forged target\n\nNone of this text matches the anchor's quote.\n",
        "test: seed forged-target page",
    )
    _write_note(
        template_vault,
        TOPIC,
        "20260101-090000-forged-note",
        _note(
            "20260101-090000-forged-note",
            anchors=(
                _anchor(
                    page=f"{TOPIC}/forged-target.md",
                    pinned_at=page_sha,
                    quote="a quote that was never in the historical blob",
                ),
            ),
        ),
    )
    _commit_all(template_vault, "test: capture a note with a forged anchor")

    payload = _summary(template_vault)

    assert payload["totals"]["notes"] == {"total": 1, "drifted": 0}


# ---------------------------------------------------------------------------
# --topic scoping: a second topic's notes must not leak into a scoped count
# ---------------------------------------------------------------------------


def test_topic_scoping_counts_only_the_named_topics_notes(template_vault: Path):
    quote = "a claim anchored cleanly in its own topic"
    page_relpath = f"{TOPIC}/scoped-page.md"
    page_sha = _write_and_commit_page(
        template_vault,
        page_relpath,
        f"# Scoped Page\n\n{quote} sits right here.\n",
        "test: seed scoped page",
    )
    _write_note(
        template_vault,
        TOPIC,
        "20260101-090000-scoped-exact-note",
        _note(
            "20260101-090000-scoped-exact-note",
            anchors=(_anchor(page=page_relpath, pinned_at=page_sha, quote=quote),),
        ),
    )
    _commit_all(template_vault, "test: capture a note in the seed topic")

    other_topic = "other-topic"
    (template_vault / other_topic).mkdir()
    (template_vault / other_topic / "index.md").write_text("# Other Topic\n", encoding="utf-8")
    _commit_all(template_vault, "test: add a second topic")
    other_page_relpath = f"{other_topic}/other-orphan-target.md"
    other_sha = _write_and_commit_page(
        template_vault,
        other_page_relpath,
        "# Other Orphan Target\n\na quote that will vanish\n",
        "test: seed other-topic page",
    )
    _write_note(
        template_vault,
        other_topic,
        "20260101-090000-other-orphaned-note",
        _note(
            "20260101-090000-other-orphaned-note",
            topic=other_topic,
            anchors=(
                _anchor(
                    page=other_page_relpath, pinned_at=other_sha, quote="a quote that will vanish"
                ),
            ),
        ),
    )
    (template_vault / other_page_relpath).write_text(
        "# Other Orphan Target\n\nCompletely different text now.\n", encoding="utf-8"
    )
    _commit_all(template_vault, "test: orphan the other topic's note")

    scoped = _summary(template_vault, topic=TOPIC)

    assert scoped["topics"][0]["notes"] == {"total": 1, "drifted": 0}, (
        "the other topic's orphaned note must not leak into a --topic-scoped count"
    )
