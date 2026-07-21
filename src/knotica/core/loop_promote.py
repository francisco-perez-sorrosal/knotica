"""Promote a loop eval result branch onto the vault default branch.

Loop runner keep-path fetches the eval clone tip onto ``loop/r/<shortsha>`` then
merges into default. This module exposes that merge as an explicit human gate
(MCP / dashboard) — same safety model as :mod:`knotica.core.compile_promote`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core.errors import ErrorCode, err, ok
from knotica.core.lock import DEFAULT_ACQUIRE_TIMEOUT_SECONDS, LockBusyError, vault_lock
from knotica.core.loop import DEFAULT_BRANCH_PREFIX, RESULT_BRANCH_PREFIX
from knotica.core.loop_state import loop_state_path
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = ["loop_promote", "loop_result_branch_name", "resolve_loop_promote_branch"]


def loop_result_branch_name(sha: str) -> str:
    """Return ``loop/r/<shortsha>`` for a full or short SHA."""
    cleaned = sha.strip()
    return f"{RESULT_BRANCH_PREFIX}{cleaned[:12]}"


def resolve_loop_promote_branch(branch: str, *, tip_sha: str | None = None) -> str:
    """Map ``loop/c/*`` to ``loop/r/<sha>`` when a tip SHA is known."""
    cleaned = branch.strip()
    if cleaned.startswith(RESULT_BRANCH_PREFIX):
        return cleaned
    if cleaned.startswith(DEFAULT_BRANCH_PREFIX):
        if not tip_sha:
            raise ValueError("loop/c branch requires tip SHA to resolve loop/r target")
        return loop_result_branch_name(tip_sha)
    raise ValueError(f"not a loop promote branch: {branch!r}")


def loop_promote(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    branch: str,
    *,
    apply: bool,
    lock_timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Plan or apply merging a loop result branch into the default branch.

    Accepts ``loop/r/<shortsha>`` directly, or ``loop/c/*`` when the matching
    ``loop/r/<shortsha>`` exists locally (tip SHA read from git).
    """
    del store
    root = Path(vault_root)
    cleaned_topic = topic.strip().strip("/")
    if not cleaned_topic or "/" in cleaned_topic:
        return err(
            ErrorCode.TOPIC_NOT_FOUND, f"loop promote failed because topic {topic!r} is invalid"
        )

    cleaned_branch = branch.strip()
    if not (
        cleaned_branch.startswith(RESULT_BRANCH_PREFIX)
        or cleaned_branch.startswith(DEFAULT_BRANCH_PREFIX)
    ):
        return err(
            ErrorCode.INVALID_ARGUMENT,
            (
                f"loop promote failed because branch {branch!r} must start with "
                f"{RESULT_BRANCH_PREFIX!r} or {DEFAULT_BRANCH_PREFIX!r}"
            ),
            fix="Pass loop/r/<shortsha> from branch_scoreboard, or a loop/c/* tip with a fetched loop/r branch.",
        )

    try:
        vcs = VaultVcs(root)
        default = vcs.default_branch()
        dirty = vcs.is_dirty()
        current = vcs.current_branch()
    except (GitError, NotADirectoryError) as error:
        return err(ErrorCode.GIT_ERROR, f"loop promote failed because git is unavailable: {error}")

    if dirty:
        return err(
            ErrorCode.GIT_ERROR,
            "loop promote failed because the vault worktree is dirty",
            fix="Commit changes or run `knotica doctor repair`, then retry.",
        )

    target_branch = cleaned_branch
    if cleaned_branch.startswith(DEFAULT_BRANCH_PREFIX):
        if not vcs.branch_exists(cleaned_branch):
            return err(
                ErrorCode.GIT_ERROR,
                f"loop promote failed because candidate branch {cleaned_branch!r} does not exist",
            )
        tips = dict(vcs.list_branch_tips(DEFAULT_BRANCH_PREFIX))
        tip_sha = tips.get(cleaned_branch)
        if not tip_sha:
            return err(ErrorCode.GIT_ERROR, f"could not resolve tip SHA for {cleaned_branch!r}")
        target_branch = loop_result_branch_name(tip_sha)

    if not vcs.branch_exists(target_branch):
        return err(
            ErrorCode.GIT_ERROR,
            (f"loop promote failed because result branch {target_branch!r} does not exist locally"),
            fix=(
                "Run loop_runner once so the eval clone tip is fetched onto loop/r/<sha>, "
                "or pass an existing loop/r branch from branch_scoreboard."
            ),
        )

    if not apply:
        return ok(
            {
                "mode": "dry-run",
                "merged": False,
                "branch": target_branch,
                "candidate_branch": cleaned_branch if cleaned_branch != target_branch else None,
                "into": default,
                "current_branch": current,
                "commit_sha": None,
                "message": (
                    f"Preview — would merge {target_branch} into {default}. "
                    "Use Apply merge in the dashboard or mode='apply' after review."
                ),
            }
        )

    lock = vault_lock(vcs.root, timeout=lock_timeout)
    try:
        lock.__enter__()
    except LockBusyError as error:
        return err(ErrorCode.LOCK_BUSY, str(error))

    try:
        try:
            if current != default:
                vcs.checkout_branch(default)
            commit_sha = _merge_loop_branch(vcs, target_branch, cleaned_topic)
        except GitError as error:
            return err(
                ErrorCode.GIT_ERROR,
                f"loop promote failed because git merge reported: {error}",
                fix="Resolve merge conflicts manually or delete the loop/r branch and re-run eval.",
            )
    finally:
        lock.__exit__(None, None, None)

    return ok(
        {
            "mode": "apply",
            "merged": True,
            "branch": target_branch,
            "candidate_branch": cleaned_branch if cleaned_branch != target_branch else None,
            "into": default,
            "current_branch": default,
            "commit_sha": commit_sha,
            "message": f"Merged {target_branch} into {default}.",
        }
    )


def _merge_loop_branch(vcs: VaultVcs, branch: str, topic: str) -> str:
    """Merge a loop result branch, preferring default-branch audit paths on conflict."""
    allowed_ours = {"log.md", loop_state_path(topic)}
    try:
        return vcs.merge_branch(branch, ff_only=False)
    except GitError:
        if not vcs.is_merge_in_progress():
            raise
        unmerged = vcs.unmerged_paths()
        if not unmerged or not all(path in allowed_ours for path in unmerged):
            vcs.abort_merge()
            raise
        for path in unmerged:
            vcs.checkout_merge_side(path, "ours")
        return vcs.continue_merge()
