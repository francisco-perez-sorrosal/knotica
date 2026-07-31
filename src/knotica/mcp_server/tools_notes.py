"""``note_capture`` -- the flat, conversational note-capture tool.

One tool, one act: the user says something that *is* a note, and this writes it
under ``notes/<topic>/`` in a single commit. Capture is the most conversational
act in the product, so it stays a flat tool rather than an action on the
``notes`` dispatcher -- an extra selection hop is a direct tax on the one act
that has to feel free.

Two things this module owns beyond delegating to
:func:`~knotica.core.operations.capture_note.capture_note`:

- **``placement``** -- a pre-composed sentence stating where the note landed.
  The model must be able to tell the user that in one line without re-deriving
  it from ``fidelity`` x heading presence, which is exactly where it would
  otherwise invent a location the note does not have. The cost is a small
  string table here; the payoff is that the wrong-location failure mode is
  unreachable.
- **the anchors view** -- the freshly written note is read back through
  :func:`~knotica.core.notes.store.read_note` so the returned anchors carry
  their *resolved* projection status, not just what was recorded.

A degraded anchor is never a failure. It rides back as an ``ANCHOR_DEGRADED``
warning on the success envelope, because telling a model the write failed when
the note is on disk would have it tell the user their thought was lost.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.notes.store import ResolvedNote, read_note
from knotica.core.notes_config import resolve_notes_config
from knotica.core.operations.capture_note import capture_note
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_notes_tools", "render_anchors"]

ToolResult = CallToolResult

_CAPTURE_DESCRIPTION = (
    "Save one personal note (marginalia) against a topic, anchored to the KB "
    "passage that provoked it. Pass the user's words VERBATIM as `note` -- never "
    "paraphrase, summarize, or improve them. Pass the passage you displayed as "
    "`quote`, copied exactly from your own output, and the pages you actually "
    "synthesized it from as `pages`; the server verifies that claim against the "
    "vault and pins the strongest anchor it can prove -- span, page, or topic. "
    "The note is always saved: a weak or unprovable anchor degrades the pin and "
    "rides back as an ANCHOR_DEGRADED warning, never a failure. Writes only "
    "under `notes/<topic>/` -- never a wiki page, never a dataset, never the "
    "loop; a note cannot change what the wiki says or how it scores. `intent` "
    "defaults to `reflection` (private, stays here); `dispute`/`gap`/`question` "
    "only *mark* a note as promotable -- crossing into the KB is a separate "
    "human-gated act. One commit; requires the lock. Idempotent by content: "
    "re-sending the same note for the same quote is a no-op. Call `notes "
    "action=list` (or `action=read` with the returned note_id) to review what "
    "was saved -- notes live outside the wiki corpus, so `search` will never "
    "find them. Call this when the user's message **is** the note -- an "
    'addressed remark ("note this", "worth remembering:", "I\'ve never '
    'bought that argument") or an explicit reflective aside about what they '
    "just read. Never infer a note from the user merely reacting or thinking "
    "aloud, and never write one on their behalf; an unaddressed reaction routes "
    'to an offer ("want me to note that?") instead.'
)

#: Placement sentences keyed by the recorded anchor fidelity. Written out
#: longhand rather than assembled from fragments so each one reads like
#: something a person would say -- that is the whole point of shipping the
#: sentence instead of the parts.
_PLACEMENT_SPAN = (
    "Saved as a {intent}, anchored to the passage you quoted in {page}{heading} ({status})."
)
_PLACEMENT_PAGE = (
    "Saved as a {intent}, anchored to {page} as a whole -- I could not pin it to the exact passage."
)
_PLACEMENT_TOPIC = (
    "Saved as a {intent} against the topic as a whole -- I could not pin it to a particular page."
)
#: The note is on disk but its anchor bullet does not describe any real vault
#: state, so no location claim is honest to make.
_PLACEMENT_UNANCHORED = "Saved as a {intent}; it carries no usable anchor."

_SPAN_FIDELITY = "span"
_PAGE_FIDELITY = "page"


def register_notes_tools(mcp: FastMCP) -> None:
    """Register the flat ``note_capture`` tool on ``mcp``."""

    @mcp.tool(name="note_capture", description=_CAPTURE_DESCRIPTION)
    def note_capture(
        topic: str,
        note: str,
        quote: str = "",
        pages: list[str] = [],  # never mutated; the wire schema needs a literal `default: []`
        intent: str = "reflection",
        tags: list[str] = [],  # never mutated; the wire schema needs a literal `default: []`
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _capture_payload(
                store,
                resolved.path,
                topic,
                note,
                quote=quote,
                pages=tuple(pages),
                intent=intent,
                tags=tuple(tags),
            ),
        )


def _capture_payload(
    store: VaultStore,
    vault_path: Path,
    topic: str,
    note: str,
    *,
    quote: str,
    pages: tuple[str, ...],
    intent: str,
    tags: tuple[str, ...],
) -> dict[str, Any]:
    """Capture the note, then compose the wire envelope around what landed."""
    vcs = VaultVcs(vault_path)
    result = dict(
        capture_note(
            store,
            vault_path,
            vcs,
            topic,
            note,
            quote=quote,
            pages=pages,
            intent=intent,
            tags=tags,
        )
    )
    _raise_if_error(result)

    warnings = result.pop("warnings", [])
    path = str(result["path"])
    note_id = str(result["note_id"])
    cleaned_topic = PurePath(path).parent.name
    notes_config = resolve_notes_config()
    resolved = read_note(
        store,
        vcs,
        cleaned_topic,
        note_id,
        guess_threshold=notes_config.guess_threshold,
        complete_orphan_threshold=notes_config.complete_orphan_threshold,
    )
    payload: dict[str, Any] = {
        "topic": cleaned_topic,
        "note_id": note_id,
        "path": path,
        "intent": resolved.document.intent if resolved is not None else intent,
        "anchors": render_anchors(resolved),
        # Phase 1 pins at most one anchor and the capture path returns no
        # ranked runners-up, so there is never a refinement to offer yet.
        "alternatives": [],
        "placement": _placement(resolved, intent),
        "written": True,
        "duplicate": bool(result["duplicate"]),
        "commit": str(result["commit"]),
    }
    if warnings:
        payload["warnings"] = warnings
    return payload


def render_anchors(resolved: ResolvedNote | None) -> list[dict[str, Any]]:
    """The note's anchors with their resolved projection, for the wire.

    ``fidelity`` is what the anchor bullet *recorded*; ``status`` and
    ``resolved_fidelity`` are what it resolves to against the live vault now --
    kept as separate fields because a consumer conflating them would report a
    stale pin as a current one. Shared with the ``notes`` dispatcher so both
    surfaces render an anchor identically.
    """
    if resolved is None:
        return []
    return [
        {
            "index": index,
            "page": anchor.page,
            "heading": anchor.heading,
            "fidelity": anchor.fidelity,
            "status": projection.status,
            "resolved_fidelity": projection.fidelity,
            "quote": anchor.quote,
            "pinned_at": anchor.pinned_at,
        }
        for index, (anchor, projection) in enumerate(resolved.resolved_anchors)
    ]


def _placement(resolved: ResolvedNote | None, fallback_intent: str) -> str:
    """One sentence saying where the note landed -- composed here, not by the caller."""
    if resolved is None or not resolved.resolved_anchors:
        return _PLACEMENT_UNANCHORED.format(intent=fallback_intent)
    intent = resolved.document.intent
    anchor, projection = resolved.resolved_anchors[0]
    if projection.fidelity is None:
        return _PLACEMENT_UNANCHORED.format(intent=intent)
    page = _page_label(anchor.page)
    if anchor.fidelity == _SPAN_FIDELITY and page:
        heading = f' in the "{anchor.heading}" section' if anchor.heading else ""
        return _PLACEMENT_SPAN.format(
            intent=intent, page=page, heading=heading, status=projection.status
        )
    if anchor.fidelity == _PAGE_FIDELITY and page:
        return _PLACEMENT_PAGE.format(intent=intent, page=page)
    return _PLACEMENT_TOPIC.format(intent=intent)


def _page_label(page: str) -> str:
    """The page's bare stem -- what a person would call it out loud."""
    return PurePath(page).stem if page else ""


def _raise_if_error(result: dict[str, Any]) -> None:
    """Re-raise a returned failure envelope so the adapter renders isError=True.

    ``capture_note`` returns its typed failures as envelopes rather than
    raising, which would otherwise ride back on an ``isError=False`` result and
    let a client mistake a ``TOPIC_NOT_FOUND`` for a saved note.
    """
    error = result.get("error")
    if not isinstance(error, dict):
        return
    raise KnoticaError(
        ErrorCode(error["code"]),
        str(error["message"]),
        fix=str(error["fix"]),
        retryable=bool(error["retryable"]),
    )
