"""Client-driven source-ingest session lifecycle -- open / publish / abandon.

An approved gap-fill suggestion is ingested by the interactive client across
many turns (store the source, distil pages, wikilink, update the index) --
too many calls, spread over too much human-paced time, to fit inside one
locked ``VaultTransaction``. This module gives that multi-step session an
isolated landing spot: a **private** git worktree, checked out on a
``loop/wip/<topic>/source-<id8>`` branch, that no other reader ever sees.

The session is stateless by construction (dec-004): every function derives
its target purely from ``(topic, suggestion_id)`` (or from the branch name
alone, which already encodes both) plus whatever git itself already knows --
``git worktree list``, branch existence, and (for a resumed session) a diff
of the WIP branch's own tip. No server memory of "which ingest is open"
exists between calls; a crashed process loses nothing recoverable, because
the recoverable state already lives in git.

Lifecycle:

* :func:`open_ingest` -- create (or idempotently resume) the session. Never
  restarts a partial ingest; a resumed session's ``resume`` block tells the
  client what is already committed so it writes only what is missing.
* :func:`publish_ingest` -- the readiness boundary. Atomically renames the
  private WIP branch to its public ``loop/c/<topic>/source-<id8>`` name (via
  :meth:`~knotica.core.vcs.VaultVcs.publish_branch`) and removes the
  worktree. Only after this call is the candidate visible to the loop's
  ``_next_candidate`` scan -- the gate never sees a partial branch.
* :func:`abandon_ingest` -- crash/discard recovery: removes the worktree and
  deletes the WIP branch outright (mirrors the loop's own ``_discard``
  semantics for a candidate that never earns a merge).
* :func:`prune_stale_worktrees` -- best-effort housekeeping for ingests that
  were never published or explicitly abandoned (a crashed client, an
  interrupted session). Mirrors :meth:`knotica.core.loop.LoopRunner.
  _prune_result_branches`'s discipline: staleness-bounded, failures never
  propagate.

Worktrees are created under ``<vault_root>/.knotica/worktrees/`` -- inside
the vault, exactly like the runtime lock file at ``.knotica/locks/``, and
gitignored by the vault template for the same reason: it is throwaway
runtime state, fully recoverable from git itself (``git worktree list``),
never part of the wiki's versioned substrate.

Imports **zero** search-provider machinery -- this module sits on the MCP
cold-start path (a mutating tool reaches it on every call, not just when a
source search runs). ``core.gapfill`` is imported at module level because it
does the same: its own module docstring establishes that it touches the
search-provider layer only lazily, inside the one function that needs it, so
importing its public ``suggestions_path`` here carries no such weight.
"""

import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

from knotica.core.branch_namespaces import (
    CANDIDATE_BRANCH_PREFIX,
    WIP_BRANCH_PREFIX,
    _id8,
    _parse_wip_branch,
    _SOURCE_INFIX,
    candidate_branch_name,
    wip_branch_name,
)
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.gapfill import suggestions_path
from knotica.core.lint import INDEX_PATH
from knotica.core.page import topic_relative_page_name
from knotica.core.records import SuggestionRecord, parse_suggestions_jsonl
from knotica.core.vcs import VaultVcs
from knotica.store import VaultStore

__all__ = [
    "CANDIDATE_BRANCH_PREFIX",
    "WIP_BRANCH_PREFIX",
    "IngestHandle",
    "ResumeState",
    "abandon_ingest",
    "candidate_branch_name",
    "open_ingest",
    "prune_stale_worktrees",
    "publish_ingest",
    "wip_branch_name",
    "worktree_path_for",
]

#: Worktrees live inside the vault, gitignored -- mirrors the ``.knotica/
#: locks/`` runtime-artifact precedent (see module docstring).
_WORKTREE_ROOT = PurePath(".knotica/worktrees")

#: Branch-name conventions an ingest session moves through (private while the
#: client is writing, public once submitted) and the ``source-`` leaf infix are
#: owned by :mod:`knotica.core.branch_namespaces`; imported above and re-exported
#: here so external callers keep resolving ``source_ingest.CANDIDATE_BRANCH_PREFIX``
#: et al.

#: Mirrors ``operations/store_source.py``'s own (private, unexported)
#: constant -- a fixed vault-layout literal, not shared logic, so a small
#: local redeclaration is preferable to reaching into another module's
#: private surface.
_SOURCES_DIR = "sources"

_APPROVED_STATUS = "approved"

#: A WIP worktree/branch older than this with no publish/abandon call is
#: swept by :func:`prune_stale_worktrees` -- crash-recovery hygiene, not a
#: session timeout (an in-progress, human-paced ingest is expected to take
#: minutes to hours, never days).
_STALE_WORKTREE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class ResumeState:
    """What is already committed on a re-opened ingest's WIP branch.

    Best-effort: computed by diffing the WIP branch's tip against the
    canonical vault's *current* ``HEAD``, which is exact as long as the
    default branch has not advanced since this worktree was created (the
    common case for a single-suggestion ingest). Staleness here is safe by
    construction -- ``VaultTransaction``'s idempotency-by-result-state means
    a falsely-reported "not yet written" page costs one extra no-op write,
    never data loss or a duplicate commit.
    """

    source_present: bool
    pages_present: tuple[str, ...]
    index_synced: bool


