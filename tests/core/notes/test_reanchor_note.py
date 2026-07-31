"""Behavioral contract of `reanchor`, `detach`, `archive` -- the note-correction ops.

Three human review actions on an already-captured note, each exactly one
`VaultTransaction`/one commit, following `capture_note`'s template. The
governing invariant every test below defends: **a correction is appended,
never a rewrite.** `reanchor` appends a new anchor recording what a human
just confirmed; `detach` appends a terminal record saying the note no longer
points anywhere; neither ever touches an anchor that already exists on disk.
`archive` is the one operation of the three that is not about anchors at
all -- it flips the note's frontmatter status and leaves the `## Anchors`
section completely alone.

`reanchor` and `detach` act on one anchor at a time, named by its 0-based
index into the note's append-only history -- index 0 is always the anchor of
record, and the index is stable because the history never reorders. Only a
*live* target is addressable: supersession and detachment are per distinct
page (a note may carry more than one independent anchor, each resolved on
its own), so an index that is out of range, already superseded by a later
record on the same page, or itself the terminal `detached` kind is rejected
with `INVALID_ARGUMENT` before any write -- there is no dedicated code for
"that anchor is not the live one", so every shape of that rejection funnels
through the same one. `reanchor` accepts `page`/`quote` and also accepts
neither: empty means "accept the currently-resolved projection", the drift
queue's one-click accept, not a separate code path. `archive` takes no index
at all and is idempotent, mirroring `capture_note`'s own duplicate-call
precedent: a second `archive` on an already-archived note changes nothing
and says so through the same `written`/`duplicate` vocabulary, not a new
flag.

The second invariant, and the highest-value one in this file: **an anchor's
quote is verbatim knowledge-base prose, and it must never reach a shared,
scored surface.** `VaultTransaction` writes its `title` argument into both
the commit subject and the vault-root operation log, whose folder family is
scored. All three operations here derive that title from the note's own id
alone -- never from an anchor's quote, never from the note body -- so a
distinctive passage pinned by a `reanchor`, or already sitting on a note a
`detach`/`archive` touches, must be provably absent from both.

Fixture notes are built through the already-shipped `capture_note` operation
rather than hand-authored, so every anchor these tests start from is exactly
what a real capture would produce, with two exceptions `capture_note` cannot
reach on its own -- it only ever writes one anchor per call: a note with
zero anchors, and a note already carrying two independent live anchors on
different pages. Both are placed directly via `serialize_note`.
"""

from collections.abc import Mapping
from pathlib import Path

from knotica.core.notes.anchor import (
    AnchorRecord,
    NoteDocument,
    anchor_of_record,
    effective_anchor,
    parse_note,
    serialize_note,
)
from knotica.core.operations.capture_note import capture_note
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
_REANCHOR_OP = "note_reanchor"
_DETACH_OP = "note_detach"
_ARCHIVE_OP = "note_archive"
_UNKNOWN_NOTE_ID = "20260101-000000-never-captured"


# ---------------------------------------------------------------------------
# Deferred-import call helpers (the RED trigger for this whole file)
# ---------------------------------------------------------------------------


def _reanchor(
    vault: Path, topic: str, note_id: str, anchor: int, *, page: str = "", quote: str = ""
) -> Mapping[str, object]:
    """Invoke `reanchor`; imported lazily so collection succeeds before the module exists."""
    from knotica.core.operations.reanchor_note import reanchor

    result = reanchor(
        LocalFSStore(vault), vault, VaultVcs(vault), topic, note_id, anchor, page=page, quote=quote
    )
    assert isinstance(result, Mapping), f"expected an envelope mapping, got {result!r}"
    return result


def _detach(vault: Path, topic: str, note_id: str, anchor: int) -> Mapping[str, object]:
    from knotica.core.operations.reanchor_note import detach

    result = detach(LocalFSStore(vault), vault, VaultVcs(vault), topic, note_id, anchor)
    assert isinstance(result, Mapping), f"expected an envelope mapping, got {result!r}"
    return result


def _archive(vault: Path, topic: str, note_id: str) -> Mapping[str, object]:
    from knotica.core.operations.reanchor_note import archive

    result = archive(LocalFSStore(vault), vault, VaultVcs(vault), topic, note_id)
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


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _seed_page(vault: Path, relpath: str, content: str, message: str) -> None:
    target = vault / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", message)


