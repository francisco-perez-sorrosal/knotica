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
from knotica.core.notes.anchor import (
    PROMOTED_EVAL_PREFIX,
    NoteDocument,
    live_anchors,
    parse_note,
)
from knotica.core.page import set_frontmatter_scalar
from knotica.core.operations.curate_example import curate_example
from knotica.store import VaultStore

__all__ = ["GAP_ELIGIBLE_INTENTS", "gap_intent_message", "no_live_pages_message", "promote_note"]

#: The note frontmatter key carrying the eval-bridge audit trail.
_PROMOTED_FIELD = "promoted"

_TARGET_TRAINSET = "trainset"
_TARGET_GAP = "gap"
_TARGET_GOLDEN = "golden"
_KNOWN_TARGETS = (_TARGET_TRAINSET, _TARGET_GAP, _TARGET_GOLDEN)

#: Intents that opt a note into gap filing (D2) -- a plain ``reflection`` never
#: does. Public because the dispatcher gates on the same policy: it used to
#: redeclare this set and its message under a comment reading "Mirrors
#: promote_note.GAP_ELIGIBLE_INTENTS exactly", which is a policy kept in sync
#: by convention -- the failure mode `vault_layout.py` exists to retire.
GAP_ELIGIBLE_INTENTS = frozenset({"dispute", "gap", "question"})

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

# INTERFACE_DESIGN section 8's "Promote a note with no question to ask" row,
# copied verbatim -- the error grammar is documented as the executable
# interface. It was specified there and enforced nowhere: the gap arm rejected
# an empty question inside `report_gap`, but the trainset arm appended
# `{"query": "", "answer": ""}` and committed it.
_NO_QUESTION_MESSAGE = "this note records a reflection, not a question the wiki should answer"
_NO_QUESTION_FIX = (
    "Ask the user for the question the wiki should answer, then call "
    "`notes action=promote` again with it."
)


def gap_intent_message(intent: str) -> str:
    """The intent-gate rejection text -- one declaration, two call sites."""
    return (
        "filing a gap needs a note whose intent is dispute, gap, or question; "
        f"this one is a {intent}"
    )


def no_live_pages_message(note_id: str) -> str:
    """The no-grounding-page rejection text -- one declaration, two call sites."""
    return (
        f"note {note_id!r} has no live anchored page to ground the question -- "
        "an eval question must be answerable from the knowledge base."
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

    if not question.strip():
        return err(ErrorCode.INVALID_ARGUMENT, _NO_QUESTION_MESSAGE, fix=_NO_QUESTION_FIX)

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
                path,
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
    path: str,
    pages_used: tuple[str, ...],
    *,
    question: str,
    answer: str,
    verdict: str,
) -> dict[str, object]:
    """Append one curated example, reusing ``curate_example`` -- no re-implementation.

    The note is stamped ``promoted: eval:<qa-id>`` **inside ``curate_example``'s
    own transaction**, via its ``extra_writes`` hook, so the crossing remains
    exactly one commit. Writing the stamp here instead would need a second
    commit and break the one-commit-per-mutating-operation invariant.

    This is the note-side audit trail that closes the asymmetry: a gap promotion
    is already discoverable in reverse from ``gaps.jsonl``
    (``reported_reason = note:<path>#0``), while a trainset promotion left no
    trace on either side -- ``qa.jsonl`` deliberately records no note provenance,
    and ``source`` is always ``curate_example``. Without this stamp, "how many
    note-derived questions are in the trainset" is unanswerable, which is the
    condition ``dec-059``'s reversal trigger is written in terms of.
    """
    if not answer.strip():
        return err(
            ErrorCode.INVALID_ARGUMENT,
            "a trainset example needs the grounded answer as well as the question -- "
            f"an empty answer recorded with verdict {verdict!r} asserts that nothing "
            "was a good answer, which silently degrades the training substrate.",
            fix=(
                "Ask the user for the answer the wiki gave, cited from the anchored pages, "
                "then call `notes action=promote` again with it."
            ),
        )
    if not pages_used:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            no_live_pages_message(note_id),
            fix="Anchor the note to a live KB page first (`notes action=reanchor`), then promote again.",
        )

    def stamp_note(record_id: str) -> dict[str, str]:
        return {
            path: set_frontmatter_scalar(
                store.read_text(path), _PROMOTED_FIELD, f"{PROMOTED_EVAL_PREFIX}{record_id}"
            )
        }

    return curate_example(
        store,
        vault_root,
        topic,
        question,
        pages_used,
        answer,
        verdict,
        extra_writes=stamp_note,
    )


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
    if document.intent not in GAP_ELIGIBLE_INTENTS:
        return err(
            ErrorCode.INVALID_ARGUMENT,
            gap_intent_message(document.intent),
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