@dataclass(frozen=True, slots=True)
class IngestHandle:
    """Returned by :func:`open_ingest`; passed back whole to
    :func:`publish_ingest`/:func:`abandon_ingest`.

    ``candidate`` is the WIP branch name -- an opaque identifier from a
    client's perspective, but in fact a pure function of ``(topic,
    suggestion_id)``, round-tripped by every subsequent candidate-scoped
    write exactly as a pagination cursor is round-tripped elsewhere in this
    codebase. ``vault_root`` rides along on the handle (rather than being a
    separate argument every call must re-supply) purely so
    ``publish_ingest``/``abandon_ingest`` are one-argument calls -- it is not
    part of what a client ever sees; the MCP layer re-derives a fresh handle
    per call via :func:`open_ingest`'s idempotent resume, never persists one.
    """

    candidate: str
    state: Literal["created", "resumed"]
    resume: ResumeState
    provenance: dict[str, object]
    vault_root: Path


def worktree_path_for(vault_root: str | Path, topic: str, suggestion_id: str) -> Path:
    """Deterministic worktree location for one ingest session.

    A pure function of ``(vault_root, topic, suggestion_id)`` -- never
    recorded as separate state; recoverable at any time from these same
    three inputs plus ``git worktree list`` (see module docstring).
    """
    return Path(vault_root) / _WORKTREE_ROOT / topic / f"{_SOURCE_INFIX}{_id8(suggestion_id)}"


def open_ingest(
    store: VaultStore,
    root: str | Path,
    topic: str,
    suggestion_id: str,
) -> IngestHandle:
    """Open (or idempotently resume) a source-ingest session.

    Validates the named suggestion is ``approved`` (else raises
    ``SUGGESTION_NOT_APPROVED``), derives the deterministic worktree path and
    WIP branch, and creates the worktree if this is the first call. Re-opening
    an in-progress ingest resolves the *same* worktree/branch and reports
    ``state="resumed"`` with a :class:`ResumeState` computed from what is
    already committed there -- it never restarts an ingest.

    Args:
        store: The **canonical** vault's storage backend (reads
            ``suggestions.jsonl``; the worktree's own store, if one is ever
            needed for a candidate-scoped write, is a separate concern for
            the caller -- see ``VaultTransaction``'s ``work_dir`` contract).
        root: The already-resolved canonical vault root.
        topic: The suggestion's topic.
        suggestion_id: id of an ``approved`` suggestion (from
            ``suggestions_read``).

    Raises:
        KnoticaError: ``SUGGESTION_NOT_FOUND`` / ``SUGGESTION_NOT_APPROVED``
            for the suggestion lookup, raised *before* any worktree is
            touched -- a refused open never leaves partial state behind.

    Note:
        A resumed session does not re-verify that its worktree registration
        is still live (:func:`_resume_state` only needs the branch name, not
        the worktree path, to compute what is already committed). An
        operator-driven ``git worktree prune`` between calls would surface
        only later, as a failure on the next candidate-scoped write -- a
        known, narrow gap tracked for a future step rather than guarded here.
    """
    record = _load_approved_suggestion(store, topic, suggestion_id)
    vault_root = Path(root)
    vcs = VaultVcs(vault_root)
    branch = wip_branch_name(topic, suggestion_id)

    if vcs.branch_exists(branch):
        state: Literal["created", "resumed"] = "resumed"
        resume = _resume_state(vcs, topic, branch)
    else:
        vcs.add_worktree(
            worktree_path_for(vault_root, topic, suggestion_id),
            branch=branch,
            start_ref="HEAD",
        )
        state = "created"
        resume = ResumeState(source_present=False, pages_present=(), index_synced=False)

    return IngestHandle(
        candidate=branch,
        state=state,
        resume=resume,
        provenance=_provenance(record),
        vault_root=vault_root,
    )


def publish_ingest(handle: IngestHandle) -> str:
    """Finalize an ingest session -- the readiness boundary.

    Atomically renames the WIP branch to its public candidate name and
    removes the worktree. Only after this call is the candidate visible to
    the loop's ``_next_candidate`` scan.

    Args:
        handle: The :class:`IngestHandle` from :func:`open_ingest` (or a
            fresh resumed one -- both are equally valid, since a handle is a
            pure function of ``(topic, suggestion_id)``).

    Returns:
        The new public candidate branch name
        (``loop/c/<topic>/source-<id8>``).

    Raises:
        KnoticaError: ``SUGGESTION_NOT_FOUND``-shaped when
            ``handle.candidate`` is not a well-formed WIP branch name.
    """
    topic, id8 = _parse_wip_branch(handle.candidate)
    vcs = VaultVcs(handle.vault_root)
    dest = candidate_branch_name(topic, id8)
    worktree = _find_worktree(vcs, handle.candidate)
    vcs.publish_branch(handle.candidate, dest)
    if worktree is not None:
        vcs.remove_worktree(worktree["path"])
    return dest