def _seed_captured_note(vault: Path, *, page_relpath: str, quote: str) -> str:
    """Seed one page and capture a note pinned to it at span fidelity; returns its note_id."""
    _seed_page(vault, page_relpath, f"# Seed page\n\n{quote}.\n", f"test: seed {page_relpath}")
    result = capture_note(
        LocalFSStore(vault),
        vault,
        VaultVcs(vault),
        TOPIC,
        "a reflection worth revisiting",
        quote=quote,
        pages=[page_relpath],
    )
    assert "error" not in result, f"fixture setup failed: {result!r}"
    note_id = result["note_id"]
    assert isinstance(note_id, str)
    return note_id


def _seed_note_with_anchors(
    vault: Path,
    note_id: str,
    anchors: tuple[AnchorRecord, ...],
    *,
    body: str = "a hand-authored note seeded directly for a test fixture",
) -> None:
    """Hand-place a note with a given anchor history -- for shapes `capture_note`
    cannot produce directly: a bare note with zero anchors, or one already
    carrying more than one independent live anchor.
    """
    document = NoteDocument(
        id=note_id,
        topic=TOPIC,
        intent="reflection",
        created="2026-07-30T08:00:00Z",
        updated="2026-07-30T08:00:00Z",
        status="active",
        tags=(),
        body=body,
        anchors=anchors,
    )
    path = vault / _note_path(note_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_note(document), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", f"test: seed note {note_id} with {len(anchors)} anchor(s)")


def _seed_bare_note(vault: Path, note_id: str) -> None:
    """Hand-place a note with zero anchors -- unreachable via `capture_note`, which always
    writes at least one anchor, even at topic fidelity.
    """
    _seed_note_with_anchors(vault, note_id, (), body="a hand-authored note with no anchors at all")


def _note_path(note_id: str) -> str:
    return f"notes/{TOPIC}/{note_id}.md"


def _read_note(vault: Path, note_id: str) -> NoteDocument:
    text = (vault / _note_path(note_id)).read_text(encoding="utf-8")
    document, error = parse_note(text)
    assert error is None, f"the note must parse cleanly, got error: {error!r}"
    assert document is not None
    return document


# ---------------------------------------------------------------------------
# reanchor -- append-only correctness
# ---------------------------------------------------------------------------


def test_reanchor_appends_a_new_reanchored_anchor_leaving_the_original_byte_identical(
    template_vault: Path,
):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-original.md",
        quote="the passage the note was first pinned against",
    )
    before = _read_note(template_vault, note_id)
    assert len(before.anchors) == 1, "sanity: capture always writes exactly one anchor"
    original_anchor = before.anchors[0]
    new_page = f"{TOPIC}/reanchor-corrected.md"
    new_quote = "the passage a human confirmed is the right one"
    _seed_page(
        template_vault, new_page, f"# Corrected\n\n{new_quote}.\n", "test: seed corrected page"
    )

    _success(_reanchor(template_vault, TOPIC, note_id, 0, page=new_page, quote=new_quote))

    after = _read_note(template_vault, note_id)
    assert len(after.anchors) == 2, "reanchor must append, never replace"
    assert after.anchors[0] == original_anchor, (
        "the original anchor of record must be byte-unchanged after a reanchor -- correcting "
        "a note must never rewrite what it corrects"
    )
    assert after.anchors[1].kind == "reanchored"
    assert after.anchors[1].page == new_page
    assert after.anchors[1].quote == new_quote


def test_reanchor_leaves_effective_anchor_pointing_at_the_new_anchor(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-effective-original.md",
        quote="the first passage this note pointed at",
    )
    new_page = f"{TOPIC}/reanchor-effective-new.md"
    _seed_page(template_vault, new_page, "# New\n\nthe corrected passage.\n", "test: seed new page")

    _success(
        _reanchor(template_vault, TOPIC, note_id, 0, page=new_page, quote="the corrected passage.")
    )

    after = _read_note(template_vault, note_id)
    assert effective_anchor(after) == after.anchors[1]
    assert effective_anchor(after) != anchor_of_record(after)


