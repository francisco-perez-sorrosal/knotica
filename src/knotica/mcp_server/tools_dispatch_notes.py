"""Operator dispatcher ``notes`` -- registers the ``notes`` MCP tool.

Recall, inspection, the drift review queue, and all four correction/promotion
actions over the personal notes layer: ``reanchor`` re-pins an anchor,
``detach`` records that it no longer points anywhere, ``promote`` crosses the
notes/KB boundary into a curated example or a reported gap, and ``archive``
flips a note's frontmatter status. All four mutate -- exactly one commit per
``apply`` call -- so, unlike ``list``/``read``/``drift``, this dispatcher's
description carries the same read/offer confirmation guard every other
mutating dispatcher in this codebase states.

**The full seven-action design is now registered.** ``list``, ``read``,
``drift``, ``reanchor``, ``detach``, ``promote``, and ``archive`` are all
live; supplying anything else is rejected with ``INVALID_ARGUMENT`` rather
than accepted and quietly ignored -- an action that appears to work and does
nothing is worse than one that says it does not exist.

This module is the thin router: MCP tool registration and dispatch. Per-action
payload construction lives in two cohesion-scoped sibling modules --
:mod:`knotica.mcp_server.tools_dispatch_notes_read` (``list``/``read``/
``drift``, read-only) and :mod:`knotica.mcp_server.tools_dispatch_notes_mutations`
(``reanchor``/``detach``/``promote``/``archive``, mutating). Shared argument
validation and the resolved-anchor status vocabulary (``exact``, ``shifted``,
``fuzzy``, ``orphaned``, ``unanchored``) live in the leaf both sit on,
:mod:`knotica.mcp_server.tools_dispatch_notes_common`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.notes.store import list_notes
from knotica.core.notes_config import resolve_notes_config
from knotica.core.vcs import VaultVcs
from knotica.mcp_server.tools_dispatch_notes_common import (
    _ALL_FILTER,
    _ANCHOR_STATUSES as _ANCHOR_STATUSES,
    _DEFAULT_LIMIT,
    _DEFAULT_MODE,
    _LEAST_SEVERE_ANCHOR_STATUS as _LEAST_SEVERE_ANCHOR_STATUS,
    _MOST_SEVERE_ANCHOR_STATUS as _MOST_SEVERE_ANCHOR_STATUS,
    _validate_action,
    _validate_topic,
)
from knotica.mcp_server.tools_dispatch_notes_mutations import (
    _archive_payload,
    _DEFAULT_PROMOTE_TARGET,
    _DEFAULT_VERDICT,
    _detach_payload,
    _promote_payload,
    _reanchor_payload,
)
from knotica.mcp_server.tools_dispatch_notes_read import (
    _drift_payload,
    _drift_status as _drift_status,
    _list_payload,
    _read_payload,
    _status_counts as _status_counts,
)
from knotica.mcp_server.vault_ctx import with_resolved_vault
from knotica.store import VaultStore

__all__ = ["register_dispatch_notes_tools"]

ToolResult = CallToolResult

_DISPATCHER = "notes"
_ACTIONS = ("list", "read", "drift", "reanchor", "detach", "promote", "archive")

_NOTES_DISPATCH_DESCRIPTION = (
    "Browse and correct the personal notes layer (marginalia) for one topic -- "
    "the notes written with `note_capture` or by hand in Obsidian. `action=list` "
    'is the recall path ("what did I note about this?"): notes live outside the '
    "wiki corpus, so `search` will never find them. Filter `list` by `intent` "
    "(reflection|dispute|gap|question|all) and by resolved anchor `status` "
    "(exact|shifted|fuzzy|orphaned|unanchored|all), and paginate with the opaque cursor from a "
    "prior next_cursor (default 20, max 50 per page); the response carries "
    "intent_counts and status_counts for the whole topic. `action=read` returns "
    "one note in full -- its text and every anchor with the page, the passage "
    "originally pinned, and how that pin resolves against the vault today. "
    "`action=drift` is the review queue: one item per anchor resolving "
    "fuzzy, orphaned, or anchor-invalid (a note that self-healed or never "
    "pointed at anything never appears). Each item carries the note plus a "
    "drift detail -- pinned_quote (the original passage, always present), "
    "live_quote (the current text, when confidently placed), overlap (the "
    "similarity score), alternatives (a scored candidate placement, when one "
    "clears the confidence floor), and rewritten_at/rewritten_by (who last "
    "touched the page, when known); total_count includes anchor-invalid, "
    "invalid_count breaks out how many of those there are. Paginates the "
    "same way as `list`. `action=reanchor` re-pins one anchor, named by its "
    "0-based `anchor` index into the note's append-only history -- pass `page` "
    "and `quote` together to pin explicitly, or leave both empty to accept the "
    "currently-resolved projection (the drift queue's one-click accept). "
    "`action=detach` appends a terminal record saying that anchor no longer "
    "points anywhere; the note itself is kept. Both act only on a *live* anchor "
    "-- one not already superseded or detached -- and reject an out-of-range or "
    "dead index with INVALID_ARGUMENT before any write; reanchor further rejects "
    "a deleted target page with PAGE_NOT_FOUND, whose fix names `notes "
    "action=detach` as the fallback. `action=promote` is the only action here "
    "that can write outside the notes layer: it grounds a caller-supplied "
    "`question` in the note's currently-live anchored pages (there is no "
    "`pages_used` argument -- grounding is always derived server-side, never "
    "caller-supplied) and writes a curated example (`target=trainset`, the "
    "default) or a reported gap (`target=gap`, only for a dispute/gap/question "
    "-intent note; a reflection is rejected). `target=golden` always rejects -- "
    "trainset and golden must stay disjoint, so that promotion runs through "
    "`golden_review` instead. `question` defaults to the note's own text when "
    "the note's `intent` already is `question`. `action=archive` flips a "
    "note's `status` to `archived` and touches nothing else -- no anchor "
    "index, no `## Anchors` change, and it never deletes the file; archiving "
    "an already-archived note is a no-op (`written=false`, `duplicate=true`). "
    "`mode` (default `dry-run`) previews the transition -- returning a "
    "decision envelope (decision_id, summary, context, options, provenance, "
    "reason_required) alongside the preview fields -- without writing; "
    "`mode=apply` performs exactly one commit. `mode=apply` never fires from "
    "detection alone -- only the dashboard operator invokes it, or the user "
    "has explicitly confirmed the change; an unconfirmed detection routes to "
    "`notes action=list` or an offer instead. `list`/`read`/`drift` are "
    "read-only: no commits, no lock. Pass vault to select a configured vault."
)


def register_dispatch_notes_tools(mcp: FastMCP) -> None:
    """Register the ``notes`` operator dispatcher on ``mcp``."""

    @mcp.tool(name="notes", description=_NOTES_DISPATCH_DESCRIPTION)
    def notes(
        action: str,
        topic: str = "",
        note_id: str = "",
        intent: str = _ALL_FILTER,
        status: str = _ALL_FILTER,
        cursor: str = "",
        limit: int = _DEFAULT_LIMIT,
        mode: str = _DEFAULT_MODE,
        anchor: int = 0,
        page: str = "",
        quote: str = "",
        target: str = _DEFAULT_PROMOTE_TARGET,
        question: str = "",
        answer: str = "",
        verdict: str = _DEFAULT_VERDICT,
        vault: str = "",
    ) -> ToolResult:
        return with_resolved_vault(
            vault,
            lambda store, resolved: _dispatch_payload(
                store,
                resolved.path,
                action,
                topic,
                note_id=note_id,
                intent=intent,
                status=status,
                cursor=cursor,
                limit=limit,
                mode=mode,
                anchor=anchor,
                page=page,
                quote=quote,
                target=target,
                question=question,
                answer=answer,
                verdict=verdict,
            ),
        )


def _dispatch_payload(
    store: VaultStore,
    vault_path: Path,
    action: str,
    topic: str,
    *,
    note_id: str,
    intent: str,
    status: str,
    cursor: str,
    limit: int,
    mode: str,
    anchor: int,
    page: str,
    quote: str,
    target: str,
    question: str,
    answer: str,
    verdict: str,
) -> dict[str, Any]:
    cleaned_action = _validate_action(action, _DISPATCHER, _ACTIONS)
    cleaned_topic = _validate_topic(store, topic)
    notes_config = resolve_notes_config()
    vcs = VaultVcs(vault_path)

    if cleaned_action == "reanchor":
        return _reanchor_payload(
            store,
            vault_path,
            vcs,
            cleaned_topic,
            note_id=note_id,
            anchor_index=anchor,
            mode=mode,
            page=page,
            quote=quote,
            guess_threshold=notes_config.guess_threshold,
            complete_orphan_threshold=notes_config.complete_orphan_threshold,
        )
    if cleaned_action == "detach":
        return _detach_payload(
            store,
            vault_path,
            vcs,
            cleaned_topic,
            note_id=note_id,
            anchor_index=anchor,
            mode=mode,
            guess_threshold=notes_config.guess_threshold,
            complete_orphan_threshold=notes_config.complete_orphan_threshold,
        )
    if cleaned_action == "promote":
        return _promote_payload(
            store,
            vault_path,
            vcs,
            cleaned_topic,
            note_id=note_id,
            mode=mode,
            target=target,
            question=question,
            answer=answer,
            verdict=verdict,
            guess_threshold=notes_config.guess_threshold,
            complete_orphan_threshold=notes_config.complete_orphan_threshold,
        )
    if cleaned_action == "archive":
        return _archive_payload(
            store,
            vault_path,
            vcs,
            cleaned_topic,
            note_id=note_id,
            mode=mode,
            guess_threshold=notes_config.guess_threshold,
            complete_orphan_threshold=notes_config.complete_orphan_threshold,
        )

    listing = list_notes(
        store,
        vcs,
        cleaned_topic,
        guess_threshold=notes_config.guess_threshold,
        complete_orphan_threshold=notes_config.complete_orphan_threshold,
    )
    if cleaned_action == "read":
        return _read_payload(cleaned_topic, listing, note_id)
    if cleaned_action == "drift":
        return _drift_payload(
            store,
            vcs,
            cleaned_topic,
            listing,
            guess_threshold=notes_config.guess_threshold,
            complete_orphan_threshold=notes_config.complete_orphan_threshold,
            cursor=cursor,
            limit=limit,
        )
    return _list_payload(
        cleaned_topic, listing, intent=intent, status=status, cursor=cursor, limit=limit
    )
