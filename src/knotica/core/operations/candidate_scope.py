"""Resolve an optional candidate-ingest handle to its worktree write scope.

A mutating operation (``store_source``, ``write_page``) may accept an optional
``candidate`` handle -- the WIP branch name of an open source-ingest session
(see :mod:`knotica.core.source_ingest`). When empty, the operation writes to the
default branch exactly as before -- byte-identical to omitting the argument.
When set, every read *and* the commit itself must target the ingest session's
private git worktree, never the canonical vault tree the watcher observes: so
the operation swaps its store for one rooted at the worktree and passes that
worktree as the transaction's ``work_dir``.

The store and ``work_dir`` returned here are consistent by construction -- the
store is built rooted at the very path returned as ``work_dir`` -- so the
worktree-transaction contract ("``store`` must already be scoped to
``work_dir``; the transaction does not re-root it") cannot be violated by a
caller that goes through this seam. A canonical-scoped store paired with a
worktree ``work_dir`` would write stray bytes into the canonical tree; this
helper makes that mismatch unrepresentable.
"""

from pathlib import Path, PurePath

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore, VaultStore

__all__ = ["resolve_candidate_scope"]


def resolve_candidate_scope(
    store: VaultStore, vault_root: str | PurePath, candidate: str
) -> tuple[VaultStore, Path | None]:
    """Return the ``(store, work_dir)`` a mutating op should use for ``candidate``.

    Args:
        store: The canonical vault store the adapter built (used unchanged when
            ``candidate`` is empty).
        vault_root: The already-resolved canonical vault root.
        candidate: Empty for a normal default-branch write, or the WIP branch
            name of an open ingest session (from ``source_ingest_open``).

    Returns:
        ``(store, None)`` for the default-branch path -- byte-identical to
        omitting the argument. For a non-empty handle, a store rooted at the
        session's registered worktree plus that worktree path (the
        transaction's ``work_dir``).

    Raises:
        KnoticaError: ``SUGGESTION_NOT_FOUND`` when ``candidate`` does not name
            an open ingest session's worktree -- the caller must open one first.
    """
    if not candidate:
        return store, None
    worktree_path = _open_worktree_path(vault_root, candidate)
    return LocalFSStore(worktree_path), worktree_path


def _open_worktree_path(vault_root: str | PurePath, candidate: str) -> Path:
    """The working directory of the open ingest session checked out on ``candidate``.

    Matches ``candidate`` against the git worktree registry (the same lookup
    ``source_ingest`` uses to find a session's worktree). A malformed handle and
    a session whose worktree was never opened or already published both fail the
    match identically -- both mean "no open session with this handle".
    """
    for worktree in VaultVcs(vault_root).list_worktrees():
        if worktree.get("branch") == candidate:
            return Path(worktree["path"])
    raise KnoticaError(
        ErrorCode.SUGGESTION_NOT_FOUND,
        f"write to candidate {candidate!r} failed because no open ingest session has that handle.",
        fix="Call source_ingest_open first to obtain a candidate handle, then pass "
        "it to store_source / write_page.",
    )
