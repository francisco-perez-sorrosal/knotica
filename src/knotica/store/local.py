"""Local-filesystem ``VaultStore`` backend.

``LocalFSStore`` implements the :class:`~knotica.store.VaultStore` protocol
structurally (no inheritance -- the protocol is a typing contract, not a base
class) against a vault directory on the local disk. Stdlib only.

Two invariants carry the whole module:

* **Confinement** -- every public method funnels through :meth:`_resolve`,
  which rejects absolute paths outright and resolves the candidate (following
  symlinks) to verify it stays inside the vault root. Nothing touches the
  filesystem before that check passes.
* **Atomicity** -- writes land in a ``tempfile.mkstemp`` file created in the
  target's own directory, are flushed and fsynced, then swapped into place
  with ``os.replace``. Same-directory placement guarantees the rename never
  crosses a filesystem boundary (a cross-device rename is a copy, not an
  atomic swap).
"""

import os
import tempfile
from pathlib import Path, PurePath

from knotica.store import PathOutsideVaultError


class LocalFSStore:
    """Vault storage on the local filesystem, confined to one root directory.

    Args:
        root: The vault root directory. Resolved (symlinks followed) at
            construction; must already exist and be a directory -- the store
            never creates the vault itself.

    Raises:
        NotADirectoryError: If ``root`` does not exist or is not a directory.
    """

    def __init__(self, root: str | PurePath) -> None:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(f"Vault root is not an existing directory: {root}")
        self._root = resolved

    @property
    def root(self) -> Path:
        """The resolved vault root this store is confined to (read-only)."""
        return self._root

    def read_text(self, path: str | PurePath) -> str:
        """Return the UTF-8 content of the vault file at ``path``."""
        return self._resolve(path).read_text(encoding="utf-8")

    def write_text_atomic(self, path: str | PurePath, content: str) -> None:
        """Write ``content`` to ``path`` via a same-directory temp file + ``os.replace``.

        Creates missing parent directories inside the vault. On any failure
        the target is left exactly as it was (prior content or prior absence)
        and the temporary file is removed.
        """
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.tmp")
        try:
            with os.fdopen(descriptor, "wb") as temp_file:
                temp_file.write(content.encode("utf-8"))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def exists(self, path: str | PurePath) -> bool:
        """Return whether ``path`` exists in the vault (file or directory)."""
        return self._resolve(path).exists()

    def list_dir(self, path: str | PurePath = "") -> list[str]:
        """Return sorted entry names of the vault directory at ``path`` (default: root)."""
        return sorted(entry.name for entry in self._resolve(path).iterdir())

    def delete(self, path: str | PurePath) -> None:
        """Delete the vault file at ``path`` (files only; directories are refused)."""
        target = self._resolve(path)
        if target.is_dir():
            raise IsADirectoryError(f"delete() removes files only, got a directory: {path}")
        target.unlink()

    def _resolve(self, path: str | PurePath) -> Path:
        """Resolve a vault-relative ``path`` and verify it stays inside the root.

        Rejects absolute inputs before touching the filesystem, then resolves
        the joined path (following symlinks and collapsing ``..`` segments)
        and requires the result to be the root itself or a descendant of it.
        """
        candidate = PurePath(path)
        if candidate.is_absolute():
            raise PathOutsideVaultError(
                f"Vault paths must be relative to the vault root, got absolute path: {path}"
            )
        resolved = (self._root / candidate).resolve()
        if not resolved.is_relative_to(self._root):
            raise PathOutsideVaultError(f"Path escapes the vault root ({self._root}): {path}")
        return resolved