def test_reanchor_leaves_anchor_of_record_at_index_zero_unchanged(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-of-record-original.md",
        quote="the passage this note was captured against",
    )
    before_of_record = anchor_of_record(_read_note(template_vault, note_id))
    new_page = f"{TOPIC}/reanchor-of-record-new.md"
    _seed_page(template_vault, new_page, "# New\n\nyet another passage.\n", "test: seed new page")

    _success(
        _reanchor(template_vault, TOPIC, note_id, 0, page=new_page, quote="yet another passage.")
    )

    after = _read_note(template_vault, note_id)
    assert anchor_of_record(after) == before_of_record, (
        "the anchor of record is capture's idempotency fingerprint -- it must never move, "
        "on pain of a rewritten index 0 silently breaking every future re-capture match"
    )


def test_reanchor_pins_the_new_anchor_at_the_pre_reanchor_head_sha(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-pinned-at-original.md",
        quote="the first passage",
    )
    new_page = f"{TOPIC}/reanchor-pinned-at-new.md"
    _seed_page(template_vault, new_page, "# New\n\nthe corrected passage.\n", "test: seed new page")
    before_sha = git_head_sha(template_vault)

    _success(
        _reanchor(template_vault, TOPIC, note_id, 0, page=new_page, quote="the corrected passage.")
    )

    after_sha = git_head_sha(template_vault)
    after = _read_note(template_vault, note_id)
    assert after.anchors[-1].pinned_at == before_sha, (
        "the new anchor must record the vault state the human actually confirmed against, "
        "not the reanchor's own new commit"
    )
    assert after_sha != before_sha, "sanity: the reanchor must have made a new commit"


def test_reanchor_makes_exactly_one_commit_following_the_frozen_grammar(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-commit-original.md",
        quote="a passage worth correcting",
    )
    new_page = f"{TOPIC}/reanchor-commit-new.md"
    _seed_page(template_vault, new_page, "# New\n\nthe corrected passage.\n", "test: seed new page")
    commits_before = git_commit_count(template_vault)

    _success(
        _reanchor(template_vault, TOPIC, note_id, 0, page=new_page, quote="the corrected passage.")
    )

    assert git_commit_count(template_vault) == commits_before + 1
    assert git_status_porcelain(template_vault) == ""
    parsed = parse_knotica_commit(git_commit_subjects(template_vault)[0])
    assert parsed is not None, "the commit subject must follow the frozen grammar"
    assert parsed["op"] == _REANCHOR_OP
    assert parsed["topic"] == TOPIC


def test_reanchoring_an_already_reanchored_note_appends_a_third_correction(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault, page_relpath=f"{TOPIC}/triple-original.md", quote="the very first passage"
    )
    second_page = f"{TOPIC}/triple-second.md"
    _seed_page(
        template_vault, second_page, "# Second\n\nthe second passage.\n", "test: seed second"
    )
    _success(
        _reanchor(template_vault, TOPIC, note_id, 0, page=second_page, quote="the second passage.")
    )
    third_page = f"{TOPIC}/triple-third.md"
    _seed_page(template_vault, third_page, "# Third\n\nthe third passage.\n", "test: seed third")

    # Target index 1 -- the correction just appended is now the live entry for its page.
    _success(
        _reanchor(template_vault, TOPIC, note_id, 1, page=third_page, quote="the third passage.")
    )

    after = _read_note(template_vault, note_id)
    assert len(after.anchors) == 3, (
        "kind='reanchored' is not terminal -- a corrected anchor can be corrected again"
    )
    assert [anchor.kind for anchor in after.anchors] == ["pinned", "reanchored", "reanchored"]
    assert effective_anchor(after) == after.anchors[2]


