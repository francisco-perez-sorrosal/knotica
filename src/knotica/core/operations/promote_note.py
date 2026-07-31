"""``promote_note`` -- the eval bridge crossing out of the notes layer (dec-059).

Two destinations, both human-gated by an explicit call: ``target="trainset"``
delegates to :func:`~knotica.core.operations.curate_example.curate_example`;
``target="gap"`` delegates to :func:`~knotica.core.gapfill.report_gap`.
``target="golden"`` always rejects -- ``freeze()`` enforces trainset/golden
disjointness, so that destination is a one-way door that belongs behind
``golden_review``, not this action.

**``pages_used`` is always derived here, never accepted as an argument.**
There is no ``pages_used`` parameter on this function at all, so a caller
cannot inject a note path -- there is nowhere to inject it. The pages come
from :func:`~knotica.core.notes.anchor.live_anchors`: the newest anchor
record per distinct supersession group, excluding any group whose newest
record is ``kind="detached"``, filtered to non-empty ``page`` values
(a ``topic``-fidelity anchor names no page and contributes nothing). A note
whose live anchors resolve to zero grounding pages has nothing to ground an
eval question in and is a typed ``INVALID_ARGUMENT`` rejection -- never a
silent empty ``pages_used``.

**Gap filing is intent-gated.** Only ``dispute``/``gap``/``question`` notes
may file a gap; a plain ``reflection`` is rejected. The filed gap reuses the
existing ``origin="reported"`` shape (no fourth origin); provenance survives
in ``GapRecord.reported_reason`` as ``f"note:{path}#0"`` -- always anchor
index 0, because promotion is note-level (no anchor parameter on this
operation) and index 0 is the anchor of record: stable, canonical, and
guaranteed never to move by the append-only invariant.

**Contamination.** The note's own body and its own path must never reach
``qa.jsonl``, the gap queue, ``log.md``, or a commit subject -- only the
caller-supplied ``question``/``answer`` and the note's live KB page paths are
legitimate content there. The one deliberate exception is the gap pointer in
``reported_reason``, which *is* the note's path by design and belongs nowhere
else. This module opens no :class:`~knotica.core.transaction.VaultTransaction`
of its own -- every write happens inside the delegate's own transaction, so
no title derived from this module's inputs ever reaches ``log.md`` or a
commit subject.
"""

from pathlib import Path, PurePath

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.gapfill import report_gap
from knotica.core.notes.anchor import NoteDocument, live_anchors, parse_note
from knotica.core.operations.curate_example import curate_example
from knotica.store import VaultStore

__all__ = ["promote_note"]

_TARGET_TRAINSET = "trainset"
_TARGET_GAP = "gap"
_TARGET_GOLDEN = "golden"
_KNOWN_TARGETS = (_TARGET_TRAINSET, _TARGET_GAP, _TARGET_GOLDEN)

#: Intents that opt a note into gap filing (D2) -- a plain ``reflection`` never does.
_GAP_ELIGIBLE_INTENTS = frozenset({"dispute", "gap", "question"})

_NOTES_DIRECTORY_TEMPLATE = "notes/{topic}"
_MARKDOWN_SUFFIX = ".md"

#: Promotion carries no anchor parameter, so the ``reported_reason`` pointer
#: always names the anchor of record -- the one index guaranteed never to
#: move (see ``anchor.anchor_of_record``). Any other choice would be
#: arbitrary.
_ANCHOR_OF_RECORD_POINTER_INDEX = 0

_GOLDEN_DEFERRED_MESSAGE = (
    "promoting to the held-out (golden) set is deferred: trainset and golden must "
    "stay disjoint, so the choice is one-way and needs its own review gate"
)
_GOLDEN_DEFERRED_FIX = (
    "Promote to the training set instead: `notes action=promote target=trainset`. "
    "Golden promotion runs through `golden_review`, not this action."
)


