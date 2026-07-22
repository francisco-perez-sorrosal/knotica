"""Dispatcher mis-selection telemetry for the seven action dispatchers.

The operator long-tail was collapsed into seven action-parameterized
dispatchers. The dissent on that consolidation is that an ``action`` enum can
reintroduce god-endpoint selection ambiguity. This module is the lightweight,
dependency-free instrument that keeps a future per-domain revert
*evidence-based*: it emits structured log lines for two signals —

1. every dispatcher invocation (``tool``/``action``/``topic``),
2. a dispatcher call rejected for an unknown ``action``.

Counting (2) per domain reveals selection ambiguity within a domain — a
signal that can justify reverting one dispatcher back to flat tools without
touching the other six.
"""

from __future__ import annotations

import logging

__all__ = [
    "record_dispatch",
    "record_rejected_action",
]

_LOGGER = logging.getLogger(__name__)


def record_dispatch(dispatcher: str, action: str, topic: str) -> None:
    """Log a resolved dispatcher invocation (per-domain adoption signal)."""
    _LOGGER.info("dispatch tool=%s action=%s topic=%s", dispatcher, action, topic)


def record_rejected_action(dispatcher: str, action: str, valid_actions: tuple[str, ...]) -> None:
    """Log a dispatcher call rejected for an unknown ``action`` (ambiguity signal)."""
    _LOGGER.warning(
        "dispatch-rejected tool=%s action=%r valid=%s",
        dispatcher,
        action,
        "|".join(valid_actions),
    )
