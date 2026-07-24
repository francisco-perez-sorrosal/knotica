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
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

__all__ = ["clear_progress", "read_progress", "write_progress"]

_logger = logging.getLogger(__name__)

_LOCKS_DIR = PurePath(".knotica/locks")

#: A progress entry older than this is a leftover from a dead run, not news.
_STALE_AFTER_SECONDS = 15 * 60.0

#: Bound on the accumulating per-example outcomes list (mirrors the existing
#: ``detail[:200]`` truncation convention below).
EXAMPLES_CAP = 200
DETAIL_CAP = 200


def _progress_path(vault_root: Path, topic: str) -> Path:
    safe_topic = topic.strip().strip("/").replace("/", "-") or "vault"
    return vault_root / _LOCKS_DIR / f"loop-progress-{safe_topic}.json"


def _capped_examples(examples: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The accumulating outcomes list, capped and per-entry detail-truncated."""
    if not examples:
        return []
    capped = examples[:EXAMPLES_CAP]
    return [
        {**entry, "detail": str(entry.get("detail", ""))[:DETAIL_CAP]}
        if "detail" in entry
        else entry
        for entry in capped
    ]


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
    examples: list[dict[str, Any]] | None = None,
) -> None:
    """Overwrite the in-flight progress entry (atomic replace).

    ``substage`` refines the per-question phase ("answering", "judging"), with
    ``sub_current``/``sub_total`` counting judge samples when they actually run
    (a warm judge-cache hit draws no samples and reports none). ``examples`` is
    the accumulating per-question outcome list (``{id, status, error_class,
    detail}``), capped at ``EXAMPLES_CAP`` entries with each ``detail``
    truncated to ``DETAIL_CAP`` characters.

    Never raises: a progress heartbeat must never cancel the eval run it is
    reporting on (td-013). Concurrent writers each get a unique temp file
    (:func:`tempfile.mkstemp`), so no two writers can ever race on the same
    rename target — failures are logged at debug and swallowed.
    """
    path = _progress_path(vault_root, topic)
    tmp_path: str | None = None
    try:
        # Payload construction (int() coercions, json.dumps) is inside the try: a
        # bad `current` value or non-serializable `examples` entry must be
        # swallowed like an I/O failure, never propagate to cancel the run.
        payload = {
            "phase": phase,
            "current": int(current),
            "total": int(total),
            "detail": detail[:DETAIL_CAP],
            "substage": substage,
            "sub_current": int(sub_current),
            "sub_total": int(sub_total),
            "examples": _capped_examples(examples),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(json.dumps(payload))
        os.replace(tmp_path, path)
        tmp_path = None
    except Exception:  # noqa: BLE001 — the contract is "never raise", not "never raise OSError"
        _logger.debug("write_progress: non-fatal write failure for topic %r", topic, exc_info=True)
    finally:
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                _logger.debug("write_progress: temp cleanup failed for topic %r", topic)


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
        "examples": payload.get("examples") or [],
        "updated_at": str(payload["updated_at"]),
    }
