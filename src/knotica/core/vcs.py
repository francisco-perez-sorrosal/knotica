"""Path-scoped git operations for the vault (subprocess-based, stdlib only).

``VaultVcs`` is the core's only git surface. Its API is deliberately incapable
of touching files it was not told about: every mutating operation takes an
explicit list of vault-relative paths, and there is **no** add-all, no
``reset --hard``, and no history-destroying primitive. This is structural
protection for the vault's foreign content -- a concurrent Obsidian edit to an
unrelated file can never be swept into a knotica commit or destroyed by a
knotica rollback.

Consumers of the **mutating** surface (``commit_paths``/``rollback_paths``):
``core.transaction`` only (the single mutation path). The read/checkout
``clone_to`` is additionally called by the eval harness to build an immutable
frozen-corpus clone -- it creates a fresh tree elsewhere and never touches the
live vault, so it stays outside the single-mutation-path invariant. Commit
messages are composed by the caller (the frozen grammar lives in the vault
constitution and ``core.records``); this module treats them as opaque strings.

Failures surface as :class:`GitError`, which the transaction layer maps to the
user-facing git-failure contract. Transient contention from another git
process racing this one -- an ``index.lock`` collision, or a scoped commit
landing in the narrow window while a concurrent merge holds ``MERGE_HEAD`` --
is retried a few times before giving up.
"""

import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path, PurePath

#: Seconds to wait before retrying a git command that lost a transient race
#: against another concurrent git process on the same working tree.
INDEX_LOCK_RETRY_WAIT_SECONDS = 2.0
#: Number of retries (beyond the first attempt) on transient git contention.
INDEX_LOCK_RETRIES = 3

#: Error substrings that mark a *transient* collision with another git
#: process on the same working tree -- self-clearing once that process's own
#: command finishes, so a short retry (not a real failure) is the correct
#: response. Beyond the literal ``index.lock`` file, two ``LoopRunner``
#: passes can genuinely overlap: one's scoped ``git commit`` can land in the
#: narrow window while another's ``git merge`` still holds ``MERGE_HEAD``, or
#: while its own ref update is in flight. A *real*, non-transient conflict
#: (actual overlapping content) still surfaces as a ``GitError`` once retries
#: are exhausted -- this only smooths the timing race, never masks a genuine
#: failure.
_TRANSIENT_GIT_RACE_SIGNATURES = (
    "index.lock",
    "unable to write index",
    "cannot do a partial commit during a merge",
    "cannot lock ref",
)

#: Committer identity stamped locally on an eval clone. A fresh ``git clone``
#: inherits no committer identity from the source's *local* config, so on a
#: machine with no global git identity a later commit would fail with
#: "Author identity unknown". Stamping a fixed local identity makes eval commits
#: deterministic and independent of ambient global git config. The ``.invalid``
#: TLD (RFC 2606) guarantees the address never resolves to a real mailbox.
_CLONE_COMMITTER_NAME = "knotica eval harness"
_CLONE_COMMITTER_EMAIL = "eval@knotica.invalid"

_GIT_COMMAND_TIMEOUT_SECONDS = 60.0


class GitError(Exception):
    """A git command failed; carries the command and its combined output.

    The mutation transaction maps this to the user-facing git-failure result.
    """

    def __init__(self, message: str, *, command: Sequence[str] = (), output: str = "") -> None:
        super().__init__(message)
        self.command = tuple(command)
        self.output = output


