"""RFC 3339 datetime normalization for OKF transportability."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from knotica.okf.constants import RFC3339_DATE_ONLY_RE, RFC3339_DATETIME_RE

_DATE_ONLY = re.compile(RFC3339_DATE_ONLY_RE)
_DATETIME = re.compile(RFC3339_DATETIME_RE)


def is_rfc3339(value: str) -> bool:
    """Return whether ``value`` is a date-only or full RFC 3339 datetime."""
    return bool(_DATE_ONLY.fullmatch(value) or _DATETIME.fullmatch(value))


def normalize_timestamp(value: object) -> tuple[str | None, str | None]:
    """Normalize a timestamp field to RFC 3339 UTC.

    Returns ``(normalized, warning)``. Date-only values become midnight UTC.
    """
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, "timestamp must be a non-empty string"
    text = value.strip()
    if _DATETIME.fullmatch(text):
        return text, None
    if _DATE_ONLY.fullmatch(text):
        return f"{text}T00:00:00Z", "date-only value expanded to midnight UTC"
    return None, f"not RFC 3339: {text!r}"


def now_rfc3339() -> str:
    """Current UTC instant as RFC 3339."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def best_timestamp(fields: dict[str, object]) -> tuple[str | None, str | None]:
    """Pick the best timestamp from OKF/Knotica fields with fallback warning."""
    for key in ("timestamp", "updated", "retrieved", "created"):
        if key not in fields:
            continue
        normalized, warning = normalize_timestamp(fields[key])
        if normalized is not None:
            suffix = f" (from {key})" if key != "timestamp" else ""
            return normalized, (warning + suffix if warning else f"inferred from {key}")
    return now_rfc3339(), "timestamp defaulted to current UTC instant"
