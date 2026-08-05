"""Observation-attempt identity — whether an attempt records anything new.

An observation eval that raises used to cost the vault a *pair* of commits
regardless of outcome: ``evaluating`` before the eval, ``failed`` after. Both
differ from the stored state, so nothing suppressed a write that carried no new
information -- and because the failure path deliberately leaves the cursor
unadvanced, a permanent failure re-attempted forever and paid a pair every time.
One live vault reached 14,845 commits, of which roughly 14 were content.

:mod:`knotica.core.loop_retry_backoff` paced the *known* instance of that down
by reading the error's own ``retryable`` contract, so a missing golden set waits
an hour instead of a minute. It did not close the family: a permanent failure
raised as a bare ``Exception`` publishes no such claim and is treated as
transient by design -- misclassifying a real transient would stall recovery for
an hour -- so it still paces at a minute and grows history at the original rate.
Pacing bounds how *often* a no-information write happens; only identity can stop
it happening at all. This module owns that identity, and the retry pacing that
now depends on it.

**When two attempts are materially identical.** Both of these must hold:

1. They evaluated the same *content*. Compared by content equality, never by sha
   equality: the loop's own bookkeeping commits move the default branch's HEAD
   between attempts even when nothing a human wrote has changed.
2. Every :class:`~knotica.core.loop_state.LoopState` field outside
   :data:`_VOLATILE_FIELDS` is equal.

Rule 2 is a **deny-list, not an allow-list** -- anything that is not explicitly
a timestamp counts as information, so a field added to ``LoopState`` later
participates automatically. The asymmetry is deliberate: the worst case of an
identity that is too narrow is a redundant commit, while an identity that is too
broad silently swallows a *new* failure, which is a debugging catastrophe. Bias
toward precision.

**What stays observable while a write is suppressed.** Runner liveness and
in-flight eval progress already report through ``.knotica/locks/`` -- gitignored
runtime files with no git involved (:mod:`knotica.core.loop_heartbeat`,
:mod:`knotica.core.loop_progress`). Suppressing a commit costs no visibility:
``wiki_status`` still shows a live runner and its per-question progress, against
the last *recorded* verdict, which is still exactly true.

**Why the attempt clock lives here and not on the state.** Retry pacing used to
read ``LoopState.last_eval_started_at``, which only advances when a state write
happens -- so suppressing the write would have released the retry floor on every
tick and traded commit spam for far more expensive *eval* spam. The clock is
therefore a gitignored runtime marker next to the heartbeat, advanced on every
attempt whether or not that attempt is recorded. It cannot live in process
memory instead: the supervised daemon (``knotica.service.manager``) builds a
fresh ``LoopRunner`` every cycle, so in-process state would be empty on every
tick in production.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from knotica.core.loop_retry_backoff import is_retryable_failure, retry_floor_seconds
from knotica.core.loop_state import LoopStage, LoopState, write_loop_state
from knotica.store import VaultStore

__all__ = [
    "is_same_content_retry",
    "note_attempt",
    "record_failed_attempt",
    "records_nothing_new",
    "retry_hold",
]

_LOCKS_DIR = PurePath(".knotica/locks")

#: Fields that time-stamp an attempt rather than describe one. Two states
#: differing only here describe the same situation.
_VOLATILE_FIELDS = frozenset({"updated_at", "last_eval_started_at"})

#: The head an attempt ran against. Excluded from the field comparison because
#: rule 1 compares it by content instead; leaving the stored value in place also
#: preserves the *first* head that exhibited the failure, which is the more
#: useful thing for a human reading the history.
_CONTENT_ANCHOR_FIELD = "candidate_sha"

_IGNORED_FIELDS = _VOLATILE_FIELDS | {_CONTENT_ANCHOR_FIELD}


def _described_state(state: LoopState) -> dict[str, Any]:
    """The state's *informative* fields — everything that is not a timestamp."""
    return {
        name: value
        for name, value in state.model_dump(mode="json").items()
        if name not in _IGNORED_FIELDS
    }


def records_nothing_new(stored: LoopState, attempt: LoopState, *, same_content: bool) -> bool:
    """Whether persisting ``attempt`` would tell a reader nothing ``stored`` does not."""
    return same_content and _described_state(stored) == _described_state(attempt)


