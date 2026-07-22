"""Golden-review payload helpers for the ``golden`` action dispatcher.

Thin adapters over :mod:`knotica.core.golden_review`. These functions have
no MCP tool registrations of their own — they are imported directly by
``tools_dispatch_golden.py``, the sole entry point into this logic.
"""

from __future__ import annotations

import json
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.page import PageNotFoundError, TopicNotFoundError

_EXCEPTIONS = (KnoticaError, TopicNotFoundError, PageNotFoundError)


def _parse_accepted(accepted_json: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(accepted_json)
    except json.JSONDecodeError as exc:
        raise KnoticaError(
            code=ErrorCode.INVALID_FRONTMATTER,
            message=f"accepted_json is not valid JSON: {exc}",
            fix="Pass a JSON array of candidate objects.",
        ) from exc
    if not isinstance(payload, list):
        raise KnoticaError(
            code=ErrorCode.INVALID_FRONTMATTER,
            message="accepted_json must be a JSON array of candidates",
            fix="Pass a JSON array of candidate objects.",
        )
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise KnoticaError(
                code=ErrorCode.INVALID_FRONTMATTER,
                message=f"accepted_json[{index}] is not an object",
                fix="Each candidate must be a JSON object.",
            )
        rows.append(item)
    return rows
