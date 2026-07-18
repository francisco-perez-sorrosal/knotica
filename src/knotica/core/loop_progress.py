"""In-flight eval progress — runtime visibility for status surfaces.

A long observation eval (25 golden questions, minutes) is otherwise a black
box between ``evaluating`` and its verdict. The evaluate path overwrites one
small JSON file under ``.knotica/locks/`` (gitignored runtime, same home as
the heartbeat) once per example; ``wiki_status`` reads it per poll so the
dashboard can show "question 7/25" instead of a frozen stage card. Plain
filesystem writes — no ``VaultStore``, no git, no commits.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

__all__ = ["clear_progress", "read_progress", "write_progress"]

_LOCKS_DIR = PurePath(".knotica/locks")

#: A progress entry older than this is a leftover from a dead run, not news.
_STALE_AFTER_SECONDS = 15 * 60.0


def _progress_path(vault_root: Path, topic: str) -> Path:
    safe_topic = topic.strip().strip("/").replace("/", "-") or "vault"
    return vault_root / _LOCKS_DIR / f"loop-progress-{safe_topic}.json"


def write_progress(
    vault_root: Path,
    topic: str,
    *,
    phase: str,
    current: int = 0,
    total: int = 0,
    detail: str = "",
    substage: str = "",
    sub_current: int = 0,
    sub_total: int = 0,
) -> None:
    """Overwrite the in-flight progress entry (atomic replace).

    ``substage`` refines the per-question phase ("answering", "judging"), with
    ``sub_current``/``sub_total`` counting judge samples when they actually run
    (a warm judge-cache hit draws no samples and reports none).
    """
    path = _progress_path(vault_root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "current": int(current),
        "total": int(total),
        "detail": detail[:200],
        "substage": substage,
        "sub_current": int(sub_current),
        "sub_total": int(sub_total),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def clear_progress(vault_root: Path, topic: str) -> None:
    """Remove the progress entry when the run finishes (missing file is fine)."""
    _progress_path(vault_root, topic).unlink(missing_ok=True)


def read_progress(vault_root: Path, topic: str) -> dict[str, Any] | None:
    """The in-flight progress entry, or ``None`` when absent/stale/unreadable."""
    path = _progress_path(vault_root, topic)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age = (datetime.now(UTC) - updated_at).total_seconds()
    if age > _STALE_AFTER_SECONDS:
        return None
    return {
        "phase": str(payload.get("phase") or ""),
        "current": int(payload.get("current") or 0),
        "total": int(payload.get("total") or 0),
        "detail": str(payload.get("detail") or ""),
        "substage": str(payload.get("substage") or ""),
        "sub_current": int(payload.get("sub_current") or 0),
        "sub_total": int(payload.get("sub_total") or 0),
        "updated_at": str(payload["updated_at"]),
    }
