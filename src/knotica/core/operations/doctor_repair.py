"""Path-scoped worktree repair — the mutating half of ``knotica doctor repair``.

Dry-run lists dirty porcelain entries. Apply restores explicitly selected paths
to ``HEAD`` through :func:`~knotica.core.transaction.restore_worktree_paths`
(sole-writer seam). Never ``git restore .`` — unselected paths (including
concurrent Obsidian edits) stay untouched.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.transaction import restore_worktree_paths
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = ["doctor_repair"]


def doctor_repair(
    store: VaultStore,
    vault_root: str | Path,
    *,
    apply: bool,
    paths: tuple[str, ...] = (),
    all_tracked: bool = False,
    delete_untracked: bool = False,
) -> dict[str, Any]:
    """Plan or apply a path-scoped dirty-tree repair.

    Args:
        store: Vault store (unused for I/O; kept for operation signature parity).
        vault_root: Resolved vault root.
        apply: ``False`` = dry-run inventory; ``True`` = restore selected paths.
        paths: Explicit vault-relative paths to restore (required for apply
            unless ``all_tracked``).
        all_tracked: When applying, select every *tracked* dirty path.
        delete_untracked: Allow untracked paths in the selection (deleted via
            rollback_paths' created-since-ref branch). Default refuses them.
    """
    del store  # signature parity with other operations; repair uses VaultVcs.
    root = Path(vault_root)
    try:
        vcs = VaultVcs(root)
        dirty = vcs.list_dirty_entries()
    except (GitError, NotADirectoryError) as error:
        return err(
            ErrorCode.GIT_ERROR,
            f"doctor repair failed because git status is unavailable: {error}",
        )

    if not apply:
        return ok(
            {
                "mode": "dry-run",
                "dirty_count": len(dirty),
                "entries": dirty,
                "tracked_paths": [str(e["path"]) for e in dirty if e["tracked"]],
                "untracked_paths": [str(e["path"]) for e in dirty if e["untracked"]],
                "message": (
                    "Dry-run only — no files restored. Pass --apply with --paths "
                    "or --all-tracked to restore."
                ),
            }
        )

    try:
        selected = _resolve_apply_paths(
            dirty,
            paths=paths,
            all_tracked=all_tracked,
            delete_untracked=delete_untracked,
        )
    except KnoticaError as error:
        return error.envelope()
    except ValueError as error:
        return err(ErrorCode.INVALID_CURSOR, str(error), fix=str(error))

    if not selected:
        return ok(
            {
                "mode": "apply",
                "restored": [],
                "dirty_count": len(dirty),
                "entries": dirty,
                "message": "Nothing to restore — work tree is clean for the selection.",
            }
        )

    try:
        restored = restore_worktree_paths(root, selected)
    except KnoticaError as error:
        return error.envelope()
    except ValueError as error:
        return err(ErrorCode.INVALID_CURSOR, str(error))

    return ok(
        {
            "mode": "apply",
            "restored": restored,
            "dirty_count": len(dirty),
            "entries": dirty,
            "message": f"Restored {len(restored)} path(s) to HEAD.",
        }
    )


def _resolve_apply_paths(
    dirty: list[dict[str, str | bool]],
    *,
    paths: tuple[str, ...],
    all_tracked: bool,
    delete_untracked: bool,
) -> list[str]:
    dirty_by_path = {str(entry["path"]): entry for entry in dirty}
    if all_tracked and paths:
        raise KnoticaError(
            ErrorCode.INVALID_CURSOR,
            "doctor repair failed because --all-tracked and --paths cannot be combined",
            fix="Pass either --all-tracked or an explicit --paths list.",
        )
    if all_tracked:
        selected = [str(entry["path"]) for entry in dirty if entry["tracked"]]
        if not selected:
            return []
        return selected
    if not paths:
        raise KnoticaError(
            ErrorCode.INVALID_CURSOR,
            "doctor repair --apply requires --paths PATH... or --all-tracked",
            fix=(
                "Run `knotica doctor repair --dry-run`, then "
                "`knotica doctor repair --apply --paths <file>...` "
                "or `--all-tracked` for every tracked dirty path."
            ),
        )

    selected: list[str] = []
    for raw in paths:
        path = PurePath(raw).as_posix().lstrip("/")
        entry = dirty_by_path.get(path)
        if entry is None:
            raise KnoticaError(
                ErrorCode.INVALID_CURSOR,
                f"doctor repair failed because {path!r} is not currently dirty",
                fix="Re-run --dry-run; only dirty paths can be restored.",
            )
        if entry["untracked"] and not delete_untracked:
            raise KnoticaError(
                ErrorCode.INVALID_CURSOR,
                f"doctor repair failed because {path!r} is untracked",
                fix=(
                    "Omit untracked paths, or pass --delete-untracked to remove them "
                    "(destructive for those paths only)."
                ),
            )
        selected.append(path)
    return selected
