"""The vault's one-writer seam -- ``VaultTransaction``, the single mutation path.

Every mutating operation (MCP tool, CLI command, future headless loop) reaches
the vault through exactly one code path: an operation in ``core.operations``
opens a :class:`VaultTransaction`, declares its file writes, and exits. The
transaction choreographs lock, scrub, atomic writes, the operation-log append,
and exactly one path-scoped git commit. Because there is only one writer, the
audit invariant "one git commit per effective mutating operation" holds for
every surface at once -- adapters never write the vault directly.

Invariants (each is load-bearing; the module structure makes them evident):

* **Lock brackets the transaction, and only fs+git happen inside it.** The
  exclusive vault flock is acquired at ``__enter__`` and released at
  ``__exit__`` -- always, on every path. Callers must finish all fetching,
  network, and LLM work *before* entering; the transaction offers no hook
  through which slow work could run under the lock, so a concurrent session
  never starves waiting on someone else's download.
* **Writes are buffered, then applied at exit.** :meth:`VaultTransaction.write`
  only records intent (scrubbing the content as it does). Nothing touches the
  filesystem until the block exits cleanly, which keeps the window between the
  first byte written and the commit as small as possible.
* **The touched-paths list is the single source for both commit and rollback.**
  ``_finalize`` appends each path to one list *as it is physically written*;
  the commit stages exactly that list, and a failure rolls back exactly that
  list. Commit scope and rollback scope cannot diverge because they are the
  same variable.
* **Foreign content is invisible.** Staging and rollback are path-scoped via
  :class:`~knotica.core.vcs.VaultVcs` (which has no add-all and no
  ``reset --hard`` at all), so a concurrent edit to any file the transaction
  did not write -- an open Obsidian note, a manual ``git add`` -- is neither
  swept into the knotica commit nor destroyed by a knotica rollback.
* **Idempotency by result-state.** A declared write whose scrubbed content is
  byte-identical to the file already in the vault is not a change. When no
  declared write changes anything, the transaction exits with
  ``changed=False``, writes no log entry, and makes **zero** commits -- so
  retrying an operation after a transport failure never dirties the audit log.
* **Redaction is always loud.** Content passes through
  :func:`~knotica.core.scrub.scrub` at declaration time; every redaction is
  reported on the result (with spans located in the caller's original text),
  even when the transaction turns out to be a no-op.

The transaction knows nothing about MCP, the CLI, or config resolution: it
takes an already-resolved vault root and an already-constructed store. Failure
mapping owned here: lock contention raises ``KnoticaError(LOCK_BUSY)`` and git
failures raise ``KnoticaError(GIT_ERROR)``; any other exception propagates
unchanged after the rollback has restored the transaction's own paths.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from types import TracebackType
from typing import Literal, NoReturn

from knotica.core.errors import ErrorCode, KnoticaError, KnoticaWarning, secret_scrubbed_warning
from knotica.core.lock import DEFAULT_ACQUIRE_TIMEOUT_SECONDS, LockBusyError, vault_lock
from knotica.core.records import LogEntry, format_commit_subject, format_log_entry
from knotica.core.scrub import RedactedSpan, scrub
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

#: Vault-relative path of the operation log the transaction appends to.
LOG_PATH = "log.md"


@dataclass(frozen=True)
class RedactedWrite:
    """All redactions applied to one declared write.

    Attributes:
        path: The vault-relative path the write was declared for.
        spans: The redacted spans, located in the caller's *original* content
            (see :class:`~knotica.core.scrub.RedactedSpan`).
    """

    path: str
    spans: tuple[RedactedSpan, ...]


@dataclass(frozen=True)
class TransactionResult:
    """The outcome of a completed transaction, for the operations layer to envelope.

    Attributes:
        changed: Whether the vault changed. ``False`` means the transaction
            was a no-op: identical content, no log entry, no commit.
        commit_sha: The vault ``HEAD`` after the transaction -- the new commit
            when ``changed`` is true, the pre-existing head when it is not
            (so a retried operation returns the sha of the state it matched).
        touched_paths: The declared paths whose bytes actually changed, in
            declaration order. Excludes the operation log, which is committed
            alongside every effective transaction; empty for a no-op.
        redactions: Every declared write whose content was redacted by the
            secret scrub -- reported even for no-op writes, so a redaction is
            never silent.
    """

    changed: bool
    commit_sha: str
    touched_paths: tuple[str, ...]
    redactions: tuple[RedactedWrite, ...]

    def warnings(self) -> tuple[KnoticaWarning, ...]:
        """Render the redactions as secret-scrub warnings, one per redacted write.

        Ready for the operations layer to attach to its success envelope via
        ``ok(..., warnings=result.warnings())``. Empty when nothing was
        redacted.
        """
        return tuple(
            secret_scrubbed_warning(_redaction_message(redaction)) for redaction in self.redactions
        )


def _redaction_message(redaction: RedactedWrite) -> str:
    """Name every redacted span of one write, without carrying the secret."""
    located = ", ".join(f"{span.pattern} (line {span.line})" for span in redaction.spans)
    return f"Content written to {redaction.path} had secrets redacted: {located}."


class VaultTransaction:
    """Context manager for one mutating vault operation -- the only writer.

    Usage::

        with VaultTransaction(store, vault_root, op, topic, title) as txn:
            txn.write("agentic-systems/react.md", page_content)
            txn.write("index.md", updated_index)
        result = txn.result   # changed / commit_sha / touched_paths / redactions

    The block body must declare writes and nothing else -- all slow work
    (fetching, LLM calls) happens *before* entering, because the vault lock is
    held for the whole block. An exception raised inside the block leaves the
    vault untouched (writes are buffered) and releases the lock.

    Args:
        store: The vault storage backend; the transaction's only write surface
            for file content (atomic temp+rename writes, path confinement).
        vault_root: The already-resolved vault root directory. The transaction
            has no config knowledge; callers resolve the vault first. Must be
            the same root ``store`` is confined to.
        op: The operation name for the log entry and commit subject
            (lowercase letters/underscores, e.g. ``"write_page"``).
        topic: The topic slot of the log entry and commit subject.
        title: The human-readable title slot.
        lock_timeout: Maximum seconds to wait for the vault lock before
            failing with the retryable lock-busy error.

    Raises:
        ValueError: At construction, when ``op``/``topic``/``title`` violate
            the frozen commit-subject grammar (fail fast, before any lock).
        KnoticaError: With code ``LOCK_BUSY`` when the lock is contended past
            the timeout, or ``GIT_ERROR`` when a git step fails at exit.
    """

    def __init__(
        self,
        store: VaultStore,
        vault_root: str | PurePath,
        op: str,
        topic: str,
        title: str,
        *,
        lock_timeout: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
    ) -> None:
        self._store = store
        self._vcs = VaultVcs(vault_root)
        # Rendering the subject now validates op/topic/title against the
        # frozen grammar before any lock is taken or byte is written.
        self._commit_subject = format_commit_subject(op, topic, title)
        self._op = op
        self._topic = topic
        self._title = title
        self._lock_timeout = lock_timeout
        self._writes: dict[str, str] = {}
        self._redactions: dict[str, tuple[RedactedSpan, ...]] = {}
        self._lock: AbstractContextManager[None] | None = None
        self._active = False
        self._result: TransactionResult | None = None

    def __enter__(self) -> "VaultTransaction":
        if self._active or self._result is not None:
            raise RuntimeError("VaultTransaction is single-use; create a new one per operation")
        lock = vault_lock(self._vcs.root, timeout=self._lock_timeout)
        try:
            lock.__enter__()
        except LockBusyError as error:
            raise KnoticaError(ErrorCode.LOCK_BUSY, str(error)) from error
        self._lock = lock
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self._active = False
        try:
            if exc_type is None:
                self._result = self._finalize()
        finally:
            # Release-last discipline: whatever happened above -- success,
            # rollback, or a raise from either -- the lock always releases.
            lock, self._lock = self._lock, None
            if lock is not None:
                lock.__exit__(None, None, None)
        return False

    def write(self, path: str | PurePath, content: str) -> None:
        """Declare one full-content file write (buffered; applied at exit).

        Content is secret-scrubbed immediately and the scrubbed form is what
        the vault will store; redacted spans surface on the result. Declaring
        the same path again replaces the earlier declaration (last one wins).

        Args:
            path: Vault-relative target path. The operation log is refused --
                the transaction is its only writer, and a second append path
                would break the one-entry-per-operation invariant.
            content: The full new file content (UTF-8 text).

        Raises:
            RuntimeError: When called outside the ``with`` block.
            ValueError: For absolute paths or the reserved log path.
        """
        if not self._active:
            raise RuntimeError("write() is only valid inside the transaction's `with` block")
        normalized = _normalize_write_path(path)
        scrubbed, spans = scrub(content)
        self._writes[normalized] = scrubbed
        if spans:
            self._redactions[normalized] = tuple(spans)
        else:
            self._redactions.pop(normalized, None)

    @property
    def result(self) -> TransactionResult:
        """The transaction outcome; available only after a clean exit."""
        if self._result is None:
            raise RuntimeError("result is available only after the transaction exits cleanly")
        return self._result

    def _finalize(self) -> TransactionResult:
        """Apply the buffered writes: idempotency check, log append, one commit.

        Runs under the lock. On any failure after the first physical write,
        rolls back exactly the paths written so far and re-raises.
        """
        changed = {
            path: content for path, content in self._writes.items() if self._differs(path, content)
        }
        redactions = tuple(RedactedWrite(path, spans) for path, spans in self._redactions.items())
        if not changed:
            return TransactionResult(
                changed=False,
                commit_sha=self._vcs.head_sha(),
                touched_paths=(),
                redactions=redactions,
            )
        pre_head = self._vcs.head_sha()
        # Rendered before any write so a log-grammar violation aborts the
        # transaction while the vault is still untouched.
        log_content = self._appended_log(pages=tuple(changed))
        # The one list both the commit and the rollback are scoped to: each
        # path is appended the moment it is physically written, never before.
        touched: list[str] = []
        try:
            for path, content in changed.items():
                self._store.write_text_atomic(path, content)
                touched.append(path)
            self._store.write_text_atomic(LOG_PATH, log_content)
            touched.append(LOG_PATH)
            commit_sha = self._vcs.commit_paths(touched, self._commit_subject)
        except BaseException as error:
            self._rollback_and_raise(touched, pre_head, error)
        return TransactionResult(
            changed=True,
            commit_sha=commit_sha,
            touched_paths=tuple(changed),
            redactions=redactions,
        )

    def _rollback_and_raise(
        self, touched: list[str], pre_head: str, error: BaseException
    ) -> NoReturn:
        """Restore exactly the transaction's own writes, then surface ``error``.

        Rollback is scoped to ``touched`` -- the same list the commit would
        have staged -- so files the transaction never wrote are untouchable
        by construction. Git failures leave as ``KnoticaError(GIT_ERROR)``;
        everything else re-raises unchanged.
        """
        try:
            if touched:
                self._vcs.rollback_paths(touched, pre_head)
        except GitError as rollback_error:
            raise KnoticaError(
                ErrorCode.GIT_ERROR,
                f"The {self._op} operation failed ({error}) and rolling back its own "
                f"writes also failed: {rollback_error}. The vault may hold uncommitted "
                f"changes to: {', '.join(touched)}.",
            ) from rollback_error
        if isinstance(error, GitError):
            raise KnoticaError(
                ErrorCode.GIT_ERROR,
                f"The {self._op} operation's git step failed: {error}. "
                "Its writes were rolled back; the vault is unchanged.",
            ) from error
        raise error

    def _differs(self, path: str, content: str) -> bool:
        """Whether writing ``content`` to ``path`` would change vault bytes."""
        if not self._store.exists(path):
            return True
        return self._store.read_text(path) != content

    def _appended_log(self, pages: tuple[str, ...]) -> str:
        """The full new log content: existing log plus this operation's entry.

        The entry follows the frozen grammar (H2 line plus one bullet per
        touched page) and is separated from the existing content by exactly
        one blank line, matching the template's entry rhythm.
        """
        entry = format_log_entry(
            LogEntry(
                date=datetime.now(UTC).strftime("%Y-%m-%d"),
                op=self._op,
                topic=self._topic,
                title=self._title,
                pages=pages,
            )
        )
        existing = self._store.read_text(LOG_PATH) if self._store.exists(LOG_PATH) else ""
        if not existing.strip():
            return entry
        return existing.rstrip("\n") + "\n\n" + entry


def _normalize_write_path(path: str | PurePath) -> str:
    """Normalize a declared write path to a vault-relative POSIX string.

    Rejects absolute paths (the store re-checks full confinement, symlinks
    included, at apply time) and the operation log (owned by the transaction).
    """
    pure = PurePath(path)
    if pure.is_absolute():
        raise ValueError(f"Vault paths must be relative to the vault root: {path}")
    normalized = pure.as_posix()
    if normalized == LOG_PATH:
        raise ValueError(
            f"{LOG_PATH} is maintained by the transaction itself; it cannot be a write target"
        )
    return normalized
