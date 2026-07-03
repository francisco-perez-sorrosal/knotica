"""The vault mutation lock -- an in-vault ``fcntl.flock`` guard (POSIX).

One exclusive lock per vault, held for the duration of a mutating transaction,
coordinates writers **across processes**: stdio MCP servers can be long-lived
and shared across concurrent sessions, so process-per-session isolation cannot
be assumed. The lock file lives inside the vault at ``.knotica/locks/vault.lock``
(gitignored via the template) so any process mutating a given vault contends on
the same inode.

Discipline (load-bearing): the critical section spans **filesystem and git work
only** -- never network calls, LLM calls, or long computation. Holding the lock
across slow work starves every concurrent session into lock-busy retries. The
API is a plain context manager with no callback hooks by design: there is no
seam through which slow work can be injected into the acquisition path.

Contention surfaces as :class:`LockBusyError` after a bounded acquisition wait
(never a hang); the transaction layer maps it to the retryable lock-busy result.
"""

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath

#: Vault-relative location of the lock file (parent dirs created lazily).
LOCK_RELATIVE_PATH = PurePath(".knotica/locks/vault.lock")
#: Default bounded wait for acquisition. Small on purpose: a healthy critical
#: section (fs + git only) completes in well under a second, so anything that
#: makes a caller wait longer deserves a retryable busy signal, not patience.
DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 5.0
#: Poll interval between non-blocking acquisition attempts.
_POLL_INTERVAL_SECONDS = 0.1

_LOCK_FILE_MODE = 0o644


class LockBusyError(Exception):
    """The vault lock is held by a concurrent operation (retryable).

    The mutation transaction maps this to the retryable lock-busy result.
    """

    def __init__(self, lock_path: Path, timeout: float) -> None:
        super().__init__(
            f"Could not acquire the vault lock ({lock_path}) within {timeout:.1f}s: "
            "another operation is in progress. Retry in a moment."
        )
        self.lock_path = lock_path
        self.timeout = timeout


@contextmanager
def vault_lock(
    vault_root: str | PurePath,
    timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Hold the exclusive mutation lock for ``vault_root``'s vault.

    Creates ``.knotica/locks/`` lazily. The lock file itself is never
    unlinked: removing it would let a later acquirer lock a fresh inode while
    an earlier holder still owns the old one, silently admitting two writers.

    Args:
        vault_root: The vault root directory.
        timeout: Maximum seconds to wait for acquisition; ``0`` means a
            single non-blocking attempt.

    Raises:
        LockBusyError: If the lock is not acquired within ``timeout``.
    """
    lock_path = Path(vault_root) / LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, _LOCK_FILE_MODE)
    try:
        _acquire_within(descriptor, lock_path, timeout)
        yield
    finally:
        # Releasing an unlocked descriptor is a no-op, so this is safe even
        # when acquisition itself raised. Close still releases on its own;
        # the explicit unlock keeps the release visible and ordered.
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _acquire_within(descriptor: int, lock_path: Path, timeout: float) -> None:
    """Poll for the exclusive flock until acquired or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LockBusyError(lock_path, timeout) from None
            time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
