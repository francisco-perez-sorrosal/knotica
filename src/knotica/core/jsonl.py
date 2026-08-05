"""Lenient JSONL reading — keep the object rows, skip everything else.

Two vault files are read this way: the datasets inventory's dataset files and
the ingest activity journal. Both are append-only logs whose value is the rows
that *do* parse — a truncated final line from a crashed append, or a row that is
a bare scalar, must not take down a read of the whole file. Callers that need
strict parsing (a corrupt line is an error the user must fix) raise their own
typed error instead and do not use this module.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["read_jsonl_dicts"]


def read_jsonl_dicts(text: str) -> list[dict[str, Any]]:
    """Parse JSONL into object rows, dropping blank, undecodable, and non-object lines."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
