"""Payload construction for the ``notes`` dispatcher's mutating actions --
``reanchor`` and ``detach`` (dry-run/apply mode pair, one commit per
``apply``).

``mode=dry-run`` (the schema default) previews and returns the uniform
decision envelope every mutating gate in this codebase renders
(``suggestions_review._dry_run`` is the precedent); ``mode=apply`` performs
exactly one commit via the already-tested
:mod:`knotica.core.operations.reanchor_note` functions -- exposed, not
reimplemented. Core has no plan-only entry point, so the dry-run path
re-derives the same liveness gate read-only (:func:`_live_anchor` below)
instead of routing the preview through the write path.

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
from knotica.core.notes.anchor import AnchorRecord, live_anchors
from knotica.core.notes.resolve import Projection
from knotica.core.notes.store import NotesListing, ResolvedNote, list_notes
from knotica.core.operations.reanchor_note import detach, reanchor
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.tools_dispatch_notes_common import _drift_live_quote, _validate_mode
from knotica.store import VaultStore

_REANCHOR_ACTION = "reanchor"
_DETACH_ACTION = "detach"

#: Identical wording to `core.operations.reanchor_note.reanchor`'s page/quote
#: pairing gate, so a caller sees the same rejection previewing or applying.
_PAGE_QUOTE_PAIRING_MESSAGE = (
    "reanchor failed because page and quote must be supplied together, or both "
    "left empty to accept the currently-resolved projection."
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
        anchor_index=anchor_index,
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
        anchor_index=anchor_index,
        summary=f"Detach note {note_id}'s anchor {anchor_index}",
        preview=(
            "Detach -> appends a terminal record saying this anchor no longer "
            "points anywhere; the note itself is kept."
        ),
        context=_anchor_context(store, note_id, anchor_index, anchor, projection),
        provenance=_anchor_provenance(anchor),
    )


def _apply_action(
    topic: str, action: str, mutate: Callable[[], dict[str, object]]
) -> dict[str, Any]:
    """Run a `reanchor`/`detach` call, re-raise its failure envelope (if any),
    and wrap a success into the dispatcher's ``apply`` envelope."""
    result = dict(mutate())
    _raise_if_error(result)
    return {"mode": "apply", "topic": topic, "action": action, "committed": True, **result}


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
    listing = list_notes(
        store,
        vcs,
        topic,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    note = _find_note(topic, listing, note_id, op_label=op_label)
    return _live_anchor(note, anchor_index, op_label=op_label)


def _decision_envelope(
    *,
    action: str,
    topic: str,
    note_id: str,
    anchor_index: int,
    summary: str,
    preview: str,
    context: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """The uniform dry-run shape every mutating gate renders (see
    `suggestions_review._dry_run`), adapted for one anchor. Neither `reanchor`
    nor `detach` takes a `reason` argument, so `reason_required` is always
    `False` -- there is no field to require one for."""
    return {
        "mode": "dry-run",
        "topic": topic,
        "note_id": note_id,
        "action": action,
        "decision_id": f"{note_id}:{anchor_index}",
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


def _raise_if_error(result: dict[str, Any]) -> None:
    """Re-raise a returned failure envelope so the adapter renders ``isError=True``.
    ``reanchor``/``detach`` return typed failures as envelopes rather than
    raising -- mirrors ``tools_notes._raise_if_error`` exactly (kept local,
    not imported: that one is private to its own module)."""
    error = result.get("error")
    if not isinstance(error, dict):
        return
    raise KnoticaError(
        ErrorCode(error["code"]),
        str(error["message"]),
        fix=str(error["fix"]),
        retryable=bool(error["retryable"]),
    )