def test_reanchor_with_no_page_or_quote_accepts_the_currently_resolved_projection(
    template_vault: Path,
):
    """`page`/`quote` are each optional: empty means "accept the projected
    match", not "leave the argument blank forever" -- so a reanchor with
    neither re-pins to wherever the target anchor currently resolves. This is
    the drift queue's one-click accept, not a separate code path from the
    explicit-arguments case above.
    """
    quote = "a passage that has not drifted at all"
    note_id = _seed_captured_note(
        template_vault, page_relpath=f"{TOPIC}/reanchor-accept-projection.md", quote=quote
    )
    before = _read_note(template_vault, note_id)
    before_sha = git_head_sha(template_vault)

    _success(_reanchor(template_vault, TOPIC, note_id, 0))

    after = _read_note(template_vault, note_id)
    assert len(after.anchors) == 2, "accepting the projection still appends, like any reanchor"
    accepted = after.anchors[1]
    assert accepted.kind == "reanchored"
    assert accepted.page == before.anchors[0].page, (
        "with no page/quote supplied, the resolved page is whatever the anchor currently "
        "projects onto -- unchanged here, since nothing has drifted"
    )
    assert accepted.quote == quote
    assert accepted.pinned_at == before_sha
    assert after.anchors[0] == before.anchors[0], (
        "accepting the projection still never rewrites what it corrects"
    )


# ---------------------------------------------------------------------------
# detach -- terminal correctness
# ---------------------------------------------------------------------------


def test_detach_appends_a_terminal_detached_record_and_effective_anchor_becomes_none(
    template_vault: Path,
):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/detach-target.md",
        quote="a passage worth detaching from",
    )

    _success(_detach(template_vault, TOPIC, note_id, 0))

    after = _read_note(template_vault, note_id)
    assert len(after.anchors) == 2
    assert after.anchors[-1].kind == "detached"
    assert effective_anchor(after) is None


def test_detach_leaves_every_prior_anchor_byte_identical(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/detach-chain-original.md",
        quote="the first passage in the chain",
    )
    second_page = f"{TOPIC}/detach-chain-second.md"
    _seed_page(
        template_vault,
        second_page,
        "# Second\n\nthe second passage in the chain.\n",
        "test: seed second",
    )
    _success(
        _reanchor(
            template_vault,
            TOPIC,
            note_id,
            0,
            page=second_page,
            quote="the second passage in the chain.",
        )
    )
    before = _read_note(template_vault, note_id)
    assert len(before.anchors) == 2, "sanity: one original + one reanchor"

    # Target index 1 -- the reanchored correction is now the live entry for its page.
    _success(_detach(template_vault, TOPIC, note_id, 1))

    after = _read_note(template_vault, note_id)
    assert after.anchors[0] == before.anchors[0], "detach must not touch the anchor of record"
    assert after.anchors[1] == before.anchors[1], (
        "detach must not touch the reanchored correction either"
    )
    assert after.anchors[2].kind == "detached"


def test_detach_makes_exactly_one_commit_following_the_frozen_grammar(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/detach-commit-target.md",
        quote="a passage that will be detached",
    )
    commits_before = git_commit_count(template_vault)

    _success(_detach(template_vault, TOPIC, note_id, 0))

    assert git_commit_count(template_vault) == commits_before + 1
    assert git_status_porcelain(template_vault) == ""
    parsed = parse_knotica_commit(git_commit_subjects(template_vault)[0])
    assert parsed is not None
    assert parsed["op"] == _DETACH_OP
    assert parsed["topic"] == TOPIC


# ---------------------------------------------------------------------------
# Multi-anchor independence -- correcting one page must never touch another
# ---------------------------------------------------------------------------


def test_reanchoring_one_pages_anchor_leaves_a_different_pages_anchor_untouched_and_live(
    template_vault: Path,
):
    """A note may carry more than one independent anchor, each resolved on
    its own page. Correcting one must never touch, or silently un-live, the
    other -- the exact shape that broke the shipped module's note-scoped
    liveness check, which `live_anchors` now answers per page instead.
    """
    note_id = "20260730-093000-two-independent-pages"
    page_a = AnchorRecord(
        page=f"{TOPIC}/multi-anchor-page-a.md",
        heading="",
        fidelity="span",
        pinned_at="9f1a3c0",
        quote="the passage on page A",
    )
    page_b = AnchorRecord(
        page=f"{TOPIC}/multi-anchor-page-b.md",
        heading="",
        fidelity="span",
        pinned_at="a3f9c21",
        quote="the passage on page B, never touched by this test's reanchor",
    )
    _seed_note_with_anchors(
        template_vault, note_id, (page_a, page_b), body="a reflection anchored to two pages"
    )
    new_page = f"{TOPIC}/multi-anchor-page-a-corrected.md"
    _seed_page(
        template_vault,
        new_page,
        "# Corrected\n\nthe corrected passage on page A.\n",
        "test: seed corrected page",
    )

    _success(
        _reanchor(
            template_vault,
            TOPIC,
            note_id,
            0,
            page=new_page,
            quote="the corrected passage on page A.",
        )
    )

    after = _read_note(template_vault, note_id)
    assert after.anchors[0] == page_a, "the targeted anchor itself must stay byte-unchanged"
    assert after.anchors[1] == page_b, (
        "page B's anchor must be byte-unchanged by a reanchor targeting page A"
    )

    from knotica.core.notes.anchor import live_anchors

    assert page_b in live_anchors(after), (
        "page B was never targeted -- it must still resolve as live"
    )