def abandon_ingest(handle: IngestHandle) -> None:
    """Discard a crashed or user-abandoned ingest.

    Removes the worktree (when still registered) and force-deletes the WIP
    branch -- the recovery path for an ingest that never reaches
    :func:`publish_ingest`. Mirrors the loop's own ``_discard`` semantics for
    a candidate that never earns a merge: the work is thrown away, not
    quarantined (quarantine is reserved for a *submitted* candidate the gate
    itself refuses).

    Args:
        handle: The :class:`IngestHandle` from :func:`open_ingest`.

    Raises:
        KnoticaError: ``SUGGESTION_NOT_FOUND``-shaped when
            ``handle.candidate`` is not a well-formed WIP branch name.
    """
    _parse_wip_branch(handle.candidate)
    vcs = VaultVcs(handle.vault_root)
    worktree = _find_worktree(vcs, handle.candidate)
    if worktree is not None:
        vcs.remove_worktree(worktree["path"])
    if vcs.branch_exists(handle.candidate):
        vcs.delete_branch(handle.candidate, force=True)


def prune_stale_worktrees(
    root: str | Path,
    *,
    max_age_seconds: float = _STALE_WORKTREE_SECONDS,
) -> list[str]:
    """Best-effort sweep of orphaned WIP worktrees/branches.

    Crash-recovery hygiene for ingests that were never published or
    explicitly abandoned. Mirrors :meth:`knotica.core.loop.LoopRunner.
    _prune_result_branches`'s discipline exactly: one failure anywhere in the
    sweep aborts the rest of it silently rather than raising, because this
    is optional cleanup, never load-bearing for correctness.

    Returns the WIP branch names actually removed (empty on any failure or
    when nothing is stale).
    """
    vcs = VaultVcs(Path(root))
    removed: list[str] = []
    try:
        now = time.time()
        for branch, sha in vcs.list_branch_tips(WIP_BRANCH_PREFIX):
            if now - vcs.commit_timestamp(sha) < max_age_seconds:
                continue
            worktree = _find_worktree(vcs, branch)
            if worktree is not None:
                vcs.remove_worktree(worktree["path"])
            vcs.delete_branch(branch, force=True)
            removed.append(branch)
    except Exception:  # noqa: BLE001 -- housekeeping must never break the caller
        pass
    return removed


def _load_approved_suggestion(
    store: VaultStore, topic: str, suggestion_id: str
) -> SuggestionRecord:
    """Look up ``suggestion_id`` in ``topic`` and require ``status == "approved"``."""
    path = suggestions_path(topic)
    records = parse_suggestions_jsonl(store.read_text(path)) if store.exists(path) else []
    record = next((r for r in records if r.suggestion_id == suggestion_id), None)
    if record is None:
        raise KnoticaError(
            ErrorCode.SUGGESTION_NOT_FOUND,
            f"open_ingest failed because no suggestion {suggestion_id!r} exists in topic {topic!r}.",
        )
    if record.status != _APPROVED_STATUS:
        raise KnoticaError(
            ErrorCode.SUGGESTION_NOT_APPROVED,
            f"open_ingest failed because suggestion {suggestion_id!r} is "
            f"{record.status!r}, not {_APPROVED_STATUS!r}.",
        )
    return record


def _provenance(record: SuggestionRecord) -> dict[str, object]:
    """The suggestion's provenance fields, verbatim, for the client to weave
    into the source it stores and the pages it distils."""
    return {
        "suggestion_id": record.suggestion_id,
        "gap_id": record.gap_id,
        "qa_id": record.qa_id,
        "query_text": record.query_text,
        "source_url": record.candidate.get("url"),
        "source_doi": record.candidate.get("doi"),
    }


def _resume_state(vcs: VaultVcs, topic: str, branch: str) -> ResumeState:
    """Compute what a re-opened session already committed (see :class:`ResumeState`)."""
    changed = vcs.changed_paths("HEAD", branch)
    source_prefix = f"{_SOURCES_DIR}/{topic}/"
    page_prefix = f"{topic}/"
    pages = sorted(
        topic_relative_page_name(topic, path)
        for path in changed
        if path.startswith(page_prefix) and path.endswith(".md")
    )
    return ResumeState(
        source_present=any(path.startswith(source_prefix) for path in changed),
        pages_present=tuple(pages),
        index_synced=INDEX_PATH in changed,
    )


def _find_worktree(vcs: VaultVcs, branch: str) -> dict[str, str] | None:
    """The registered worktree checked out on ``branch``, if any (read-only)."""
    return next((wt for wt in vcs.list_worktrees() if wt.get("branch") == branch), None)
