"""Storage boundary -- ``VaultStore`` protocol and the local filesystem backend.

Pure storage primitives: atomic temp+rename writes, reads, existence checks,
listing, deletion. Knows nothing about git, logs, schemas, or records; stdlib
only. Innermost layer: anything may depend on ``store``; ``store`` depends on
nothing else in knotica. Writing the vault through this package from outside
``knotica.core.transaction`` is forbidden (enforced by the import-boundary
fitness test).

Contract summary
----------------
Every method takes a **vault-relative** path. Implementations must resolve the
path against the vault root and reject any path that is absolute or that
escapes the root (via ``..`` segments or symlinks) by raising
:class:`PathOutsideVaultError` -- uniformly, on every method, including pure
reads. Atomic writes go through a same-directory temporary file finalized with
``os.replace`` so that a reader never observes a partially written file and an
interrupted write leaves any prior content intact.
"""

from pathlib import PurePath
from typing import Protocol, runtime_checkable

__all__ = ["LocalFSStore", "PathOutsideVaultError", "VaultStore"]


class PathOutsideVaultError(ValueError):
    """A vault-relative path is absolute or resolves outside the vault root.

    Raised by every :class:`VaultStore` method before any filesystem side
    effect. Covers three escape shapes: absolute input paths, ``..`` traversal
    that resolves above the root, and symlinks inside the vault whose targets
    resolve outside it. Subclasses :class:`ValueError` because the offending
    path is invalid input at the storage boundary, not a transient condition.
    """


@runtime_checkable
class VaultStore(Protocol):
    """Structural protocol for vault storage backends.

    Implementations provide raw file mechanics for a single vault rooted at a
    fixed directory. All paths are vault-relative; path-safety (see
    :class:`PathOutsideVaultError`) is enforced on every call. The protocol is
    deliberately minimal -- no git, no locking, no schema or record knowledge;
    those concerns live in ``knotica.core`` behind the single mutation path.

    Error contract (beyond path safety): missing files raise
    ``FileNotFoundError``; type mismatches raise ``IsADirectoryError`` /
    ``NotADirectoryError``. Implementations must not swallow these -- callers
    in ``core`` translate them into the user-facing error envelope.
    """

    def read_text(self, path: str | PurePath) -> str:
        """Return the full content of the file at ``path``, decoded as UTF-8.

        Raises ``FileNotFoundError`` if the file does not exist and
        ``IsADirectoryError`` if ``path`` names a directory.
        """
        ...

    def write_text_atomic(self, path: str | PurePath, content: str) -> None:
        """Write ``content`` (UTF-8) to ``path`` atomically.

        The content is written to a temporary file **in the same directory**
        as the target and moved into place with ``os.replace`` -- the rename
        never crosses a filesystem boundary, so the swap is atomic: a
        concurrent reader sees either the prior content or the new content,
        never a partial file, and an interrupted write leaves the prior
        content (or prior absence) untouched. Missing parent directories
        *inside the vault* are created; the vault root itself must already
        exist. No temporary file survives a failed write.
        """
        ...

    def exists(self, path: str | PurePath) -> bool:
        """Return whether a file or directory exists at ``path``.

        Path safety is enforced even here: an escaping path raises
        :class:`PathOutsideVaultError` rather than returning ``False``, so a
        caller bug never masquerades as absence.
        """
        ...

    def list_dir(self, path: str | PurePath = "") -> list[str]:
        """Return the sorted entry names of the directory at ``path``.

        Defaults to the vault root. Names are bare entry names (not paths),
        sorted lexicographically for deterministic output. Raises
        ``FileNotFoundError`` if the directory does not exist and
        ``NotADirectoryError`` if ``path`` names a file.
        """
        ...

    def delete(self, path: str | PurePath) -> None:
        """Delete the file at ``path``.

        Files only -- directories raise ``IsADirectoryError`` (deleting a
        tree is never a single-file storage primitive). A missing file raises
        ``FileNotFoundError``; callers wanting idempotent deletion check
        :meth:`exists` first.
        """
        ...


from knotica.store.local import LocalFSStore  # noqa: E402  (re-export; needs the names above)
