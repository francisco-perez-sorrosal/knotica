"""``reanchor``, ``detach``, ``archive`` -- the note-correction operations.

Three human review actions on an already-captured note, each following
:mod:`~knotica.core.operations.capture_note`'s template: validate everything
first, build the whole updated file in memory, then take the vault lock
exactly once inside a single
:class:`~knotica.core.transaction.VaultTransaction`.

**A correction is appended, never a rewrite.** ``reanchor`` appends a new
anchor recording what a human just confirmed (``kind="reanchored"``);
``detach`` appends a terminal record saying the note no longer points
anywhere (``kind="detached"``). Neither ever touches a record that already
exists on disk -- the anchor of record at index 0, and every anchor appended
after it, stay byte-identical forever. ``archive`` is the one operation of
the three that is not about anchors at all: it flips the note's frontmatter
``status`` and leaves the ``## Anchors`` section completely alone.

``reanchor`` and ``detach`` act on one anchor at a time, named by its 0-based
index into the note's append-only history. Only a *live* target is
addressable -- see :func:`~knotica.core.notes.anchor.live_anchors`:
supersession and detachment are per distinct ``page``, not per note, so an
index that is out of range, superseded by a later record on the same page, or
itself the terminal ``detached`` kind is rejected with ``INVALID_ARGUMENT``
before any write. ``reanchor`` accepts an explicit ``(page, quote)`` pair, or
neither: leaving both empty means "accept the currently-resolved
projection" -- the drift queue's one-click accept, not a separate code path.

**An anchor's quote is verbatim knowledge-base prose, and it must never reach
a shared, scored surface.** :class:`~knotica.core.transaction.VaultTransaction`
writes its ``title`` argument into both the commit subject and the
vault-root ``log.md`` entry, whose folder family is scored. All three
operations here derive that title from the note's own id alone -- never an
anchor's quote, never the note body.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.notes.anchor import (
    AnchorRecord,
    NoteDocument,
    append_anchor_text,
    live_anchors,
    parse_note,
    serialize_note,
)
from knotica.core.notes.resolve import resolve_anchor
from knotica.core.notes_config import resolve_notes_config
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

__all__ = ["archive", "detach", "reanchor"]

#: Operation names stamped on the commit subject and the operation log.
_REANCHOR_OP = "note_reanchor"
_DETACH_OP = "note_detach"
_ARCHIVE_OP = "note_archive"

_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"
_MARKDOWN_SUFFIX = ".md"
_ARCHIVED_STATUS = "archived"
_REANCHORED_KIND = "reanchored"
_DETACHED_KIND = "detached"

#: Fidelity of a freshly appended anchor -- ``span`` when it names a page,
#: ``topic`` when it does not, mirroring capture's own convention.
_SPAN_FIDELITY = "span"
_TOPIC_FIDELITY = "topic"

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Row-specific fix text for ``reanchor``'s ``PAGE_NOT_FOUND`` failure --
#: the error grammar gives this row a fallback the generic
#: :data:`~knotica.core.errors.DEFAULT_FIX` lacks: a
#: user pointing at a deleted page can keep the note without an anchor.
_PAGE_NOT_FOUND_FIX = (
    "Call `search` in this topic for the surviving page, or "
    "`tend action=notes notes_action=detach` to keep the note without an anchor."
)


def reanchor(
    store: VaultStore,
    vault_root: str | PurePath,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    anchor: int,
    *,
    page: str = "",
    quote: str = "",
) -> dict[str, object]:
    """Append a new anchor correcting ``anchor``, leaving it byte-unchanged.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root.
        vcs: Vault git access, read here for the pre-write ``HEAD`` sha and,
            when ``page``/``quote`` are empty, the anchor's historical text.
        topic: The note's owning topic.
        note_id: The note's id (its filename stem).
        anchor: 0-based index into the note's anchor history; must be live.
        page: The page to re-pin to. Empty (with ``quote``) means "accept the
            currently-resolved projection".
        quote: The passage to pin. Empty (with ``page``) means the same.

    Returns:
        A success envelope ``{note_id, path, anchor_index, kind, commit}``,
        or a typed failure envelope.
    """
    loaded = _load_note(store, topic, note_id, op_label="reanchor")
    if not isinstance(loaded, tuple):
        return loaded
    document, path = loaded
    target = _live_target(document, anchor, op_label="reanchor")
    if not isinstance(target, AnchorRecord):
        return target
    if bool(page) != bool(quote):
        return err(
            ErrorCode.INVALID_ARGUMENT,
            "reanchor failed because page and quote must be supplied together, or both "
            "left empty to accept the currently-resolved projection.",
        )

    try:
        if page:
            if not store.exists(page):
                return err(
                    ErrorCode.PAGE_NOT_FOUND,
                    f"reanchor failed because page {page!r} does not exist.",
                    fix=_PAGE_NOT_FOUND_FIX,
                )
            new_page, new_quote = page, quote
        else:
            new_page, new_quote = _accept_projection(store, vcs, target)

        new_anchor = AnchorRecord(
            page=new_page,
            heading="",
            fidelity=_SPAN_FIDELITY if new_page else _TOPIC_FIDELITY,
            pinned_at=vcs.head_sha(),
            quote=new_quote,
            kind=_REANCHORED_KIND,
        )
        return _append_and_commit(
            store,
            vault_root,
            document,
            new_anchor,
            op=_REANCHOR_OP,
            topic=topic,
            note_id=note_id,
            path=path,
        )
    except KnoticaError as error:
        return error.envelope()


def detach(
    store: VaultStore,
    vault_root: str | PurePath,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    anchor: int,
) -> dict[str, object]:
    """Append a terminal ``detached`` record for ``anchor``'s page.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root.
        vcs: Vault git access, read here for the pre-write ``HEAD`` sha.
        topic: The note's owning topic.
        note_id: The note's id (its filename stem).
        anchor: 0-based index into the note's anchor history; must be live.

    Returns:
        A success envelope ``{note_id, path, anchor_index, kind, commit}``,
        or a typed failure envelope.
    """
    loaded = _load_note(store, topic, note_id, op_label="detach")
    if not isinstance(loaded, tuple):
        return loaded
    document, path = loaded
    target = _live_target(document, anchor, op_label="detach")
    if not isinstance(target, AnchorRecord):
        return target

    new_anchor = replace(target, pinned_at=vcs.head_sha(), kind=_DETACHED_KIND)
    try:
        return _append_and_commit(
            store,
            vault_root,
            document,
            new_anchor,
            op=_DETACH_OP,
            topic=topic,
            note_id=note_id,
            path=path,
        )
    except KnoticaError as error:
        return error.envelope()


def archive(
    store: VaultStore,
    vault_root: str | PurePath,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
) -> dict[str, object]:
    """Set the note's frontmatter ``status`` to ``archived``; touches no anchor.

    Idempotent, mirroring :func:`~knotica.core.operations.capture_note.capture_note`'s
    own duplicate-call precedent: archiving an already-archived note makes no
    second commit and returns ``written=False, duplicate=True`` rather than a
    new flag.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root.
        vcs: Vault git access, read here for the current ``HEAD`` sha.
        topic: The note's owning topic.
        note_id: The note's id (its filename stem).

    Returns:
        A success envelope ``{note_id, path, status, written, duplicate,
        commit}``, or a typed failure envelope.
    """
    loaded = _load_note(store, topic, note_id, op_label="archive")
    if not isinstance(loaded, tuple):
        return loaded
    document, path = loaded
    if document.status == _ARCHIVED_STATUS:
        return ok(
            {
                "note_id": note_id,
                "path": path,
                "status": _ARCHIVED_STATUS,
                "written": False,
                "duplicate": True,
                "commit": vcs.head_sha(),
            }
        )

    updated = datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
    content = serialize_note(replace(document, status=_ARCHIVED_STATUS, updated=updated))
    try:
        with VaultTransaction(store, vault_root, _ARCHIVE_OP, topic, _title(note_id)) as txn:
            txn.write(path, content)
    except KnoticaError as error:
        return error.envelope()
    return ok(
        {
            "note_id": note_id,
            "path": path,
            "status": _ARCHIVED_STATUS,
            "written": True,
            "duplicate": False,
            "commit": txn.result.commit_sha,
        }
    )


# ---------------------------------------------------------------------------
# Shared write path -- reanchor and detach both append one record and commit.
# ---------------------------------------------------------------------------


def _append_and_commit(
    store: VaultStore,
    vault_root: str | PurePath,
    document: NoteDocument,
    new_anchor: AnchorRecord,
    *,
    op: str,
    topic: str,
    note_id: str,
    path: str,
) -> dict[str, object]:
    """Splice ``new_anchor`` onto the file's existing bytes and commit it once.

    Deliberately **not** ``serialize_note(replace(document, anchors=...))``. That
    re-renders every bullet canonically, so correcting anchor 0 of a
    hand-authored note rewrote the formatting of anchors 1..n that the operation
    was never asked to touch -- valid-but-non-canonical spacing the user typed,
    replaced in the user's own file. The parsed records were identical either
    way, which is why record-level tests never saw it.

    The append-only contract is about the note file, not the tuple: appending
    bytes to the end is what "the original anchor of record is never modified"
    actually means on disk, and it keeps a correction's diff to the lines the
    correction added.
    """
    content = append_anchor_text(store.read_text(path), new_anchor)
    with VaultTransaction(store, vault_root, op, topic, _title(note_id)) as txn:
        txn.write(path, content)
    return ok(
        {
            "note_id": note_id,
            "path": path,
            "anchor_index": len(document.anchors),
            "kind": new_anchor.kind,
            "commit": txn.result.commit_sha,
        }
    )


# ---------------------------------------------------------------------------
# Loading and target validation
# ---------------------------------------------------------------------------


def _load_note(
    store: VaultStore, topic: str, note_id: str, *, op_label: str
) -> tuple[NoteDocument, str] | dict[str, object]:
    """The note's parsed document and path, or a ``NOTE_NOT_FOUND`` envelope."""
    path = f"{_NOTES_DIRECTORY_TEMPLATE.format(topic=topic)}/{note_id}{_MARKDOWN_SUFFIX}"
    if not store.exists(path):
        return err(
            ErrorCode.NOTE_NOT_FOUND,
            f"{op_label} failed because no note named {note_id!r} exists in topic {topic!r}.",
        )
    document, error = parse_note(store.read_text(path))
    if error is not None or document is None:
        return err(
            ErrorCode.NOTE_NOT_FOUND,
            f"{op_label} failed because note {note_id!r} could not be read: {error}",
        )
    return document, path


