"""Loop-runner heartbeat — runtime liveness signal for status surfaces.

The watch loop beats a small JSON file under ``.knotica/locks/`` (the vault's
gitignored runtime directory, same home as the mutation lock) so ``wiki_status``
can report whether a runner process is actually watching the topic. This is
machine-local runtime state, never vault content: plain filesystem writes, no
``VaultStore``, no git, no lock contention.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

__all__ = [
    "HEARTBEAT_STALE_FACTOR",
    "clear_heartbeat",
    "read_runner_liveness",
    "write_heartbeat",
]

#: A runner is considered dead once its last beat is older than this many
#: watch intervals (beats happen once per poll tick).
HEARTBEAT_STALE_FACTOR = 3.0

_LOCKS_DIR = PurePath(".knotica/locks")


def _heartbeat_path(vault_root: Path, topic: str) -> Path:
    safe_topic = topic.strip().strip("/").replace("/", "-") or "vault"
    return vault_root / _LOCKS_DIR / f"loop-runner-{safe_topic}.json"


def write_heartbeat(vault_root: Path, topic: str, *, interval_seconds: float) -> None:
    """Record one watch tick (atomic replace; parent dirs created lazily)."""
    path = _heartbeat_path(vault_root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "topic": topic,
        "interval_seconds": float(interval_seconds),
        "beat_at": datetime.now(UTC).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def clear_heartbeat(vault_root: Path, topic: str) -> None:
    """Remove the heartbeat on clean runner shutdown (missing file is fine)."""
    _heartbeat_path(vault_root, topic).unlink(missing_ok=True)


def read_runner_liveness(
    vault_root: Path, topic: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Liveness readout for status surfaces: ``{alive, pid, beat_at, interval_seconds}``.

    ``alive`` is False when no heartbeat exists, the file is unreadable, or the
    last beat is older than :data:`HEARTBEAT_STALE_FACTOR` intervals (a crashed
    runner leaves a stale file behind; staleness, not existence, is the signal).
    """
    dead: dict[str, Any] = {"alive": False, "pid": None, "beat_at": None, "interval_seconds": None}
    path = _heartbeat_path(vault_root, topic)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        beat_at = datetime.fromisoformat(str(payload["beat_at"]))
        interval = float(payload.get("interval_seconds") or 2.0)
    except (OSError, ValueError, KeyError, TypeError):
        return dead
    current = now if now is not None else datetime.now(UTC)
    age = (current - beat_at).total_seconds()
    alive = age <= max(1.0, interval * HEARTBEAT_STALE_FACTOR)
    return {
        "alive": alive,
        "pid": payload.get("pid"),
        "beat_at": payload["beat_at"],
        "interval_seconds": interval,
    }
