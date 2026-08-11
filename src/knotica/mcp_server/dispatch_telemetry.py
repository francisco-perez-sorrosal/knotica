"""Dispatcher mis-selection telemetry for the operator action dispatchers.

The operator long-tail was collapsed into action-parameterized
dispatchers. The dissent on that consolidation is that an ``action`` enum can
reintroduce god-endpoint selection ambiguity. This module is the lightweight,
dependency-free instrument that keeps a future per-domain revert
*evidence-based*: it emits structured log lines for two signals —

1. every dispatcher invocation (``tool``/``action``/``topic``),
2. a dispatcher call rejected for an unknown ``action``.

Counting (2) per domain reveals selection ambiguity within a domain — a
signal that can justify reverting one dispatcher back to flat tools without
touching the other six.

A third signal covers the **billed two-phase actions** (``loop action=run_eval``,
``loop action=run_once``, ``gapfill_discover``). Signal (1) records only
tool/action/topic, which is identical for a free preview, a confirm that billed,
and a confirm whose nonce had gone stale and silently fell back to a preview.
Those three are the whole decision surface of a spending action, and a log that
cannot tell them apart cannot answer "did that click cost anything?" — a question
that took a live instrumented reproduction to settle once, because the log could
not.

**The persisted sink.** Log lines answer "what happened just now"; they cannot
answer "did the tool surface get worse between last month and this one", because
stderr is not kept. Every signal above therefore also appends one timestamped
JSONL record to an **opt-in** sink, enabled by pointing
:data:`SINK_DIR_ENV_VAR` at a directory. Three properties are deliberate:

* **Opt-in, and outside the vault.** Tool routing is a property of the *server's*
  tool surface, not of any one wiki, so the records are not vault data and must
  not fragment across configured vaults or vanish while the server is
  unconfigured — which is precisely the state in which a confused client is most
  worth measuring. Off by default because a default-on sink would silently write
  the maintainer's home directory on every test run and drown a capture window in
  suite traffic.
* **No root is threaded in.** The module resolves its own destination from the
  environment at write time, so it stays a stdlib-only leaf and wiring a new
  call site stays a one-line edit.
* **Best effort, always.** A sink failure is logged and swallowed. Telemetry
  never fails a tool call.

Records carry a five-value ``outcome`` (:data:`ROUTING_OUTCOMES`) — the closest
available proxy for "did the client route this call correctly". The vocabulary is
closed by construction: a caller may pass a raw knotica error code and the sink
buckets it. Two blind spots are structural and must not be papered over when
reading the data: a client that calls the **wrong tool entirely** is rejected by
MCP before any knotica code runs, and a client that simply calls knotica **less
often** is invisible to a per-invocation counter. Neither is measurable here.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = [
    "OUTCOME_CONFIRMED",
    "OUTCOME_PREVIEW",
    "OUTCOME_STALE_CONFIRM",
    "ROUTING_ERROR",
    "ROUTING_INVALID_ARGUMENT",
    "ROUTING_NOT_FOUND",
    "ROUTING_OK",
    "ROUTING_OUTCOMES",
    "ROUTING_TOPIC_NOT_FOUND",
    "SINK_DIR_ENV_VAR",
    "record_dispatch",
    "record_rejected_action",
    "record_two_phase",
    "sink_path",
]

_LOGGER = logging.getLogger(__name__)

#: Routing resolved to a handler (or the call succeeded, where the call site
#: records after the handler returns).
ROUTING_OK = "ok"
#: The call reached a handler and was refused on argument validation — the
#: sharpest in-domain mis-selection signal short of an unknown ``action``.
ROUTING_INVALID_ARGUMENT = "INVALID_ARGUMENT"
#: A page, suggestion or note the client named does not exist.
ROUTING_NOT_FOUND = "NOT_FOUND"
#: Broken out of :data:`ROUTING_NOT_FOUND` on purpose: an invented topic is the
#: single most diagnostic sign that a description change moved the client's model
#: of the vault.
ROUTING_TOPIC_NOT_FOUND = "TOPIC_NOT_FOUND"
#: Any other failure — house errors, git failures, an unconfigured server. Not a
#: routing signal; kept so the five buckets partition every call.
ROUTING_ERROR = "error"

#: The closed vocabulary every persisted record's ``outcome`` belongs to.
ROUTING_OUTCOMES = (
    ROUTING_OK,
    ROUTING_INVALID_ARGUMENT,
    ROUTING_NOT_FOUND,
    ROUTING_TOPIC_NOT_FOUND,
    ROUTING_ERROR,
)

#: Error codes that carry routing meaning. Everything else buckets to
#: :data:`ROUTING_ERROR`; an empty outcome buckets to :data:`ROUTING_OK`.
_CODE_OUTCOMES = MappingProxyType(
    {
        "INVALID_ARGUMENT": ROUTING_INVALID_ARGUMENT,
        "TOPIC_NOT_FOUND": ROUTING_TOPIC_NOT_FOUND,
        "PAGE_NOT_FOUND": ROUTING_NOT_FOUND,
        "SUGGESTION_NOT_FOUND": ROUTING_NOT_FOUND,
        "NOTE_NOT_FOUND": ROUTING_NOT_FOUND,
    }
)

#: Environment variable naming the directory the JSONL sink appends to. Unset or
#: empty means no file sink at all — the log lines above are then the only output.
SINK_DIR_ENV_VAR = "KNOTICA_TELEMETRY_DIR"

_SINK_SCHEMA_VERSION = 1
_SINK_FILE_TEMPLATE = "dispatch-%Y-%m-%d.jsonl"

#: Identifies the server process every record was emitted by. The server is
#: stateless and holds no session, so this is the closest available proxy for
#: one: it makes "how many sessions did this window cover" countable and
#: de-interleaves two servers writing the same day's file. Random per process
#: rather than the pid, which recycles. Carries nothing about the user.
_RUN_ID = uuid.uuid4().hex[:12]


def sink_path(now: datetime | None = None) -> Path | None:
    """The JSONL file this moment's records land in, or ``None`` when off.

    One file per UTC day, so a capture window is a contiguous set of whole files
    rather than a slice of one.
    """
    directory = os.environ.get(SINK_DIR_ENV_VAR, "").strip()
    if not directory:
        return None
    moment = now if now is not None else datetime.now(UTC)
    return Path(directory).expanduser() / moment.strftime(_SINK_FILE_TEMPLATE)


def _routing_outcome(outcome: str) -> str:
    """Bucket ``outcome`` into :data:`ROUTING_OUTCOMES`.

    Accepts either a routing label or a raw knotica error code, so a call site
    holding a ``KnoticaError`` needs no mapping table of its own.
    """
    cleaned = outcome.strip()
    if not cleaned:
        return ROUTING_OK
    if cleaned in ROUTING_OUTCOMES:
        return cleaned
    return _CODE_OUTCOMES.get(cleaned.upper(), ROUTING_ERROR)


def _append(event: str, fields: dict[str, Any]) -> None:
    """Append one record to the JSONL sink.

    Best effort: a write failure (missing directory, permissions, full disk) is
    logged and swallowed, because telemetry must never fail a tool call. A
    non-serializable field would still raise — that is a programming error in a
    caller, not a sink failure, and hiding it would make the sink lie silently.
    """
    now = datetime.now(UTC)
    path = sink_path(now)
    if path is None:
        return
    record = {
        "schema_version": _SINK_SCHEMA_VERSION,
        "ts": now.isoformat().replace("+00:00", "Z"),
        "run": _RUN_ID,
        "event": event,
        **fields,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        _LOGGER.warning("telemetry sink write failed path=%s error=%s", path, error)


def record_dispatch(dispatcher: str, action: str, topic: str, *, outcome: str = ROUTING_OK) -> None:
    """Log and persist a resolved invocation (per-domain adoption signal).

    ``outcome`` is the routing outcome **as known where this is called**. A call
    site that records before dispatching leaves the default, which asserts only
    that routing resolved to a handler; a call site that records after the
    handler returns passes the terminal result — a knotica error code is accepted
    directly and bucketed.
    """
    label = _routing_outcome(outcome)
    _LOGGER.info("dispatch tool=%s action=%s topic=%s outcome=%s", dispatcher, action, topic, label)
    _append("dispatch", {"tool": dispatcher, "action": action, "topic": topic, "outcome": label})


#: Phase 1 — a preview was minted. Nothing was billed.
OUTCOME_PREVIEW = "preview"
#: Phase 2 — the nonce matched and execution was reached. This is the billing leg.
OUTCOME_CONFIRMED = "confirmed"
#: A confirm arrived whose nonce did not match, was expired, or was already
#: consumed. It falls back to a fresh preview and bills nothing — indistinguishable
#: from a successful confirm at the tool surface, which is exactly why it is
#: logged distinctly.
OUTCOME_STALE_CONFIRM = "stale-confirm"


def record_two_phase(dispatcher: str, action: str, topic: str, *, outcome: str) -> None:
    """Log one leg of a billed two-phase action.

    ``outcome`` is one of :data:`OUTCOME_PREVIEW`, :data:`OUTCOME_CONFIRMED`, or
    :data:`OUTCOME_STALE_CONFIRM`. Emitted at ``warning`` for a stale confirm --
    the user believed they were spending and nothing ran, so it is the one leg
    worth surfacing above ``info`` in a default log configuration.

    The persisted record keeps the billing leg in its own ``phase`` field rather
    than in ``outcome``: the two vocabularies answer different questions, and
    collapsing them would make "how often did routing fail" uncountable.
    """
    level = logging.WARNING if outcome == OUTCOME_STALE_CONFIRM else logging.INFO
    billed = outcome == OUTCOME_CONFIRMED
    _LOGGER.log(
        level,
        "two-phase tool=%s action=%s topic=%s outcome=%s billed=%s",
        dispatcher,
        action,
        topic,
        outcome,
        billed,
    )
    _append(
        "two_phase",
        {
            "tool": dispatcher,
            "action": action,
            "topic": topic,
            "outcome": ROUTING_OK,
            "phase": outcome,
            "billed": billed,
        },
    )


def record_rejected_action(dispatcher: str, action: str, valid_actions: tuple[str, ...]) -> None:
    """Log and persist a call rejected for an unknown ``action`` (ambiguity signal).

    The rejected action is echoed verbatim so the persisted record shows *what*
    the client reached for; no topic is in scope this early, and the field is
    written empty rather than omitted to keep the sink's shape uniform.
    """
    _LOGGER.warning(
        "dispatch-rejected tool=%s action=%r valid=%s",
        dispatcher,
        action,
        "|".join(valid_actions),
    )
    _append(
        "rejected",
        {
            "tool": dispatcher,
            "action": action,
            "topic": "",
            "outcome": ROUTING_INVALID_ARGUMENT,
            "valid_actions": list(valid_actions),
        },
    )