def _live_target(
    document: NoteDocument, index: int, *, op_label: str
) -> AnchorRecord | dict[str, object]:
    """The anchor at ``index``, or an ``INVALID_ARGUMENT`` envelope when it is not live.

    "Not live" covers every shape this module rejects the same way: an index
    out of range, one superseded by a later record on the same page, and one
    that is itself terminal (``kind="detached"``) -- there is no dedicated
    code for any of them in the shared error vocabulary.
    """
    anchors = document.anchors
    if not 0 <= index < len(anchors):
        return err(
            ErrorCode.INVALID_ARGUMENT,
            f"{op_label} failed because anchor index {index} is out of range -- this note "
            f"has {len(anchors)} anchor(s).",
        )
    live_ids = {id(record) for record in live_anchors(document)}
    if id(anchors[index]) not in live_ids:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            f"{op_label} failed because anchor index {index} is not live -- it has been "
            "superseded or detached by a later record for the same page.",
        )
    return anchors[index]


# ---------------------------------------------------------------------------
# Accepting the currently-resolved projection (empty page/quote)
# ---------------------------------------------------------------------------


def _accept_projection(store: VaultStore, vcs: VaultVcs, target: AnchorRecord) -> tuple[str, str]:
    """The ``(page, quote)`` an empty-args reanchor re-pins to.

    Resolution never migrates pages -- it only ever asks where, on the
    anchor's own page, the quote now sits -- so the accepted page is always
    ``target.page``, unchanged. The accepted quote is whatever text the
    resolution ladder currently projects the anchor onto; when nothing has
    drifted that is byte-identical to ``target.quote``.
    """
    if not target.page:
        return target.page, target.quote
    historical_text = vcs.read_file_at(target.pinned_at, target.page) or ""
    head_text = store.read_text(target.page) if store.exists(target.page) else None
    if head_text is None:
        return target.page, target.quote
    config = resolve_notes_config()
    projection = resolve_anchor(
        historical_text,
        head_text,
        target,
        guess_threshold=config.guess_threshold,
        complete_orphan_threshold=config.complete_orphan_threshold,
    )
    span = projection.span or projection.best_guess
    if span is None:
        return target.page, target.quote
    return target.page, head_text[span[0] : span[1]]


def _title(note_id: str) -> str:
    """Commit/log title derived from the note id alone -- never a quote or the body.

    See the module docstring: an anchor's quote is verbatim knowledge-base
    prose, and titling a correction with it would copy that prose into
    ``log.md``, whose folder family is scored.
    """
    return f"note {note_id}"
