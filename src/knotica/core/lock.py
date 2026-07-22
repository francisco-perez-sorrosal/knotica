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
import threading
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

#: Thread-local nesting depth of *active mutation spans* per canonical vault
#: root -- written only by :func:`vault_span_lock`. It scopes reentrancy to the
#: widened span: while a span is active on this thread, a nested ``vault_lock``
#: (a :class:`~knotica.core.transaction.VaultTransaction` commit opened inside
#: the span) reuses the held flock instead of self-deadlocking on a second file
#: descriptor. A plain ``vault_lock`` that is *not* inside a span never marks the
#: depth, so two nested transactions with no span still contend -- the
#: single-writer safety contract is unchanged.
_span_state = threading.local()


def _span_depths() -> dict[str, int]:
    """The per-thread ``{canonical_root: active_span_depth}`` map (lazy init)."""
    depths: dict[str, int] | None = getattr(_span_state, "depths", None)
    if depths is None:
        depths = {}
        _span_state.depths = depths
    return depths


def _canonical_key(vault_root: str | PurePath) -> str:
    """The reentrancy key for a vault: its resolved root as a string."""
    return str(Path(vault_root).resolve())


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


def span_is_active(vault_root: str | PurePath) -> bool:
    """True when this thread currently holds a mutation span for ``vault_root``.

    Lets acquire-time self-heal decide safely: a fresh (non-nested) acquisition
    may clear crash remnants, but a transaction nested inside a live span must
    never "heal" -- the span's own merge may be legitimately in flight.
    """
    return _span_depths().get(_canonical_key(vault_root), 0) > 0


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
    if _span_depths().get(_canonical_key(vault_root), 0) > 0:
        # A widened mutation span already holds this vault's flock on this
        # thread; reuse it. A second ``os.open`` + ``flock`` in the same thread
        # would self-deadlock (flock is per open file description). Cross-thread
        # and cross-process acquirers are unaffected -- they carry their own,
        # empty, thread-local depth and contend on the real flock as before.
        yield
        return
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


@contextmanager
def vault_span_lock(
    vault_root: str | PurePath,
    *,
    timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> Iterator[bool]:
    """Hold the exclusive vault flock across a full multi-step git span.

    The plain :func:`vault_lock` brackets one commit; a loop pass, though, runs a
    contiguous git sequence (checkout -> merge -> branch delete -> commit) that
    must not interleave with a concurrent pass's own sequence on the same working
    tree -- that race corrupts git (a dangling ``MERGE_HEAD``, ``cannot lock
    ref``). This widens the *same* flock (one lock, not a second layer) to bracket
    that whole span.

    Reentrant per thread + canonical root: the outermost entry takes the real
    flock and yields ``True``; a nested ``vault_span_lock`` -- or any
    ``vault_lock`` opened inside the span -- reuses it and yields ``False``. Only
    the outermost holder should run span-entry self-heal, hence the flag.

    Slow work (eval, the arena race) must stay *outside* the span: it runs on a
    throwaway clone and never touches the real vault's git state, so holding the
    flock across it would needlessly serialize every concurrent session.

    Yields:
        ``True`` when this is the outermost span acquisition on this thread (the
        real flock was just taken), ``False`` for a reentrant nested acquisition.

    Raises:
        LockBusyError: If the flock is not acquired within ``timeout`` (outermost
            entry only; a reentrant entry never contends).
    """
    key = _canonical_key(vault_root)
    depths = _span_depths()
    if depths.get(key, 0) > 0:
        depths[key] += 1
        try:
            yield False
        finally:
            depths[key] -= 1
        return
    # Outermost: take the real flock first (depth still 0, so the nested
    # ``vault_lock`` below does the true acquisition), then mark the span active
    # so subsequent nested acquisitions on this thread reuse it.
    inner = vault_lock(vault_root, timeout=timeout)
    inner.__enter__()
    depths[key] = 1
    try:
        yield True
    finally:
        depths[key] -= 1
        if depths[key] <= 0:
            depths.pop(key, None)
        inner.__exit__(None, None, None)


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
