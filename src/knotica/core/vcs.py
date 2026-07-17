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
user-facing git-failure contract. Transient ``index.lock`` contention (another
git process mid-operation) is retried a few times before giving up.
"""

import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path, PurePath

#: Seconds to wait before retrying a git command that lost an ``index.lock`` race.
INDEX_LOCK_RETRY_WAIT_SECONDS = 2.0
#: Number of retries (beyond the first attempt) on ``index.lock`` contention.
INDEX_LOCK_RETRIES = 3

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
        to list every local head (``refs/heads/*``).
        """
        pattern = f"refs/heads/{prefix}*" if prefix else "refs/heads/*"
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

    def branch_exists(self, name: str) -> bool:
        """Return whether local branch ``name`` exists."""
        result = self._run(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
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

    def merge_branch(self, branch: str, *, ff_only: bool = False) -> str:
        """Merge ``branch`` into ``HEAD``; return the new tip SHA.

        ``ff_only=True`` refuses non-fast-forward merges. The loop runner uses
        a regular merge by default because mid-cycle ``loop-state.json`` commits
        on the default branch can diverge from a candidate that branched earlier.
        """
        args = ["merge", "--no-edit"]
        if ff_only:
            args.append("--ff-only")
        args.append(branch)
        self._run(args, retry_index_lock=True)
        return self.head_sha()

    def push(self, remote: str, refspec: str) -> None:
        """Push ``refspec`` to ``remote`` (e.g. ``main`` or ``loop/result/abc``)."""
        self._run(["push", remote, refspec], retry_index_lock=True)

    def _remove_created_paths(self, paths: list[str]) -> None:
        """Unstage and delete paths that did not exist at the rollback ref."""
        self._run(
            ["rm", "--cached", "--ignore-unmatch", "--quiet", "--", *paths],
            retry_index_lock=True,
        )
        for path in paths:
            (self._root / path).unlink(missing_ok=True)

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
        """Run git in the vault root; retry on ``index.lock`` contention.

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
            index_lock_race = "index.lock" in result.stderr
            if index_lock_race and attempt < attempts - 1:
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