def promote_note(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    note_id: str,
    target: str,
    *,
    question: str = "",
    answer: str = "",
    verdict: str = "good",
) -> dict[str, object]:
    """Promote one note's derived question across the notes/KB boundary.

    Args:
        store: The vault storage backend.
        vault_root: The already-resolved vault root.
        topic: The note's owning topic.
        note_id: The note's id (its filename stem).
        target: ``"trainset"``, ``"gap"``, or ``"golden"`` (always rejects).
        question: The wiki question to promote. Required by both live
            destinations; defaulting from the note's own body (when the note
            already is a question) is the dispatcher's job, not this one.
        answer: The grounded answer, used only by ``target="trainset"``.
        verdict: ``"good"`` or ``"bad"``, used only by ``target="trainset"``.

    Returns:
        A success envelope (the delegate's own), or a typed failure envelope.
    """
    if target == _TARGET_GOLDEN:
        return err(ErrorCode.INVALID_ARGUMENT, _GOLDEN_DEFERRED_MESSAGE, fix=_GOLDEN_DEFERRED_FIX)
    if target not in _KNOWN_TARGETS:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            f"promote target must be one of trainset, gap, golden; got {target!r}.",
        )

    loaded = _load_note(store, topic, note_id)
    if not isinstance(loaded, tuple):
        return loaded
    document, path = loaded
    pages_used = _grounding_pages(document)

    try:
        if target == _TARGET_TRAINSET:
            return _promote_to_trainset(
                store,
                vault_root,
                topic,
                note_id,
                pages_used,
                question=question,
                answer=answer,
                verdict=verdict,
            )
        return _promote_to_gap(
            store, vault_root, topic, document, path, pages_used, question=question
        )
    except KnoticaError as error:
        return error.envelope()


def _promote_to_trainset(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    note_id: str,
    pages_used: tuple[str, ...],
    *,
    question: str,
    answer: str,
    verdict: str,
) -> dict[str, object]:
    """Append one curated example, reusing ``curate_example`` -- no re-implementation."""
    if not pages_used:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            f"note {note_id!r} has no live anchored page to ground the question -- "
            "an eval question must be answerable from the knowledge base.",
            fix="Anchor the note to a live KB page first (`notes action=reanchor`), then promote again.",
        )
    return curate_example(store, vault_root, topic, question, pages_used, answer, verdict)


def _promote_to_gap(
    store: VaultStore,
    vault_root: str | PurePath,
    topic: str,
    document: NoteDocument,
    path: str,
    pages_used: tuple[str, ...],
    *,
    question: str,
) -> dict[str, object]:
    """File one reported gap, reusing ``report_gap`` -- no re-implementation."""
    if document.intent not in _GAP_ELIGIBLE_INTENTS:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            "filing a gap needs a note whose intent is dispute, gap, or question; "
            f"this one is a {document.intent}",
            fix=(
                "Ask the user whether the wiki is actually wrong. If it is, they can change "
                "the note's intent in Obsidian, or file it directly with `gap_report`."
            ),
        )
    reported_reason = f"note:{path}#{_ANCHOR_OF_RECORD_POINTER_INDEX}"
    # report_gap is typed str | Path (narrower than this module's str | PurePath);
    # Path(...) accepts either and satisfies both at runtime and for mypy.
    result = report_gap(
        store,
        Path(vault_root),
        topic,
        question,
        reason=reported_reason,
        reference_pages=pages_used,
    )
    return ok(
        {
            "topic": result.topic,
            "gap_id": result.gap_id,
            "qa_id": result.qa_id,
            "question": result.question,
            "fault_class": result.fault_class,
            "status": result.status,
            "origin": result.origin,
            "reference_pages": list(result.reference_pages),
            "written": result.written,
        }
    )


def _grounding_pages(document: NoteDocument) -> tuple[str, ...]:
    """The distinct, currently-live KB pages this note anchors -- never the note's own path.

    Reads :func:`~knotica.core.notes.anchor.live_anchors`, the liveness
    primitive: a page whose chain ends ``detached``, or a ``topic``-fidelity
    anchor naming no page at all, contributes nothing. Order follows the
    anchor history (document order), deduplicated.
    """
    pages: list[str] = []
    for anchor in live_anchors(document):
        if anchor.page and anchor.page not in pages:
            pages.append(anchor.page)
    return tuple(pages)


def _load_note(
    store: VaultStore, topic: str, note_id: str
) -> tuple[NoteDocument, str] | dict[str, object]:
    """The note's parsed document and path, or a ``NOTE_NOT_FOUND`` envelope."""
    path = f"{_NOTES_DIRECTORY_TEMPLATE.format(topic=topic)}/{note_id}{_MARKDOWN_SUFFIX}"
    if not store.exists(path):
        return err(
            ErrorCode.NOTE_NOT_FOUND,
            f"promote failed because no note named {note_id!r} exists in topic {topic!r}.",
        )
    document, error = parse_note(store.read_text(path))
    if error is not None or document is None:
        return err(
            ErrorCode.NOTE_NOT_FOUND,
            f"promote failed because note {note_id!r} could not be read: {error}",
        )
    return document, path