# ---------------------------------------------------------------------------
# archive -- frontmatter-only, idempotent
# ---------------------------------------------------------------------------


def test_archive_sets_status_to_archived_and_touches_no_anchor(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/archive-target.md",
        quote="a passage on a note about to be archived",
    )
    before = _read_note(template_vault, note_id)
    assert before.status == "active", "sanity: capture writes active notes"

    _success(_archive(template_vault, TOPIC, note_id))

    after = _read_note(template_vault, note_id)
    assert after.status == "archived"
    assert after.anchors == before.anchors, "archiving is a frontmatter-only change"


def test_archive_makes_exactly_one_commit_following_the_frozen_grammar(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/archive-commit-target.md",
        quote="a passage on a note that will be archived",
    )
    commits_before = git_commit_count(template_vault)

    _success(_archive(template_vault, TOPIC, note_id))

    assert git_commit_count(template_vault) == commits_before + 1
    assert git_status_porcelain(template_vault) == ""
    parsed = parse_knotica_commit(git_commit_subjects(template_vault)[0])
    assert parsed is not None
    assert parsed["op"] == _ARCHIVE_OP
    assert parsed["topic"] == TOPIC


def test_archiving_an_already_archived_note_is_idempotent_and_makes_no_second_commit(
    template_vault: Path,
):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/archive-twice-target.md",
        quote="a passage archived, then archived again",
    )
    _success(_archive(template_vault, TOPIC, note_id))
    commits_after_first = git_commit_count(template_vault)

    second = _success(_archive(template_vault, TOPIC, note_id))

    assert git_commit_count(template_vault) == commits_after_first, (
        "archiving an already-archived note must be a no-op, not a second commit -- matching "
        "capture_note's idempotency precedent"
    )
    assert second["written"] is False, "the second call changed nothing -- it must say so"
    assert second["duplicate"] is True, (
        "a caller must be able to tell 'archived it' from 'it was already archived', using the "
        "same written/duplicate vocabulary capture_note already returns -- not a new flag"
    )
    assert git_status_porcelain(template_vault) == ""
    assert _read_note(template_vault, note_id).status == "archived"


def test_archive_succeeds_even_when_the_note_has_no_effective_anchor(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/archive-after-detach-target.md",
        quote="a passage that will be detached before archiving",
    )
    _success(_detach(template_vault, TOPIC, note_id, 0))

    _success(_archive(template_vault, TOPIC, note_id))

    after = _read_note(template_vault, note_id)
    assert after.status == "archived"
    assert effective_anchor(after) is None, "archiving must not resurrect the detached history"


# ---------------------------------------------------------------------------
# Failure modes -- none of them anchor-shaped except PAGE_NOT_FOUND and
# NOTE_NOT_FOUND. Every other rejection below is "the named index is not a
# live anchor" in one of its three shapes (out of range, superseded,
# already detached) and funnels through the same INVALID_ARGUMENT code,
# since no dedicated code for it exists in the shared vocabulary.
# ---------------------------------------------------------------------------


def test_reanchor_on_an_unknown_note_id_fails_with_note_not_found(template_vault: Path):
    page = f"{TOPIC}/reanchor-unknown-target.md"
    _seed_page(template_vault, page, "# Page\n\nsome text.\n", "test: seed page")
    commits_before = git_commit_count(template_vault)

    result = _reanchor(template_vault, TOPIC, _UNKNOWN_NOTE_ID, 0, page=page, quote="some text.")

    assert _error_code(result) == "NOTE_NOT_FOUND"
    assert git_commit_count(template_vault) == commits_before