def is_same_content_retry(
    state: LoopState, head: str, *, content_changed: Callable[[str, str], bool]
) -> bool:
    """Whether this tick re-attempts the exact content whose eval last failed.

    ``False`` for a brand-new content change (nothing is pending) and for content
    that genuinely differs from the head that failed -- only a same-content retry
    is a repeat.
    """
    if not state.pending_retry or state.candidate_sha is None:
        return False
    return not content_changed(state.candidate_sha, head)


def record_failed_attempt(
    store: VaultStore,
    vault_root: Path,
    *,
    stored: LoopState,
    attempt: LoopState,
    branch: str,
    head: str,
    exc: BaseException,
    same_content: bool,
) -> LoopState:
    """Persist the failed-attempt state, unless it would record nothing new.

    ``candidate_sha`` is kept at the failing head rather than nulled so a later
    tick can tell a same-content retry from a genuinely new content change, and
    ``pending_retry`` re-arms the cursor the caller deliberately left unadvanced.
    Returns the state now in effect: the persisted copy when the attempt was
    recorded, otherwise ``stored`` unchanged.
    """
    failed = attempt.model_copy(
        update={
            "stage": LoopStage.failed,
            "last_error": str(exc),
            "candidate_branch": branch,
            "candidate_sha": head,
            "pending_retry": True,
            "last_failure_retryable": is_retryable_failure(exc),
        }
    )
    if records_nothing_new(stored, failed, same_content=same_content):
        return stored
    return write_loop_state(store, vault_root, failed, title=f"observation eval error on {branch}")


def retry_hold(
    vault_root: Path,
    topic: str,
    state: LoopState,
    *,
    same_content_retry: bool,
    now: datetime,
) -> str | None:
    """Reason to defer a retry of a *failing* observation eval, or ``None``.

    Always-on and independent of ``eval_min_interval_hours``/``eval_window``: the
    only things it consults are whether this tick repeats the content that failed
    and how long ago the last attempt actually started. The floor itself is
    chosen by :func:`~knotica.core.loop_retry_backoff.retry_floor_seconds` from
    the kind of failure recorded on the state.
    """
    if not same_content_retry:
        return None
    elapsed_seconds = _seconds_since_last_attempt(
        vault_root, topic, now=now, fallback=state.last_eval_started_at
    )
    if elapsed_seconds is None:
        return None
    floor = retry_floor_seconds(retryable=state.last_failure_retryable)
    if elapsed_seconds >= floor:
        return None
    kind = "failure" if state.last_failure_retryable else "blocked"
    return f"{kind} retry held: {elapsed_seconds:.0f}s since last eval attempt < {floor}s floor"


def _attempt_path(vault_root: Path, topic: str) -> Path:
    safe_topic = topic.strip().strip("/").replace("/", "-") or "vault"
    return vault_root / _LOCKS_DIR / f"loop-attempt-{safe_topic}.json"


def note_attempt(vault_root: Path, topic: str, *, at: datetime) -> None:
    """Record that an observation eval attempt started at ``at`` (atomic replace).

    ``at`` is supplied by the runner's own clock rather than read from the wall
    clock here, so the marker stays comparable with the timestamps the loop
    already keeps -- and so tests remain deterministic.
    """
    path = _attempt_path(vault_root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"topic": topic, "started_at": at.isoformat()}), encoding="utf-8")
    os.replace(tmp, path)


def _seconds_since_last_attempt(
    vault_root: Path, topic: str, *, now: datetime, fallback: datetime | None
) -> float | None:
    """Seconds since the most recent attempt start, or ``None`` when unknown.

    Prefers the runtime marker, which advances on every attempt including the
    ones whose state write is suppressed, and falls back to the persisted
    ``last_eval_started_at`` when no usable marker exists -- a fresh machine, a
    cleared ``.knotica/locks/``. A candidate whose awareness disagrees with
    ``now`` is skipped rather than guessed at: naive and aware datetimes cannot
    be subtracted, and the fallback is the safer of the two answers.
    """
    for started in (_marker_started_at(vault_root, topic), fallback):
        if started is None or (started.tzinfo is None) != (now.tzinfo is None):
            continue
        return (now - started).total_seconds()
    return None


def _marker_started_at(vault_root: Path, topic: str) -> datetime | None:
    """The marker's recorded start instant, or ``None`` when absent/unreadable."""
    try:
        payload = json.loads(_attempt_path(vault_root, topic).read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(payload["started_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
