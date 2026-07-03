"""Behavioral contract tests for ``knotica.core.lock`` — the vault mutation lock.

The contract under test:

1. **Contention is bounded, never a hang.** A second acquirer — another
   process or another handle in the same process — gets ``LockBusyError``
   within the configured timeout; ``timeout=0`` is a single non-blocking
   attempt.
2. **Real flock semantics.** Contention is exercised against actual
   ``fcntl.flock`` holders (a thread with its own file description, and a
   real subprocess) — nothing is mocked.
3. **The lock file persists.** It is never unlinked (unlinking would let a
   later acquirer lock a fresh inode while the old holder still owns the old
   one, silently admitting two writers), its parent directories are created
   lazily, and the path is gitignored by the vault template.
4. **Not reentrant.** A nested acquisition in the same process contends like
   any other acquirer — there is no reentrant path through which two writers
   could be admitted.
"""

import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from knotica.core.lock import LOCK_RELATIVE_PATH, LockBusyError, vault_lock
from support.vault import git_is_ignored

#: Contender timeout used across contention tests: long enough to prove the
#: poll loop actually waits, short enough to keep the suite fast.
CONTENDER_TIMEOUT_SECONDS = 0.3
#: Generous wall-clock ceiling proving "bounded, not a hang" without flaking
#: on a loaded machine.
HANG_CEILING_SECONDS = 5.0


@contextmanager
def _lock_held_in_thread(vault: Path) -> Iterator[None]:
    """Hold the vault lock on a separate file description until exit.

    flock is per-open-file-description, so a second ``vault_lock`` entry in
    this same process genuinely contends with the holder.
    """
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with vault_lock(vault):
            held.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    try:
        assert held.wait(timeout=10), "lock-holder thread never acquired the lock"
        yield
    finally:
        release.set()
        holder.join(timeout=10)


_SUBPROCESS_HOLDER_SCRIPT = """\
import sys
from knotica.core.lock import vault_lock

with vault_lock(sys.argv[1]):
    print("held", flush=True)
    sys.stdin.readline()
"""


@contextmanager
def _lock_held_in_subprocess(vault: Path) -> Iterator[None]:
    """Hold the vault lock from a real second process until exit."""
    process = subprocess.Popen(
        [sys.executable, "-c", _SUBPROCESS_HOLDER_SCRIPT, str(vault)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        line = process.stdout.readline()
        assert line.strip() == "held", f"lock-holder subprocess failed to start: {line!r}"
        yield
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=10)


# ---------------------------------------------------------------------------
# Contention: bounded busy signal, real flock semantics
# ---------------------------------------------------------------------------


def test_a_second_acquirer_gets_busy_within_the_timeout_bound(template_vault: Path) -> None:
    with _lock_held_in_thread(template_vault):
        started = time.monotonic()
        with pytest.raises(LockBusyError):
            with vault_lock(template_vault, timeout=CONTENDER_TIMEOUT_SECONDS):
                pytest.fail("second acquirer must not enter while the lock is held")
        elapsed = time.monotonic() - started

    assert elapsed >= CONTENDER_TIMEOUT_SECONDS * 0.8, "busy raised before the timeout elapsed"
    assert elapsed < HANG_CEILING_SECONDS, "acquisition must be bounded by the timeout, not hang"


def test_contention_holds_across_real_processes(template_vault: Path) -> None:
    with _lock_held_in_subprocess(template_vault):
        started = time.monotonic()
        with pytest.raises(LockBusyError):
            with vault_lock(template_vault, timeout=CONTENDER_TIMEOUT_SECONDS):
                pytest.fail("a second process must not enter while another holds the lock")
        elapsed = time.monotonic() - started

    assert elapsed < HANG_CEILING_SECONDS


def test_timeout_zero_is_a_single_nonblocking_attempt(template_vault: Path) -> None:
    with _lock_held_in_thread(template_vault):
        started = time.monotonic()
        with pytest.raises(LockBusyError):
            with vault_lock(template_vault, timeout=0):
                pytest.fail("non-blocking attempt must not enter while the lock is held")
        elapsed = time.monotonic() - started

    assert elapsed < 1.0, "timeout=0 must fail immediately, not poll"


def test_nested_acquisition_in_the_same_process_contends_not_reenters(
    template_vault: Path,
) -> None:
    # No reentrant path exists: a nested entry is just another contender.
    with vault_lock(template_vault):
        with pytest.raises(LockBusyError):
            with vault_lock(template_vault, timeout=0):
                pytest.fail("the lock must not be reentrant — that would admit two writers")


def test_release_frees_the_lock_for_the_next_acquirer(template_vault: Path) -> None:
    with vault_lock(template_vault):
        pass
    # A single non-blocking attempt succeeds — the previous holder fully released.
    with vault_lock(template_vault, timeout=0):
        pass


def test_the_busy_error_names_the_lock_and_advises_retry(template_vault: Path) -> None:
    with _lock_held_in_thread(template_vault):
        with pytest.raises(LockBusyError) as exc_info:
            with vault_lock(template_vault, timeout=0):
                pass

    message = str(exc_info.value)
    assert str(template_vault / LOCK_RELATIVE_PATH) in message
    assert "retry" in message.lower(), "the busy message must tell the user what to do next"


# ---------------------------------------------------------------------------
# Lock file lifecycle
# ---------------------------------------------------------------------------


def test_the_lock_directory_is_created_lazily(template_vault: Path) -> None:
    lock_path = template_vault / LOCK_RELATIVE_PATH
    assert not lock_path.parent.exists(), (
        "precondition: the template must not ship the runtime locks directory"
    )
    with vault_lock(template_vault):
        assert lock_path.exists()


def test_the_lock_file_persists_after_release(template_vault: Path) -> None:
    lock_path = template_vault / LOCK_RELATIVE_PATH
    with vault_lock(template_vault):
        pass
    assert lock_path.exists(), (
        "the lock file must never be unlinked — a fresh inode would let a later "
        "acquirer lock while an old holder still owns the previous inode"
    )


def test_the_lock_path_is_gitignored_by_the_template(template_vault: Path) -> None:
    with vault_lock(template_vault):
        pass
    assert git_is_ignored(template_vault, str(LOCK_RELATIVE_PATH)), (
        "runtime lock files must never become vault content"
    )