def test_detach_on_an_unknown_note_id_fails_with_note_not_found(template_vault: Path):
    commits_before = git_commit_count(template_vault)

    result = _detach(template_vault, TOPIC, _UNKNOWN_NOTE_ID, 0)

    assert _error_code(result) == "NOTE_NOT_FOUND"
    assert git_commit_count(template_vault) == commits_before


def test_archive_on_an_unknown_note_id_fails_with_note_not_found(template_vault: Path):
    commits_before = git_commit_count(template_vault)

    result = _archive(template_vault, TOPIC, _UNKNOWN_NOTE_ID)

    assert _error_code(result) == "NOTE_NOT_FOUND"
    assert git_commit_count(template_vault) == commits_before


def test_reanchor_targeting_a_page_that_no_longer_exists_fails_with_page_not_found(
    template_vault: Path,
):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-page-gone-original.md",
        quote="the passage before the page vanished",
    )
    missing_page = f"{TOPIC}/reanchor-page-gone-target.md"
    commits_before = git_commit_count(template_vault)

    result = _reanchor(
        template_vault, TOPIC, note_id, 0, page=missing_page, quote="whatever the passage was"
    )

    assert _error_code(result) == "PAGE_NOT_FOUND", (
        "unlike capture, a human deliberately re-pointing at a page must fail loudly rather "
        "than silently degrading -- a degradation here would discard their instruction"
    )
    assert git_commit_count(template_vault) == commits_before, (
        "a rejected reanchor must make no commit"
    )
    after = _read_note(template_vault, note_id)
    assert len(after.anchors) == 1, "the rejected reanchor must not have appended anything"


def test_detaching_a_note_with_no_anchors_at_all_is_rejected_before_any_write(
    template_vault: Path,
):
    note_id = "20260730-090000-bare-note"
    _seed_bare_note(template_vault, note_id)
    commits_before = git_commit_count(template_vault)

    result = _detach(template_vault, TOPIC, note_id, 0)

    assert _error_code(result) == "INVALID_ARGUMENT", (
        "an index into an empty anchor history names no live anchor -- the same rejection this "
        "module gives a superseded or detached target, since no dedicated code exists for it"
    )
    assert git_commit_count(template_vault) == commits_before


def test_reanchoring_a_note_with_no_anchors_at_all_is_rejected_before_any_write(
    template_vault: Path,
):
    note_id = "20260730-090100-bare-note-reanchor"
    _seed_bare_note(template_vault, note_id)
    page = f"{TOPIC}/reanchor-bare-target.md"
    _seed_page(template_vault, page, "# Page\n\nsome text.\n", "test: seed page")
    commits_before = git_commit_count(template_vault)

    result = _reanchor(template_vault, TOPIC, note_id, 0, page=page, quote="some text.")

    assert _error_code(result) == "INVALID_ARGUMENT", (
        "an index into an empty anchor history names no live anchor -- the same rejection this "
        "module gives a superseded or detached target, since no dedicated code exists for it"
    )
    assert git_commit_count(template_vault) == commits_before


def test_detaching_an_already_detached_note_is_rejected_before_any_write(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/detach-twice-target.md",
        quote="a passage detached, then detached again",
    )
    _success(_detach(template_vault, TOPIC, note_id, 0))
    commits_after_first = git_commit_count(template_vault)

    result = _detach(template_vault, TOPIC, note_id, 0)

    assert _error_code(result) == "INVALID_ARGUMENT", (
        "a terminal detached record can never be re-mutated -- this is the append-only "
        "invariant's negative-space case, and the original index is no longer live"
    )
    assert git_commit_count(template_vault) == commits_after_first
    assert len(_read_note(template_vault, note_id).anchors) == 2, (
        "a rejected second detach must not have appended anything"
    )


