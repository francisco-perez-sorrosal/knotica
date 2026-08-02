"""Post-merge reconciliation -- notify the drift queue of anchors that just moved.

Answers one question for a topic: *did anything change under a note since it
was last resolved?* It is lazy, git-derived, and strictly read-only -- no
vault lock, no note-file write, no commit, and no :mod:`knotica.core.loop`
call site. It is a notification accelerator, never a correctness dependency:
lazy read-time resolution (:func:`knotica.core.notes.store.list_notes`)
already gives the correct answer on its own if this pass is skipped or never
runs at all.

Only **queue members** are examined -- anchors whose current (HEAD-resolved)
status is ``fuzzy``, ``orphaned``, or ``anchor-invalid``. Anchors resolving
``exact``, ``shifted``, or ``unanchored`` are skipped entirely and produce no
transition. This bound is what keeps a topic-wide pass affordable: resolving
every anchor in a topic against two blobs would double an already
O(notes) x ``git show`` cost that :func:`~knotica.core.notes.store.list_notes`
already pays once.

For each queue-member anchor, the pass resolves the *same* anchor a second
time against the page's content one revision earlier -- the previous commit
that touched the anchor's page, found via
:meth:`knotica.core.vcs.VaultVcs.path_commit_shas` -- and reports both
outcomes as one :class:`Transition`.

**"The previous commit that touched the page" means the previous commit in
HEAD's ancestry -- no branch filtering.** Merges land on the default branch,
so when this runs against a live vault HEAD's ancestry *is* the default-branch
history; run on a feature branch, it honestly reports that branch's state.

**Renames are not followed.** A renamed page means the anchor's recorded path
no longer exists at HEAD, so resolution already reports ``orphaned`` (the
page is gone from the anchor's point of view) -- this is a known, deliberate
limitation, not an oversight. Following the rename with ``git log --follow``
would draw the "before" state from a path the anchor never referenced,
describing history the note never had, which would make the report *worse*
than the plain orphan it already produces.

``anchor-invalid`` is a fixed point of this comparison, not a genuine
before/after pair: the quote was never present in the historical blob the
anchor claims, so nothing about the page caused it, and it carries no rewrite
attribution (``rewritten_at``/``rewritten_by`` are both ``None``) regardless
of how much or how little history the page has.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from knotica.core.notes.anchor import AnchorRecord
from knotica.core.notes.resolve import resolve_anchor
from knotica.core.notes.store import NotesListing, list_notes
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

__all__ = ["Transition", "reconcile_notes"]

#: Anchor statuses that make an anchor a drift-queue member. Bounding the
#: derivation to these three is what keeps a topic-wide pass affordable.
_QUEUE_MEMBER_STATUSES = frozenset({"fuzzy", "orphaned", "anchor-invalid"})

#: How many of a page's most recent touching commits to inspect: the newest
#: (attributing ``rewritten_at``/``rewritten_by``) and the one before it
#: (supplying the "before" content to diff against).
_TOUCHING_COMMITS_TO_INSPECT = 2

#: Fallback length when a commit's subject line is empty, matching the git
#: short-sha convention already used elsewhere in the codebase
#: (``core.branch_scoreboard._short_sha``).
_ABBREVIATED_SHA_LENGTH = 12


@dataclass(frozen=True)
class Transition:
    """One anchor's status change between the previous and newest commit to touch its page."""

    note_id: str
    anchor_index: int
    before: str
    after: str
    rewritten_at: str | None
    rewritten_by: str | None


def reconcile_notes(
    store: VaultStore,
    vcs: VaultVcs,
    topic: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
    listing: NotesListing | None = None,
) -> tuple[Transition, ...]:
    """Report a :class:`Transition` for every drift-queue-member anchor in ``topic``.

    Read-only throughout: resolves each queue member's current status via
    :func:`~knotica.core.notes.store.list_notes` (already computed against HEAD),
    then re-resolves the same anchor against the page's content one commit
    earlier. Never acquires the vault lock, never writes, never commits.

    ``listing`` lets a caller that has **already** resolved the topic hand that
    work in rather than paying for it twice. The drift-queue read path is
    exactly such a caller: it needs the listing to find queue members, then
    calls this. Re-listing internally made a drift open resolve every anchor in
    the topic twice, which measured as the single largest term in its cost --
    at 100 anchors it dominated the per-queue-member work by roughly 4:1.
    ``None`` keeps the self-sufficient behaviour for every other caller.
    """
    if listing is None:
        listing = list_notes(
            store,
            vcs,
            topic,
            guess_threshold=guess_threshold,
            complete_orphan_threshold=complete_orphan_threshold,
        )
    transitions: list[Transition] = []
    for resolved in listing.notes:
        for index, (anchor, projection) in enumerate(resolved.resolved_anchors):
            if projection.status not in _QUEUE_MEMBER_STATUSES:
                continue
            transition = _transition_for_anchor(
                vcs,
                resolved.document.id,
                index,
                anchor,
                projection.status,
                guess_threshold=guess_threshold,
                complete_orphan_threshold=complete_orphan_threshold,
            )
            if transition is not None:
                transitions.append(transition)
    return tuple(transitions)


def _transition_for_anchor(
    vcs: VaultVcs,
    note_id: str,
    anchor_index: int,
    anchor: AnchorRecord,
    after_status: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> Transition | None:
    if after_status == "anchor-invalid":
        return Transition(
            note_id=note_id,
            anchor_index=anchor_index,
            before="anchor-invalid",
            after="anchor-invalid",
            rewritten_at=None,
            rewritten_by=None,
        )
    return _drift_transition(
        vcs,
        note_id,
        anchor_index,
        anchor,
        after_status,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )


def _drift_transition(
    vcs: VaultVcs,
    note_id: str,
    anchor_index: int,
    anchor: AnchorRecord,
    after_status: str,
    *,
    guess_threshold: float,
    complete_orphan_threshold: float,
) -> Transition | None:
    """Resolve ``anchor`` against the page's content one commit earlier than HEAD.

    ``None`` when the page has fewer than two touching commits in its history --
    unreachable for a real ``fuzzy``/``orphaned`` anchor (that status requires the
    page to have changed since the anchor's own historically-valid blob, which is
    itself a second commit distinct from the newest), but a well-formed "nothing
    to report" rather than a crash if it ever occurs.
    """
    touching_commits = vcs.path_commit_shas(anchor.page, limit=_TOUCHING_COMMITS_TO_INSPECT)
    if len(touching_commits) < _TOUCHING_COMMITS_TO_INSPECT:
        return None
    newest_commit, previous_commit = touching_commits[0], touching_commits[1]

    historical_text = vcs.read_file_at(anchor.pinned_at, anchor.page) or ""
    before_projection = resolve_anchor(
        historical_text,
        vcs.read_file_at(previous_commit, anchor.page),
        anchor,
        guess_threshold=guess_threshold,
        complete_orphan_threshold=complete_orphan_threshold,
    )
    subject = vcs.commit_subject(newest_commit)
    return Transition(
        note_id=note_id,
        anchor_index=anchor_index,
        before=before_projection.status,
        after=after_status,
        rewritten_at=_iso_timestamp(vcs.commit_timestamp(newest_commit)),
        rewritten_by=subject if subject else newest_commit[:_ABBREVIATED_SHA_LENGTH],
    )


def _iso_timestamp(unix_seconds: int) -> str:
    return datetime.fromtimestamp(unix_seconds, tz=UTC).isoformat()
