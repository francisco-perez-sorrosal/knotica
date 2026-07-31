"""Payload construction for the ``notes`` dispatcher's mutating actions --
``reanchor``, ``detach``, ``promote``, and ``archive`` (dry-run/apply mode
pair, one commit per ``apply``).

``mode=dry-run`` (the schema default) previews and returns the uniform
decision envelope every mutating gate in this codebase renders
(``suggestions_review._dry_run`` is the precedent); ``mode=apply`` performs
exactly one commit via the already-tested delegate for the action --
:func:`~knotica.core.operations.reanchor_note.reanchor`/:func:`~knotica.core.operations.reanchor_note.detach`/
:func:`~knotica.core.operations.reanchor_note.archive`, or
:func:`~knotica.core.operations.promote_note.promote_note` -- exposed, not
reimplemented. None of the four has a plan-only entry point, so every
dry-run path in this module re-derives the same validation read-only instead
of routing the preview through the write path.

``promote`` is the one action that can write outside the notes layer --
``target=trainset``/``target=gap`` cross into ``qa.jsonl`` or the gap queue.
Its ``question`` defaulting (from the note's own text, when the note already
is a ``question``) and its ``verdict`` enum are this dispatcher's own job:
``promote_note`` takes no default for either.

Shared vocabulary, argument validation, and the drift-live-quote lookup live
in the leaf module :mod:`knotica.mcp_server.tools_dispatch_notes_common`;
the read-only sibling is :mod:`knotica.mcp_server.tools_dispatch_notes_read`.
The router in :mod:`knotica.mcp_server.tools_dispatch_notes` only registers
the MCP tool and dispatches into this module and its read-only sibling -- it
does not know how a page is built.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.notes.anchor import AnchorRecord, NoteDocument, live_anchors
from knotica.core.notes.resolve import Projection
from knotica.core.notes.store import NotesListing, ResolvedNote, list_notes
from knotica.core.operations.promote_note import (
    GAP_ELIGIBLE_INTENTS,
    gap_intent_message,
    no_live_pages_message,
    promote_note,
)
from knotica.core.operations.reanchor_note import archive, detach, reanchor
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.tools_dispatch_notes_common import _drift_live_quote, _validate_mode
from knotica.store import VaultStore

_REANCHOR_ACTION = "reanchor"
_DETACH_ACTION = "detach"
_PROMOTE_ACTION = "promote"
_ARCHIVE_ACTION = "archive"

#: Identical wording to `core.operations.reanchor_note.reanchor`'s page/quote
#: pairing gate, so a caller sees the same rejection previewing or applying.
_PAGE_QUOTE_PAIRING_MESSAGE = (
    "reanchor failed because page and quote must be supplied together, or both "
    "left empty to accept the currently-resolved projection."
)

# ---------------------------------------------------------------------------
# promote -- target enum, verdict enum, and the messages that mirror
# `core.operations.promote_note`'s own gates. Duplicated rather than
# imported, same reasoning as `_PAGE_QUOTE_PAIRING_MESSAGE` above: the
# dispatcher's dry-run path has no delegate to call for a preview, so it
# re-derives the same rejection read-only and must say the same thing.
# ---------------------------------------------------------------------------

_TARGET_TRAINSET = "trainset"
_TARGET_GAP = "gap"
_TARGET_GOLDEN = "golden"
_PROMOTE_TARGETS = (_TARGET_TRAINSET, _TARGET_GAP, _TARGET_GOLDEN)
_DEFAULT_PROMOTE_TARGET = _TARGET_TRAINSET

_PROMOTE_VERDICTS = ("good", "bad")
_DEFAULT_VERDICT = "good"

#: `question` defaults from the note's own body only when the note's own
#: `intent` already carries this value -- the same enum value `promote
#: target=gap`'s intent gate and `notes action=list`'s `intent` filter use.
_QUESTION_INTENT = "question"

_GOLDEN_DEFERRED_MESSAGE = (
    "promoting to the held-out (golden) set is deferred: trainset and golden must "
    "stay disjoint, so the choice is one-way and needs its own review gate"
)
_GOLDEN_DEFERRED_FIX = (
    "Promote to the training set instead: `notes action=promote target=trainset`. "
    "Golden promotion runs through `golden_review`, not this action."
)
_GAP_INTENT_FIX = (
    "Ask the user whether the wiki is actually wrong. If it is, they can change "
    "the note's intent in Obsidian, or file it directly with `gap_report`."
)
_NO_LIVE_PAGES_FIX = (
    "Anchor the note to a live KB page first (`notes action=reanchor`), then promote again."
)


def _reanchor_payload(
    store: VaultStore,
    vault_path: Path,
    vcs: VaultVcs,
    topic: str,
    *,
    note_id: str,
    anchor_index: int,
    mode: str,
    page: str,
    quote: str,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    if _validate_mode(mode) == "apply":
        return _apply_action(
            topic,
            _REANCHOR_ACTION,
            lambda: reanchor(
                store, vault_path, vcs, topic, note_id, anchor_index, page=page, quote=quote
            ),
        )
    anchor, projection = _resolve_live_anchor(
        store,
        vcs,
        topic,
        note_id,
        anchor_index,
        op_label=_REANCHOR_ACTION,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    if bool(page) != bool(quote):
        raise KnoticaError(ErrorCode.INVALID_ARGUMENT, _PAGE_QUOTE_PAIRING_MESSAGE)
    target = f"to {page!r}" if page else "to the currently-resolved match"
    corrected = f"a corrected anchor at {page!r}" if page else "the currently-resolved projection"
    return _decision_envelope(
        action=_REANCHOR_ACTION,
        topic=topic,
        note_id=note_id,
        decision_id=f"{note_id}:{anchor_index}",
        summary=f"Reanchor note {note_id}'s anchor {anchor_index} {target}",
        preview=f"Reanchor -> pins {corrected}, appended after the original, which stays intact.",
        context=_anchor_context(store, note_id, anchor_index, anchor, projection),
        provenance=_anchor_provenance(anchor),
    )


def _detach_payload(
    store: VaultStore,
    vault_path: Path,
    vcs: VaultVcs,
    topic: str,
    *,
    note_id: str,
    anchor_index: int,
    mode: str,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    if _validate_mode(mode) == "apply":
        return _apply_action(
            topic,
            _DETACH_ACTION,
            lambda: detach(store, vault_path, vcs, topic, note_id, anchor_index),
        )
    anchor, projection = _resolve_live_anchor(
        store,
        vcs,
        topic,
        note_id,
        anchor_index,
        op_label=_DETACH_ACTION,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    return _decision_envelope(
        action=_DETACH_ACTION,
        topic=topic,
        note_id=note_id,
        decision_id=f"{note_id}:{anchor_index}",
        summary=f"Detach note {note_id}'s anchor {anchor_index}",
        preview=(
            "Detach -> appends a terminal record saying this anchor no longer "
            "points anywhere; the note itself is kept."
        ),
        context=_anchor_context(store, note_id, anchor_index, anchor, projection),
        provenance=_anchor_provenance(anchor),
    )


def _promote_payload(
    store: VaultStore,
    vault_path: Path,
    vcs: VaultVcs,
    topic: str,
    *,
    note_id: str,
    mode: str,
    target: str,
    question: str,
    answer: str,
    verdict: str,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    cleaned_verdict = _validate_verdict(verdict)
    if _validate_mode(mode) == "apply":
        resolved_question = _promote_apply_question(
            store,
            vcs,
            topic,
            note_id,
            question,
            guess_threshold=guess_threshold,
            complete_orphan_threshold=complete_orphan_threshold,
        )
        return _apply_action(
            topic,
            _PROMOTE_ACTION,
            lambda: promote_note(
                store,
                vault_path,
                topic,
                note_id,
                target,
                question=resolved_question,
                answer=answer,
                verdict=cleaned_verdict,
            ),
        )
    return _promote_preview(
        store,
        vcs,
        topic,
        note_id,
        target,
        question,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )


def _promote_apply_question(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    question: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> str:
    """The ``question`` an apply call promotes -- defaulted from the note's
    own text only when the caller left it empty (see :func:`_default_question`);
    skips the note lookup entirely when an explicit value is already given."""
    if question:
        return question
    note = _resolve_note(
        store,
        vcs,
        topic,
        note_id,
        op_label=_PROMOTE_ACTION,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    return _default_question(note.document, question)


def _promote_preview(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    target: str,
    question: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    """The dry-run decision envelope -- replicates every gate ``mode=apply``
    would hit (target validity, the gap intent gate, the trainset
    no-live-pages gate), so a successful preview promises what ``apply``
    will do."""
    _validate_promote_target(target)
    note = _resolve_note(
        store,
        vcs,
        topic,
        note_id,
        op_label=_PROMOTE_ACTION,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    resolved_question = _default_question(note.document, question)
    pages_used = _grounding_pages(note.document)
    if target == _TARGET_GAP and note.document.intent not in GAP_ELIGIBLE_INTENTS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            gap_intent_message(note.document.intent),
            fix=_GAP_INTENT_FIX,
        )
    if target == _TARGET_TRAINSET and not pages_used:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT, no_live_pages_message(note_id), fix=_NO_LIVE_PAGES_FIX
        )
    return _decision_envelope(
        action=_PROMOTE_ACTION,
        topic=topic,
        note_id=note_id,
        decision_id=f"{note_id}:{_PROMOTE_ACTION}",
        summary=f"Promote note {note_id} to {target}",
        preview=f"Promote -> writes into {target}, grounded in {len(pages_used)} live page(s).",
        context={
            "note_id": note_id,
            "target": target,
            "intent": note.document.intent,
            "question": resolved_question,
            "pages_used": list(pages_used),
        },
        provenance={"note_created": note.document.created, "note_updated": note.document.updated},
    )


def _archive_payload(
    store: VaultStore,
    vault_path: Path,
    vcs: VaultVcs,
    topic: str,
    *,
    note_id: str,
    mode: str,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> dict[str, Any]:
    if _validate_mode(mode) == "apply":
        return _apply_action(
            topic, _ARCHIVE_ACTION, lambda: archive(store, vault_path, vcs, topic, note_id)
        )
    note = _resolve_note(
        store,
        vcs,
        topic,
        note_id,
        op_label=_ARCHIVE_ACTION,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    return _decision_envelope(
        action=_ARCHIVE_ACTION,
        topic=topic,
        note_id=note_id,
        decision_id=f"{note_id}:{_ARCHIVE_ACTION}",
        summary=f"Archive note {note_id}",
        preview="Archive -> flips the note's status to archived; the file and its anchors are kept.",
        context={"note_id": note_id, "path": note.path, "status": note.document.status},
        provenance={"note_created": note.document.created, "note_updated": note.document.updated},
    )


def _apply_action(
    topic: str, action: str, mutate: Callable[[], dict[str, object]]
) -> dict[str, Any]:
    """Run a delegate call, re-raise its failure envelope (if any), and wrap
    a success into the dispatcher's ``apply`` envelope.

    ``committed`` reads the delegate's own ``written`` flag when it reports
    one (``archive``'s idempotent replay makes no second commit) and
    otherwise defaults ``True`` -- ``reanchor``/``detach``/``promote`` always
    commit on success and carry no such flag.
    """
    result = dict(mutate())
    _raise_if_error(result)
    return {
        "mode": "apply",
        "topic": topic,
        "action": action,
        "committed": bool(result.get("written", True)),
        **result,
    }


def _resolve_note(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    *,
    op_label: str,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> ResolvedNote:
    """The note ``note_id``'s parsed document and resolved anchors, or a
    raised ``NOTE_NOT_FOUND`` -- the lookup every dry-run preview in this
    module shares."""
    listing = list_notes(
        store,
        vcs,
        topic,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    return _find_note(topic, listing, note_id, op_label=op_label)


def _resolve_live_anchor(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    note_id: str,
    anchor_index: int,
    *,
    op_label: str,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> tuple[AnchorRecord, Projection]:
    """The live anchor/projection pair a dry-run preview targets."""
    note = _resolve_note(
        store,
        vcs,
        topic,
        note_id,
        op_label=op_label,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    return _live_anchor(note, anchor_index, op_label=op_label)


def _decision_envelope(
    *,
    action: str,
    topic: str,
    note_id: str,
    decision_id: str,
    summary: str,
    preview: str,
    context: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """The uniform dry-run shape every mutating gate renders (see
    `suggestions_review._dry_run`). None of the four actions this module
    exposes takes a `reason` argument, so `reason_required` is always
    `False` -- there is no field to require one for."""
    return {
        "mode": "dry-run",
        "topic": topic,
        "note_id": note_id,
        "action": action,
        "decision_id": decision_id,
        "summary": summary,
        "context": context,
        "options": [{"action": action, "preview": preview, "reversible": False}],
        "provenance": provenance,
        "reason_required": False,
    }


def _anchor_context(
    store: VaultStore,
    note_id: str,
    anchor_index: int,
    anchor: AnchorRecord,
    projection: Projection,
) -> dict[str, Any]:
    """What a human needs to decide -- the drift queue's own per-item fields
    (see `tools_dispatch_notes_read._drift_item`), reused rather than
    re-derived."""
    return {
        "note_id": note_id,
        "anchor_index": anchor_index,
        "page": anchor.page,
        "pinned_quote": anchor.quote,
        "live_quote": _drift_live_quote(store, anchor, projection),
        "status": projection.status,
    }


def _anchor_provenance(anchor: AnchorRecord) -> dict[str, Any]:
    """When and at what fidelity the anchor was pinned."""
    return {"pinned_at": anchor.pinned_at, "fidelity": anchor.fidelity}


def _find_note(topic: str, listing: NotesListing, note_id: str, *, op_label: str) -> ResolvedNote:
    """The note ``note_id`` in ``listing``, or a raised ``NOTE_NOT_FOUND``."""
    cleaned_id = note_id.strip()
    for note in listing.notes:
        if note.document.id == cleaned_id:
            return note
    raise KnoticaError(
        ErrorCode.NOTE_NOT_FOUND,
        f"notes action={op_label} failed because no note {cleaned_id!r} exists in topic {topic!r}.",
    )


def _live_anchor(
    note: ResolvedNote, index: int, *, op_label: str
) -> tuple[AnchorRecord, Projection]:
    """The anchor/projection pair at ``index``, or a raised ``INVALID_ARGUMENT``
    for an out-of-range or already-superseded/detached target. Mirrors
    ``core.operations.reanchor_note._live_target``'s rejection text exactly,
    so a caller sees the same error previewing or applying."""
    anchors = note.document.anchors
    if not 0 <= index < len(anchors):
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"{op_label} failed because anchor index {index} is out of range -- this "
            f"note has {len(anchors)} anchor(s).",
        )
    live_ids = {id(record) for record in live_anchors(note.document)}
    if id(anchors[index]) not in live_ids:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"{op_label} failed because anchor index {index} is not live -- it has "
            "been superseded or detached by a later record for the same page.",
        )
    return note.resolved_anchors[index]


def _grounding_pages(document: NoteDocument) -> tuple[str, ...]:
    """The distinct, currently-live KB pages this note anchors. Mirrors
    ``promote_note._grounding_pages`` exactly -- same duplication reasoning
    as the message constants above."""
    pages: list[str] = []
    for anchor in live_anchors(document):
        if anchor.page and anchor.page not in pages:
            pages.append(anchor.page)
    return tuple(pages)


def _default_question(document: NoteDocument, question: str) -> str:
    """``question`` defaulting is this dispatcher's own job (see module
    docstring): ``promote_note`` takes no default of its own. Read as
    ``NoteDocument.body``, verbatim, when the note's own ``intent`` already
    is ``question``; otherwise the caller-supplied value passes through
    unchanged, including empty."""
    if question or document.intent != _QUESTION_INTENT:
        return question
    return document.body


def _validate_promote_target(target: str) -> None:
    """Mirrors ``promote_note``'s own target gate exactly, so a dry-run
    preview rejects the same way ``mode=apply`` would -- core has no
    plan-only entry point (module docstring), so the dry-run path
    re-derives this validation read-only instead of routing the preview
    through the write path."""
    if target == _TARGET_GOLDEN:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT, _GOLDEN_DEFERRED_MESSAGE, fix=_GOLDEN_DEFERRED_FIX
        )
    if target not in _PROMOTE_TARGETS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"promote target must be one of trainset, gap, golden; got {target!r}.",
        )


def _validate_verdict(verdict: str) -> str:
    """Wholly new dispatcher-level behavior: ``curate_example`` accepts any
    string verbatim, so nothing below this layer validates ``verdict`` at
    all."""
    cleaned = verdict.strip().lower()
    if cleaned not in _PROMOTE_VERDICTS:
        raise KnoticaError(
            ErrorCode.INVALID_ARGUMENT,
            f"promote verdict must be one of {'|'.join(_PROMOTE_VERDICTS)}, got {verdict!r}",
            fix=f"Pass verdict as one of: {', '.join(_PROMOTE_VERDICTS)}.",
        )
    return cleaned


def _raise_if_error(result: dict[str, Any]) -> None:
    """Re-raise a returned failure envelope so the adapter renders ``isError=True``.
    ``reanchor``/``detach``/``promote``/``archive`` return typed failures as
    envelopes rather than raising -- mirrors ``tools_notes._raise_if_error``
    exactly (kept local, not imported: that one is private to its own
    module)."""
    error = result.get("error")
    if not isinstance(error, dict):
        return
    raise KnoticaError(
        ErrorCode(error["code"]),
        str(error["message"]),
        fix=str(error["fix"]),
        retryable=bool(error["retryable"]),
    )
