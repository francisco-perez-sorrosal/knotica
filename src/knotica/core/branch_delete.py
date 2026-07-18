"""Delete local compile result branches without wiping compile-state history.

Removes reviewed ``compile/<topic>/…`` git branches after promote or when they
did not beat the per-topic baseline. ``compile-state.json`` scalars and stage
history stay on disk; only the active ``branch`` pointer is cleared when it
pointed at the deleted tip. Refuses the default branch and the checked-out HEAD.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core.compile_promote import compile_branch_prefix
from knotica.core.compile_state import (
    find_compile_history,
    mark_compile_branch_deleted,
    read_compile_state,
    write_compile_state,
)
from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = ["branch_delete"]


def branch_delete(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    branch: str,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Plan or apply deleting a local ``compile/<topic>/…`` branch.

    Args:
        store: Vault store (used when compile-state pointer must be cleared).
        vault_root: Resolved vault root.
        topic: Topic slug — ``branch`` must start with ``compile/<topic>/``.
        branch: Local compile branch to delete.
        apply: ``False`` = dry-run preview; ``True`` = ``git branch -D``.
    """
    root = Path(vault_root)
    try:
        required_prefix = compile_branch_prefix(topic)
    except KnoticaError as error:
        return error.envelope()

    cleaned_branch = branch.strip()
    if not cleaned_branch.startswith(required_prefix) or cleaned_branch == required_prefix.rstrip(
        "/"
    ):
        return err(
            ErrorCode.INVALID_CURSOR,
            (
                f"branch delete failed because branch {branch!r} must start with "
                f"{required_prefix!r} and include a commit suffix"
            ),
            fix=(
                "Pass a compile result branch from branch_scoreboard "
                f"(e.g. {required_prefix}<shortsha>)."
            ),
        )

    try:
        vcs = VaultVcs(root)
        default = vcs.default_branch()
        current = vcs.current_branch()
        branch_exists = vcs.branch_exists(cleaned_branch)
    except (GitError, NotADirectoryError) as error:
        return err(
            ErrorCode.GIT_ERROR,
            f"branch delete failed because git is unavailable: {error}",
        )

    if cleaned_branch == default:
        return err(
            ErrorCode.INVALID_CURSOR,
            f"branch delete failed because {cleaned_branch!r} is the vault default branch",
            fix="Only compile/<topic>/… result branches may be deleted.",
        )

    if current == cleaned_branch:
        return err(
            ErrorCode.INVALID_CURSOR,
            (f"branch delete failed because {cleaned_branch!r} is the current checked-out branch"),
            fix=f"Checkout {default!r} (or another branch) before deleting this compile branch.",
        )

    if not branch_exists:
        return err(
            ErrorCode.GIT_ERROR,
            f"branch delete failed because branch {cleaned_branch!r} does not exist locally",
            fix="Refresh branch_scoreboard — the branch may already have been deleted.",
        )

    cleaned_topic = topic.strip().strip("/")
    compile_state = read_compile_state(store, cleaned_topic)
    clears_active = compile_state is not None and compile_state.branch == cleaned_branch

    if not apply:
        pointer_note = (
            " Would clear the active compile branch pointer in compile-state.json."
            if clears_active
            else ""
        )
        return ok(
            {
                "mode": "dry-run",
                "deleted": False,
                "topic": cleaned_topic,
                "branch": cleaned_branch,
                "compile_state_cleared": False,
                "message": (
                    f"Preview — would delete local branch {cleaned_branch!r} "
                    f"(compile history in compile-state.json is preserved).{pointer_note} "
                    "Use Apply delete in the dashboard or mode='apply' (MCP) after review."
                ),
            }
        )

    head_sha: str | None = None
    base_sha: str | None = None
    merge_sha: str | None = None
    try:
        head_sha = vcs.ref_sha(cleaned_branch)
        default_sha = vcs.ref_sha(default)
        if vcs.is_ancestor(head_sha, default_sha) and head_sha != default_sha:
            merge_sha = vcs.find_merge_commit_for_branch(cleaned_branch)
            if merge_sha is not None:
                parents = vcs.merge_parents(merge_sha)
                if parents is not None:
                    base_sha, head_sha = parents
            if base_sha is None:
                base_sha = default_sha
    except GitError:
        pass

    history = find_compile_history(compile_state, branch=cleaned_branch)
    if history is not None:
        head_sha = head_sha or history.head_sha
        base_sha = base_sha or history.base_sha
        merge_sha = merge_sha or history.merge_sha

    try:
        vcs.delete_branch(cleaned_branch, force=True)
    except GitError as error:
        return err(
            ErrorCode.GIT_ERROR,
            f"branch delete failed because git reported: {error}",
        )

    mark_compile_branch_deleted(
        store,
        root,
        cleaned_topic,
        cleaned_branch,
        head_sha=head_sha,
        base_sha=base_sha,
        merge_sha=merge_sha,
    )

    compile_state_cleared = False
    if clears_active:
        refreshed = read_compile_state(store, cleaned_topic)
        if refreshed is not None:
            write_compile_state(
                store,
                root,
                refreshed.model_copy(update={"branch": None}),
                title="clear compile branch pointer",
            )
            compile_state_cleared = True

    return ok(
        {
            "mode": "apply",
            "deleted": True,
            "topic": cleaned_topic,
            "branch": cleaned_branch,
            "compile_state_cleared": compile_state_cleared,
            "message": (
                f"Deleted local branch {cleaned_branch!r}. "
                "Compile metrics and history in compile-state.json were kept."
            ),
        }
    )
