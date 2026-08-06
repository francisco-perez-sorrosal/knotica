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
"""

from __future__ import annotations

import logging

__all__ = [
    "OUTCOME_CONFIRMED",
    "OUTCOME_PREVIEW",
    "OUTCOME_STALE_CONFIRM",
    "record_dispatch",
    "record_rejected_action",
    "record_two_phase",
]

_LOGGER = logging.getLogger(__name__)


def record_dispatch(dispatcher: str, action: str, topic: str) -> None:
    """Log a resolved dispatcher invocation (per-domain adoption signal)."""
    _LOGGER.info("dispatch tool=%s action=%s topic=%s", dispatcher, action, topic)


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
    """
    level = logging.WARNING if outcome == OUTCOME_STALE_CONFIRM else logging.INFO
    _LOGGER.log(
        level,
        "two-phase tool=%s action=%s topic=%s outcome=%s billed=%s",
        dispatcher,
        action,
        topic,
        outcome,
        outcome == OUTCOME_CONFIRMED,
    )


def record_rejected_action(dispatcher: str, action: str, valid_actions: tuple[str, ...]) -> None:
    """Log a dispatcher call rejected for an unknown ``action`` (ambiguity signal)."""
    _LOGGER.warning(
        "dispatch-rejected tool=%s action=%r valid=%s",
        dispatcher,
        action,
        "|".join(valid_actions),
    )