def test_reanchoring_an_already_detached_note_is_rejected_before_any_write(template_vault: Path):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/reanchor-after-detach-target.md",
        quote="a passage detached before a correction is attempted",
    )
    _success(_detach(template_vault, TOPIC, note_id, 0))
    page = f"{TOPIC}/reanchor-after-detach-new.md"
    _seed_page(template_vault, page, "# New\n\nthe new passage.\n", "test: seed new page")
    # Baseline *after* the page seed: `_seed_page` commits, so capturing before it
    # would compare a post-seed count against a pre-seed baseline and fail for a
    # correct implementation. The sibling double-detach test needs no such care --
    # nothing commits between its baseline and the rejected call.
    commits_after_detach = git_commit_count(template_vault)

    result = _reanchor(template_vault, TOPIC, note_id, 0, page=page, quote="the new passage.")

    assert _error_code(result) == "INVALID_ARGUMENT", (
        "a detached note has no effective anchor left to correct -- reanchoring it would "
        "silently resurrect a history the human explicitly ended"
    )
    assert git_commit_count(template_vault) == commits_after_detach
    assert len(_read_note(template_vault, note_id).anchors) == 2, (
        "a rejected reanchor-after-detach must not have appended anything"
    )


# ---------------------------------------------------------------------------
# The contamination vector -- an anchor's quote must never reach a scored surface
# ---------------------------------------------------------------------------


def test_reanchor_never_leaks_the_new_quotes_kb_prose_into_log_or_any_commit_subject(
    template_vault: Path,
):
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/contamination-reanchor-original.md",
        quote="an unremarkable original passage",
    )
    kb_page = f"{TOPIC}/contamination-reanchor-kb-page.md"
    distinctive_phrase = "the shoggoth wears a smiley-face mask over an emergent objective"
    _seed_page(
        template_vault, kb_page, f"# KB page\n\n{distinctive_phrase}.\n", "test: seed KB page"
    )

    _success(_reanchor(template_vault, TOPIC, note_id, 0, page=kb_page, quote=distinctive_phrase))

    log_text = (template_vault / "log.md").read_text(encoding="utf-8")
    assert distinctive_phrase not in log_text, (
        "the reanchored quote is verbatim KB prose -- it must never land in log.md, whose "
        "folder family is scored"
    )
    for subject in git_commit_subjects(template_vault):
        assert distinctive_phrase not in subject, (
            f"the reanchored quote leaked into a commit subject: {subject!r}"
        )
    entries = [line for line in log_text.splitlines() if _REANCHOR_OP in line]
    assert entries, "sanity: the reanchor must have written a log entry"
    assert any(note_id in entry for entry in entries), (
        "the log title must still name which note was reanchored"
    )


def test_detach_never_leaks_the_targeted_anchors_quote_into_log_or_any_commit_subject(
    template_vault: Path,
):
    distinctive_phrase = "the paperclip maximizer reads the eval spec more carefully than the goal"
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/contamination-detach-target.md",
        quote=distinctive_phrase,
    )

    _success(_detach(template_vault, TOPIC, note_id, 0))

    log_text = (template_vault / "log.md").read_text(encoding="utf-8")
    assert distinctive_phrase not in log_text, (
        "the detached anchor's quote is verbatim KB prose and must never reach log.md"
    )
    for subject in git_commit_subjects(template_vault):
        assert distinctive_phrase not in subject
    entries = [line for line in log_text.splitlines() if _DETACH_OP in line]
    assert entries, "sanity: the detach must have written a log entry"
    assert any(note_id in entry for entry in entries)


def test_archive_never_leaks_the_notes_anchor_quote_into_log_or_any_commit_subject(
    template_vault: Path,
):
    distinctive_phrase = "reward hacking is Goodhart's law wearing a lab coat"
    note_id = _seed_captured_note(
        template_vault,
        page_relpath=f"{TOPIC}/contamination-archive-target.md",
        quote=distinctive_phrase,
    )

    _success(_archive(template_vault, TOPIC, note_id))

    log_text = (template_vault / "log.md").read_text(encoding="utf-8")
    assert distinctive_phrase not in log_text
    for subject in git_commit_subjects(template_vault):
        assert distinctive_phrase not in subject
    entries = [line for line in log_text.splitlines() if _ARCHIVE_OP in line]
    assert entries, "sanity: the archive must have written a log entry"
    assert any(note_id in entry for entry in entries)
