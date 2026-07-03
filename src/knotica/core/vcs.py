"""Path-scoped git operations for the vault (subprocess-based, stdlib only).

``VaultVcs`` is the core's only git surface. Its API is deliberately incapable
of touching files it was not told about: every mutating operation takes an
explicit list of vault-relative paths, and there is **no** add-all, no
``reset --hard``, and no history-destroying primitive. This is structural
protection for the vault's foreign content -- a concurrent Obsidian edit to an
unrelated file can never be swept into a knotica commit or destroyed by a
knotica rollback.

Consumers: ``core.transaction`` only (the single mutation path). Adapters never
import this module. Commit messages are composed by the caller (the frozen
grammar lives in the vault constitution and ``core.records``); this module
treats them as opaque strings.

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
