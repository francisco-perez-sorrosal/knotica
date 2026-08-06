"""Single-use confirmation nonces for two-phase billed tool actions.

The server is stateless, so "the user already confirmed this" cannot live in
process memory between two tool calls. It lives in a nonce file instead: phase 1
mints one and returns a decision envelope that bills nothing; phase 2 presents
it back and the action runs. One file per ``(kind, topic)`` pair, so concurrent
billed actions never collide.

Extracted from ``tools_vault`` when a third caller appeared. It was already the
shared mechanism behind ``loop action=run_eval`` and ``loop action=run_once``;
``gapfill_discover`` made it a seam two *different* tool modules need, and
reaching into another module's privates for it is worse than naming it.

These files live under the gitignored ``.knotica/locks/`` runtime directory --
never vault content, never a :class:`~knotica.store.VaultStore` write, never a
git commit. They are the daemon-marker exception the stateless-server invariant
already scopes, not a widening of it.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

__all__ = ["NONCE_TTL_SECONDS", "consume", "mint", "nonce_path"]

#: Single-use nonce lifetime. Long enough for a human to read a cost estimate
#: and decide; short enough that an abandoned confirmation cannot be replayed
#: hours later against a vault that has moved on.
NONCE_TTL_SECONDS = 300.0

#: Runtime (gitignored) directory the nonce file lives in -- same home as the
#: loop heartbeat and vault mutation lock.
_LOCKS_DIR = PurePath(".knotica/locks")


def nonce_path(vault_path: Path, kind: str, topic: str) -> Path:
    """Nonce file location for one billed action ``kind`` and ``topic``."""
    safe_topic = topic.replace("/", "-") or "vault"
    return vault_path / _LOCKS_DIR / f"{kind}-nonce-{safe_topic}.json"


def mint(vault_path: Path, kind: str, topic: str, extra: dict[str, Any]) -> str:
    """Mint + persist a single-use nonce for a billed action.

    ``extra`` is echoed back by :func:`consume`, so phase 2 can act on exactly
    the parameters phase 1 quoted a cost for rather than re-deriving them from a
    vault that may have changed in between.
    """
    nonce = secrets.token_urlsafe(16)
    path = nonce_path(vault_path, kind, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nonce": nonce,
        "topic": topic,
        "minted_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    return nonce


def consume(vault_path: Path, kind: str, topic: str, confirm: str) -> dict[str, Any] | None:
    """Verify + consume a single-use nonce; returns the minted payload or ``None``.

    The nonce file is deleted unconditionally on read (single-use, no probing a
    live nonce by sending a wrong ``confirm`` value) -- a mismatch or expiry
    falls through to phase 1, minting a fresh nonce.
    """
    path = nonce_path(vault_path, kind, topic)
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    path.unlink(missing_ok=True)
    if not isinstance(payload, dict):
        return None
    if payload.get("nonce") != confirm:
        return None
    try:
        minted_at = datetime.fromisoformat(str(payload["minted_at"]))
    except (KeyError, ValueError):
        return None
    age = (datetime.now(UTC) - minted_at).total_seconds()
    if age > NONCE_TTL_SECONDS:
        return None
    return payload