class VaultVcs:
    """Path-scoped git wrapper bound to one vault repository.

    Args:
        root: The vault root (must be an existing directory; expected to be a
            git work tree -- commands raise :class:`GitError` otherwise).
    """

    def __init__(self, root: str | PurePath) -> None:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(f"Vault root is not an existing directory: {root}")
        self._root = resolved

    @property
    def root(self) -> Path:
        """The resolved vault root this wrapper operates on (read-only)."""
        return self._root

    def head_sha(self) -> str:
        """Return the full SHA of the current ``HEAD`` commit."""
        result = self._run(["rev-parse", "HEAD"])
        return result.stdout.strip()

    def current_branch(self) -> str | None:
        """Return the current branch name, or ``None`` when ``HEAD`` is detached.

        Read-only inspection for ``knotica doctor``/``status`` -- never mutates.
        """
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"], optional_locks=False)
        branch = result.stdout.strip()
        return None if branch == "HEAD" else branch

    def unpushed_count(self) -> int | None:
        """Return commits on ``HEAD`` not yet on its upstream.

        ``None`` when the branch tracks no upstream (no remote configured) --
        there is nothing to be behind. Read-only; never mutates.
        """
        upstream = self._run(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            check=False,
            optional_locks=False,
        )
        if upstream.returncode != 0 or not upstream.stdout.strip():
            return None
        result = self._run(["rev-list", "--count", "@{upstream}..HEAD"], optional_locks=False)
        return int(result.stdout.strip() or "0")

    def changed_paths(self, base: str, head: str = "HEAD") -> list[str]:
        """Repo-relative paths that differ between ``base`` and ``head`` (read-only)."""
        result = self._run(["diff", "--name-only", f"{base}..{head}"], optional_locks=False)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def commit_timestamp(self, commit: str) -> int:
        """The commit's committer timestamp (unix seconds; read-only)."""
        result = self._run(["log", "-1", "--format=%ct", commit], optional_locks=False)
        return int(result.stdout.strip() or "0")

    def is_dirty(self, paths: Sequence[str | PurePath] | None = None) -> bool:
        """Return whether the work tree has uncommitted changes.

        Args:
            paths: Optional vault-relative paths to restrict the check to.
                ``None`` checks the whole tree (untracked files included).
        """
        command = ["status", "--porcelain"]
        if paths is not None:
            command += ["--", *_normalize_paths(paths)]
        result = self._run(command, optional_locks=False)
        return bool(result.stdout.strip())

    def list_dirty_entries(self) -> list[dict[str, str | bool]]:
        """Return porcelain dirty entries as path-scoped repair candidates.

        Read-only. Each entry is ``{path, code, tracked, untracked}`` where
        ``code`` is the two-letter porcelain status (e.g. `` M``, ``??``).
        Rename lines use the destination path. Never mutates.
        """
        result = self._run(["status", "--porcelain"], optional_locks=False)
        entries: list[dict[str, str | bool]] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            code = line[:2]
            rest = line[3:]
            path = rest.split(" -> ", 1)[1] if " -> " in rest else rest
            path = path.strip().strip('"')
            if not path:
                continue
            untracked = code == "??"
            entries.append(
                {
                    "path": PurePath(path).as_posix(),
                    "code": code,
                    "tracked": not untracked,
                    "untracked": untracked,
                }
            )
        return entries

    def clone_to(self, dest_root: str | PurePath, ref: str | None = None) -> "VaultVcs":
        """Clone this vault to ``dest_root`` and return a ``VaultVcs`` on the clone.

        Runs ``git clone <this-root> <dest_root>`` and, when ``ref`` is given,
        checks that commit-ish out on the clone. The returned wrapper is bound to
        the clone, which is the same shape as any vault root -- so
        :class:`~knotica.store.LocalFSStore`, the search backend, and
        ``VaultTransaction`` all work against it unchanged.

        This is the frozen-corpus mechanism: an eval loop clones the live vault
        to a throwaway tree, works there, and leaves the source byte-identical.
        ``clone_to`` is a read/checkout operation -- it only reads the source and
        writes a fresh tree elsewhere, never mutating the live vault -- which is
        why it lives here (keeping all git subprocess in one module) rather than
        in a separate clone module, and why it is absent from the mutating
        surface the single-mutation-path fitness test guards.

        A fresh clone inherits no committer identity from the source's local git
        config, so a fixed local identity is stamped on the clone (and gpg
        signing disabled) -- see :func:`_stamp_committer_identity` -- so a later
        eval ``VaultTransaction`` commit succeeds regardless of the machine's
        ambient global git config, and ``head_sha()`` on the returned wrapper
        yields the pinned corpus SHA.

        Args:
            dest_root: Where to create the clone. ``git clone`` creates this
                directory; it must not already exist.
            ref: Optional commit-ish to check out after cloning. ``None`` leaves
                the clone on the source's default ``HEAD``.

        Returns:
            A :class:`VaultVcs` bound to the freshly created clone.

        Raises:
            GitError: If the clone or the optional checkout fails.
        """
        destination = Path(dest_root)
        self._run(["clone", str(self._root), str(destination)])
        clone = VaultVcs(destination)
        clone._stamp_committer_identity()
        if ref is not None:
            clone._run(["checkout", ref], retry_index_lock=True)
        return clone

    def _stamp_committer_identity(self) -> None:
        """Set a fixed local committer identity and disable gpg signing here.

        Local git config overrides global, so this makes the eval committer
        identity deterministic on every machine and lets a commit succeed even
        where no global identity is configured (a fresh clone inherits none).
        """
        self._run(["config", "user.name", _CLONE_COMMITTER_NAME])
        self._run(["config", "user.email", _CLONE_COMMITTER_EMAIL])
        self._run(["config", "commit.gpgsign", "false"])

    def commit_paths(self, paths: Sequence[str | PurePath], message: str) -> str:
        """Stage and commit exactly the given paths; return the new commit SHA.

        Both the ``add`` and the ``commit`` are scoped to ``paths`` -- content
        staged by anyone else stays staged and uncommitted. Deletions of the
        named paths are recorded too (``git add`` stages a named deletion).

        Raises:
            ValueError: If ``paths`` is empty (an unscoped commit is exactly
                the operation this API exists to forbid).
            GitError: If the commit fails -- including "nothing to commit"
                for the given paths (callers detect no-ops beforehand).
        """
        normalized = _normalize_paths(paths)
        if not normalized:
            raise ValueError(
                "commit_paths() requires at least one path; refusing an unscoped commit"
            )
        self._run(["add", "--", *normalized], retry_index_lock=True)
        self._run(["commit", "-m", message, "--", *normalized], retry_index_lock=True)
        return self.head_sha()

    def rollback_paths(self, paths: Sequence[str | PurePath], ref: str) -> None:
        """Restore exactly the given paths to their state at ``ref``.

        Paths that existed at ``ref`` are checked out from it (index and work
        tree); paths that did not exist there are unstaged and deleted. All
        other files -- tracked, staged, or untracked -- are left untouched:
        this is the path-scoped replacement for ``reset --hard``.

        Raises:
            ValueError: If ``paths`` is empty.
            GitError: If any restore command fails.
        """
        normalized = _normalize_paths(paths)
        if not normalized:
            raise ValueError("rollback_paths() requires at least one path")
        existing_at_ref = [path for path in normalized if self._exists_at_ref(ref, path)]
        created_since_ref = [path for path in normalized if path not in existing_at_ref]
        if existing_at_ref:
            self._run(["checkout", ref, "--", *existing_at_ref], retry_index_lock=True)
        if created_since_ref:
            self._remove_created_paths(created_since_ref)

    # ------------------------------------------------------------------
    # Branch lifecycle (loop runner). Not part of the path-scoped write
    # surface — adapters must still never call these; the loop orchestrator
    # and tests are the intended callers.
    # ------------------------------------------------------------------

    def list_branch_tips(self, prefix: str = "loop/") -> list[tuple[str, str]]:
        """Return ``(branch_name, tip_sha)`` for local heads under ``prefix``.

        Read-only. Empty when no matching branches exist. ``prefix`` may be empty
        to list every local head. The glob is ``**`` so nested branch names
        (``loop/c/<topic>/source-<id8>``) match — a single ``*`` stops at ``/``.
        """
        pattern = f"refs/heads/{prefix}**" if prefix else "refs/heads/**"
        result = self._run(
            ["for-each-ref", "--format=%(refname:short)\t%(objectname)", pattern],
            check=False,
            optional_locks=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        tips: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            name, _, sha = line.partition("\t")
            if name and sha:
                tips.append((name.strip(), sha.strip()))
        return tips

    def tip_committer_iso(self, ref: str) -> str | None:
        """Return the tip committer timestamp (ISO-8601) for ``ref``, or ``None``."""
        result = self._run(
            ["log", "-1", "--format=%cI", ref],
            check=False,
            optional_locks=False,
        )
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        return text or None

    def branch_exists(self, name: str) -> bool:
        """Return whether local branch ``name`` exists."""
        result = self._run(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
            check=False,
            optional_locks=False,
        )
        return result.returncode == 0

    def ref_sha(self, ref: str) -> str:
        """Return the full object name for ``ref`` (branch, tag, or SHA)."""
        result = self._run(["rev-parse", ref], optional_locks=False)
        return result.stdout.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Return whether ``ancestor`` is an ancestor of ``descendant`` (inclusive)."""
        result = self._run(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
            optional_locks=False,
        )
        return result.returncode == 0

    def default_branch(self) -> str:
        """Best-effort default branch name (``main`` / ``master`` / current)."""
        symbolic = self._run(
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            check=False,
            optional_locks=False,
        )
        if symbolic.returncode == 0:
            remote_head = symbolic.stdout.strip()  # e.g. origin/main
            if "/" in remote_head:
                return remote_head.split("/", 1)[1]
        for candidate in ("main", "master"):
            probe = self._run(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
                check=False,
                optional_locks=False,
            )
            if probe.returncode == 0:
                return candidate
        current = self.current_branch()
        if current is not None:
            return current
        raise GitError("could not determine the vault's default branch")

    def create_branch(self, name: str, start_ref: str = "HEAD") -> None:
        """Create local branch ``name`` at ``start_ref`` (does not switch)."""
        self._run(["branch", name, start_ref], retry_index_lock=True)

    def checkout_branch(self, name: str) -> None:
        """Switch the work tree to local branch ``name``."""
        self._run(["checkout", name], retry_index_lock=True)

    def delete_branch(self, name: str, *, force: bool = True) -> None:
        """Delete local branch ``name`` (force by default — discard a failed candidate)."""
        flag = "-D" if force else "-d"
        self._run(["branch", flag, name], retry_index_lock=True)

    def fetch_ref_from(self, other_root: str | Path, source_ref: str, dest_ref: str) -> None:
        """Fetch ``source_ref`` from another local repo into ``dest_ref`` here.

        Used to pull an eval clone's tip back onto the source as a result branch
        without a network remote.
        """
        self._run(
            ["fetch", str(Path(other_root)), f"{source_ref}:{dest_ref}"],
            retry_index_lock=True,
        )

    def merge_branch(self, branch: str, *, ff_only: bool = False, no_ff: bool = False) -> str:
        """Merge ``branch`` into ``HEAD``; return the new tip SHA.

        ``ff_only=True`` refuses non-fast-forward merges. ``no_ff=True`` forces a
        merge commit (used by compile promote for a clear audit trail). The loop
        runner uses a regular merge by default because mid-cycle ``loop-state.json``
        commits on the default branch can diverge from a candidate that branched earlier.
        """
        args = ["merge", "--no-edit"]
        if ff_only:
            args.append("--ff-only")
        elif no_ff:
            args.append("--no-ff")
        args.append(branch)
        self._run(args, retry_index_lock=True)
        return self.head_sha()

    def is_merge_in_progress(self) -> bool:
        """Return whether a merge is in progress (``MERGE_HEAD`` exists)."""
        result = self._run(
            ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
            check=False,
            optional_locks=False,
        )
        return result.returncode == 0

    def unmerged_paths(self) -> list[str]:
        """Return vault-relative paths with unresolved merge conflicts."""
        result = self._run(
            ["diff", "--name-only", "--diff-filter=U"],
            check=False,
            optional_locks=False,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def checkout_merge_side(self, path: str, side: str) -> None:
        """Check out ``ours`` or ``theirs`` for one conflicted path during a merge."""
        if side not in {"ours", "theirs"}:
            raise ValueError("side must be 'ours' or 'theirs'")
        self._run(["checkout", f"--{side}", "--", path], retry_index_lock=True)
        self._run(["add", "--", path], retry_index_lock=True)

    def continue_merge(self) -> str:
        """Complete an in-progress merge with the default merge message."""
        self._run(["commit", "--no-edit"], retry_index_lock=True)
        return self.head_sha()

    def abort_merge(self) -> None:
        """Abort an in-progress merge and restore ``HEAD``."""
        self._run(["merge", "--abort"], retry_index_lock=True)

    def heal_git_mutation_state(self) -> None:
        """Clear crash-left mutation state so a fresh span starts git-clean.

        A pass that crashed mid-span auto-releases its vault flock (the OS drops
        it on process death) but can leave a dangling ``MERGE_HEAD`` or a stale
        ``.git/index.lock`` behind, either of which would break the next pass's
        commit. Called at span entry *while the vault flock is held* -- so no live
        knotica git process owns that index lock and any leftover is a crash
        remnant, safe to clear. The stale index lock is removed before aborting
        the merge because ``git merge --abort`` itself needs the index.
        Idempotent: a no-op on an already-clean tree.
        """
        self._clear_stale_index_lock()
        if self.is_merge_in_progress():
            self.abort_merge()

    def _clear_stale_index_lock(self) -> None:
        """Remove a leftover ``.git/index.lock`` (safe only under the vault flock)."""
        git_dir = self._root / ".git"
        if not git_dir.is_dir():
            return
        (git_dir / "index.lock").unlink(missing_ok=True)

    def push(self, remote: str, refspec: str) -> None:
        """Push ``refspec`` to ``remote`` (e.g. ``main`` or ``loop/result/abc``)."""
        self._run(["push", remote, refspec], retry_index_lock=True)

    # ------------------------------------------------------------------
    # Worktree lifecycle. A worktree lets a multi-step operation (e.g. a
    # source-candidate ingest session) commit to a branch other than the
    # canonical repo's checked-out branch, without switching that checkout
    # or disturbing its working tree. Mirrors the branch-lifecycle group
    # above: the loop/ingest orchestration layer is the intended caller.
    # ------------------------------------------------------------------

    def add_worktree(
        self, path: str | PurePath, *, branch: str, start_ref: str = "HEAD"
    ) -> "VaultVcs":
        """Create a worktree at ``path`` checked out on a new ``branch``.

        Runs ``git worktree add -b <branch> <path> <start_ref>``, which both
        creates ``branch`` at ``start_ref`` and registers a working tree for
        it at ``path`` -- distinct from :meth:`create_branch`, which creates
        a branch without checking it out anywhere. The canonical repo's own
        checkout is untouched.

        Args:
            path: Where to create the worktree. Must not already exist.
            branch: Name of the new branch to create and check out there.
            start_ref: Commit-ish the new branch starts from.

        Returns:
            A :class:`VaultVcs` bound to the newly created worktree.

        Raises:
            GitError: If ``branch`` already exists, or the worktree cannot
                be created (e.g. ``path`` already exists).
        """
        destination = Path(path)
        self._run(
            ["worktree", "add", "-b", branch, str(destination), start_ref],
            retry_index_lock=True,
        )
        return VaultVcs(destination)

    def remove_worktree(self, path: str | PurePath) -> None:
        """Remove the worktree registered at ``path``, leaving its branch intact.

        Refuses (raises :class:`GitError`) when the worktree has uncommitted
        changes -- callers must commit or discard first. The branch itself is
        never deleted; use :meth:`delete_branch` for that separately.
        """
        self._run(["worktree", "remove", str(Path(path))], retry_index_lock=True)

    def list_worktrees(self) -> list[dict[str, str]]:
        """Return registered worktrees as ``{path, sha, branch}`` (read-only).

        ``branch`` is the short ref name (e.g. ``loop/wip/<topic>/source-abc``)
        or an empty string for a detached-HEAD worktree. The canonical repo's
        own checkout is included as the first entry. Never mutates.
        """
        result = self._run(["worktree", "list", "--porcelain"], optional_locks=False)
        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                current["path"] = value
            elif key == "HEAD":
                current["sha"] = value
            elif key == "branch":
                current["branch"] = value.removeprefix("refs/heads/")
        if current:
            worktrees.append(current)
        for entry in worktrees:
            entry.setdefault("branch", "")
        return worktrees

    def publish_branch(self, src_ref: str, dest_name: str) -> None:
        """Atomically rename branch ``src_ref`` to ``dest_name``.

        A single ``git branch -m`` -- the branch keeps its history and tip
        SHA; only the ref name changes. Used both to finalize an ingest
        session (``loop/wip/<topic>/source-<id8> -> loop/c/<topic>/source-<id8>``)
        and to quarantine a refused source candidate
        (``loop/c/<topic>/source-<id8> -> loop/x/<topic>/source-<id8>``) --
        in both cases the candidate becomes invisible to a scan for the old
        prefix without ever being deleted.

        Raises:
            GitError: If ``src_ref`` does not exist, is checked out in
                another worktree, or ``dest_name`` already exists.
        """
        self._run(["branch", "-m", src_ref, dest_name], retry_index_lock=True)

    def _remove_created_paths(self, paths: list[str]) -> None:
        """Unstage and delete paths that did not exist at the rollback ref."""
        self._run(
            ["rm", "--cached", "--ignore-unmatch", "--quiet", "--", *paths],
            retry_index_lock=True,
        )
        for path in paths:
            (self._root / path).unlink(missing_ok=True)

    def file_exists_at(self, ref: str, path: str) -> bool:
        """Return whether ``path`` exists as a blob at ``ref`` (read-only)."""
        return self._exists_at_ref(ref, path)

    def read_file_at(self, ref: str, path: str) -> str | None:
        """Return file contents at ``ref:path``, or ``None`` when absent."""
        if not self._exists_at_ref(ref, path):
            return None
        result = self._run(["show", f"{ref}:{path}"], optional_locks=False)
        return result.stdout

    def diff_between(
        self,
        base: str,
        head: str,
        path: str,
        *,
        triple_dot: bool = False,
    ) -> str:
        """Return unified diff for ``path`` between ``base`` and ``head`` (read-only)."""
        sep = "..." if triple_dot else ".."
        result = self._run(
            ["diff", f"{base}{sep}{head}", "--", path],
            check=False,
            optional_locks=False,
        )
        return result.stdout

    def path_commit_shas(self, path: str, limit: int = 2) -> list[str]:
        """Return up to ``limit`` newest commit SHAs that touched ``path``."""
        if limit < 1:
            return []
        result = self._run(
            ["log", f"-{limit}", "--format=%H", "--", path],
            check=False,
            optional_locks=False,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def merge_parents(self, commit: str) -> tuple[str, str] | None:
        """Return ``(first_parent, second_parent)`` for a merge commit, else ``None``."""
        result = self._run(
            ["rev-list", "--parents", "-n", "1", commit],
            check=False,
            optional_locks=False,
        )
        parts = result.stdout.strip().split()
        if len(parts) >= 3:
            return parts[1], parts[2]
        return None

    def find_merge_commit_for_branch(self, branch_name: str) -> str | None:
        """Best-effort: newest merge commit whose message mentions ``branch_name``."""
        needle = f"Merge branch '{branch_name}'"
        result = self._run(
            [
                "log",
                "--merges",
                f"--grep={needle}",
                "-1",
                "--format=%H",
            ],
            check=False,
            optional_locks=False,
        )
        sha = result.stdout.strip()
        return sha or None

    def list_compile_merge_commits(
        self,
        topic: str,
        *,
        limit: int = 20,
    ) -> list[tuple[str, str]]:
        """Return ``(branch_name, merge_sha)`` pairs from ``--no-ff`` compile promotes."""
        prefix = f"compile/{topic.strip().strip('/')}/"
        needle = f"Merge branch '{prefix}"
        result = self._run(
            ["log", f"-{limit}", "--merges", "--format=%H %s"],
            check=False,
            optional_locks=False,
        )
        rows: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            merge_sha, _, subject = line.partition(" ")
            if needle not in subject:
                continue
            start = subject.find("'") + 1
            end = subject.find("'", start)
            if start <= 0 or end <= start:
                continue
            branch = subject[start:end]
            if branch.startswith(prefix):
                rows.append((branch, merge_sha))
        return rows

    def _exists_at_ref(self, ref: str, path: str) -> bool:
        """Return whether ``path`` exists as a blob at ``ref``."""
        result = self._run(["cat-file", "-e", f"{ref}:{path}"], check=False)
        return result.returncode == 0

    def _run(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
        retry_index_lock: bool = False,
        optional_locks: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run git in the vault root; retry on transient concurrent-git contention.

        ``--literal-pathspecs`` disables glob magic so vault paths are always
        taken verbatim -- a page named ``*.md`` must never widen a pathspec.
        """
        command = ["git", "--literal-pathspecs", *arguments]
        env = dict(os.environ)
        if not optional_locks:
            env["GIT_OPTIONAL_LOCKS"] = "0"
        attempts = 1 + (INDEX_LOCK_RETRIES if retry_index_lock else 0)
        for attempt in range(attempts):
            result = subprocess.run(
                command,
                cwd=self._root,
                env=env,
                capture_output=True,
                text=True,
                timeout=_GIT_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode == 0 or not check:
                return result
            transient_race = any(
                signature in result.stderr.lower() for signature in _TRANSIENT_GIT_RACE_SIGNATURES
            )
            if transient_race and attempt < attempts - 1:
                time.sleep(INDEX_LOCK_RETRY_WAIT_SECONDS)
                continue
            raise GitError(
                f"git {arguments[0]} failed in {self._root} (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}",
                command=command,
                output=result.stderr + result.stdout,
            )
        raise AssertionError("unreachable: retry loop always returns or raises")


def _normalize_paths(paths: Sequence[str | PurePath]) -> list[str]:
    """Normalize vault-relative paths to POSIX strings, rejecting absolutes."""
    normalized: list[str] = []
    for path in paths:
        pure = PurePath(path)
        if pure.is_absolute():
            raise ValueError(f"Vault paths must be relative to the vault root: {path}")
        normalized.append(pure.as_posix())
    return normalized
