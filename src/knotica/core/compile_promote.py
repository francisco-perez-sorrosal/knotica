"""Promote a reviewed compile branch onto the live vault default branch.

Compile runs on a clone and returns ``compile/<topic>/<sha>`` for human review.
This module is the explicit human gate: merge that branch into ``main``/``master``
under the vault flock — never inside :func:`~knotica.core.compile_run.run_compile`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knotica.core.branch_namespaces import compile_branch_prefix
from knotica.core.compiled import load_compiled
from knotica.core.compile_state import (
    compile_state_path,
    find_compile_history,
    read_compile_state,
    record_compile_promoted,
)
from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.lock import DEFAULT_ACQUIRE_TIMEOUT_SECONDS, LockBusyError, vault_lock
from knotica.core.metrics import (
    COMPILE_METRICS_HARNESS_VERSION,
    append_metrics_record,
    build_compile_metrics_record,
    next_metrics_generation,
)
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

__all__ = ["compile_branch_prefix", "compile_promote"]


def compile_promote(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    branch: str,
    *,
    apply: bool,
    lock_timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Plan or apply merging ``branch`` into the vault default branch.

    Args:
        store: Vault store (signature parity; promote uses git only).
        vault_root: Resolved vault root.
        topic: Topic slug — ``branch`` must start with ``compile/<topic>/``.
        branch: Local compile result branch (e.g. ``compile/agentic-systems/3aedba7d34b3``).
        apply: ``False`` = dry-run plan; ``True`` = checkout default and ``--no-ff`` merge.
        lock_timeout: Seconds to wait for the vault flock when applying.
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
                f"compile promote failed because branch {branch!r} must start with "
                f"{required_prefix!r} and include a commit suffix"
            ),
            fix=(
                "Pass the branch name from compile_run / compile_status "
                f"(e.g. {required_prefix}<shortsha>)."
            ),
        )

    try:
        vcs = VaultVcs(root)
        default = vcs.default_branch()
        branch_exists = vcs.branch_exists(cleaned_branch)
        dirty = vcs.is_dirty()
        current = vcs.current_branch()
    except (GitError, NotADirectoryError) as error:
        return err(
            ErrorCode.GIT_ERROR,
            f"compile promote failed because git is unavailable: {error}",
        )

    if not branch_exists:
        return err(
            ErrorCode.GIT_ERROR,
            f"compile promote failed because branch {cleaned_branch!r} does not exist locally",
            fix="Re-run compile_run or fetch the branch onto this vault before promoting.",
        )

    if dirty:
        return err(
            ErrorCode.GIT_ERROR,
            "compile promote failed because the vault worktree is dirty",
            fix="Commit changes or run `knotica doctor repair` on scoped dirty paths, then retry.",
        )

    if not apply:
        return ok(
            {
                "mode": "dry-run",
                "merged": False,
                "branch": cleaned_branch,
                "into": default,
                "current_branch": current,
                "commit_sha": None,
                "message": (
                    f"Preview — would merge {cleaned_branch} into {default} with --no-ff. "
                    "Use Apply merge in the dashboard or mode='apply' (MCP) after review."
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
            commit_sha = _merge_compile_branch(
                vcs, cleaned_branch, cleaned_topic=topic.strip().strip("/")
            )
        except GitError as error:
            return err(
                ErrorCode.GIT_ERROR,
                f"compile promote failed because git merge reported: {error}",
                fix=(
                    "Resolve merge conflicts manually in the vault, or delete the branch and "
                    "re-run compile_run."
                ),
            )
    finally:
        lock.__exit__(None, None, None)

    cleaned_topic = topic.strip().strip("/")
    parents = vcs.merge_parents(commit_sha)
    if parents is not None:
        base_sha, head_sha = parents
    else:
        base_sha = vcs.ref_sha(default)
        head_sha = vcs.ref_sha(cleaned_branch)
    record_compile_promoted(
        store,
        root,
        cleaned_topic,
        cleaned_branch,
        merge_sha=commit_sha,
        base_sha=base_sha,
        head_sha=head_sha,
    )
    _append_compile_metrics_on_promote(
        store,
        root,
        cleaned_topic,
        cleaned_branch,
        merge_sha=commit_sha,
    )

    return ok(
        {
            "mode": "apply",
            "merged": True,
            "branch": cleaned_branch,
            "into": default,
            "current_branch": default,
            "commit_sha": commit_sha,
            "message": f"Merged {cleaned_branch} into {default}. Ask again to use the compiled engine.",
        }
    )


def _append_compile_metrics_on_promote(
    store: VaultStore,
    vault_root: Path,
    topic: str,
    branch: str,
    *,
    merge_sha: str,
) -> None:
    """Record the promoted compile scalar in metrics.jsonl for Loop charting."""
    state = read_compile_state(store, topic)
    if state is None:
        return

    entry = find_compile_history(state, branch=branch)
    scalar = entry.scalar_after if entry is not None else state.scalar_after
    if scalar is None:
        return

    artifact = load_compiled(store, topic)
    generation = next_metrics_generation(store, topic)
    record = build_compile_metrics_record(
        topic,
        scalar,
        merge_sha=merge_sha,
        generation=generation,
        n_examples=artifact.golden_n if artifact is not None else 0,
        harness_version=(
            artifact.harness_version if artifact is not None else COMPILE_METRICS_HARNESS_VERSION
        ),
    )
    append_metrics_record(
        store,
        vault_root,
        topic,
        record,
        operation="compile",
        title=f"compile generation {generation}",
    )


def _merge_compile_branch(vcs: VaultVcs, branch: str, *, cleaned_topic: str) -> str:
    """Merge a compile branch, resolving known audit-path conflicts on the default branch."""
    allowed_ours = {"log.md", compile_state_path(cleaned_topic)}
    try:
        return vcs.merge_branch(branch, no_ff=True)
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
