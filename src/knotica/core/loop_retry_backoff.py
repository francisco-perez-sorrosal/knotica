"""How long a *failed* observation eval waits before it is re-attempted.

The loop deliberately leaves its cursor unadvanced when an observation eval
raises, so the same content is tried again rather than silently skipped. How
soon "again" should be depends entirely on whether the failure can succeed on a
retry at all:

- **Transient** -- a rate-limited call, a flaky clone. Retrying in a minute is
  exactly right; that is what the re-arm design is for.
- **Blocked** -- a precondition is missing (no frozen golden set, no
  credential). No number of retries fixes it; only an operator action does.
  Retrying such a failure on the transient floor buys nothing and costs two
  bookkeeping commits per attempt (``observing …`` then ``observation eval
  error …``). Left unchecked that is minute-by-minute commit growth for as long
  as the precondition is unmet.

The distinction is not inferred from the error's message. ``KnoticaError``
already publishes a ``retryable`` contract (``NOT_CONFIGURED`` is not
retryable), so this module reads that claim and lets anything that makes no
claim be treated as transient -- the safe default, since wrongly calling a
transient failure "blocked" would stall real recovery for an hour.

A pure leaf: no I/O, no vault access, no imports from the loop it serves.
"""

from __future__ import annotations

__all__ = [
    "BLOCKED_RETRY_FLOOR_SECONDS",
    "FAILURE_RETRY_FLOOR_SECONDS",
    "is_retryable_failure",
    "retry_floor_seconds",
]

#: Floor between retries of a *transient* failing observation eval. Without it,
#: a persistently-failing eval retries every loop tick (5-30s) indefinitely.
FAILURE_RETRY_FLOOR_SECONDS = 60

#: Floor between retries of a failure the error reports as NOT retryable. Long,
#: but deliberately not infinite: the operator action that unblocks a topic
#: (freezing a golden set) writes under ``<topic>/.knotica/``, which the loop's
#: content-change check ignores by design -- so a never-expiring block would
#: outlive its own cause and need a restart to clear.
BLOCKED_RETRY_FLOOR_SECONDS = 3600


def is_retryable_failure(exc: BaseException) -> bool:
    """Whether ``exc`` claims a retry could succeed without operator action.

    Reads the raised error's own ``retryable`` contract. An exception that
    publishes no such attribute makes no claim, and is treated as transient.
    """
    return bool(getattr(exc, "retryable", True))


def retry_floor_seconds(*, retryable: bool) -> int:
    """Seconds to wait before re-attempting a failed observation eval."""
    return FAILURE_RETRY_FLOOR_SECONDS if retryable else BLOCKED_RETRY_FLOOR_SECONDS
