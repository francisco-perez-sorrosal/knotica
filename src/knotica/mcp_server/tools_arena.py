"""Arena status / history payload helpers for the ``arena`` action dispatcher.

These functions have no MCP tool registrations of their own — they are
imported directly by ``tools_dispatch_arena.py``, the sole entry point into
this logic.
"""

from __future__ import annotations

from typing import Any

from knotica.core.arena import (
    ArenaState,
    read_arena_history,
    read_arena_state,
)
from knotica.core.page import TopicNotFoundError
from knotica.mcp_server import envelope
from knotica.store import VaultStore


def _status_payload(store: VaultStore, topic: str) -> dict[str, Any]:
    cleaned = _clean_topic(topic)
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    state = read_arena_state(store, cleaned)
    if state is None:
        empty = ArenaState(topic=cleaned)
        return envelope.read_ok(empty.render())
    return envelope.read_ok(state.render())


def _history_payload(store: VaultStore, topic: str, *, limit: int) -> dict[str, Any]:
    cleaned = _clean_topic(topic)
    if not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    clamped = max(1, min(int(limit), 100))
    return envelope.read_ok(
        {
            "topic": cleaned,
            "races": read_arena_history(store, cleaned, limit=clamped),
            "limit": clamped,
        }
    )


def _clean_topic(topic: str) -> str:
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise TopicNotFoundError(topic or "(empty)")
    return cleaned
