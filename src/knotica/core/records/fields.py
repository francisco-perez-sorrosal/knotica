"""Boundary parsing helpers shared by every record family in this package.

One record line arrives as untyped JSON; these helpers turn a key into a typed
value or a :class:`RecordParseError` naming the record kind, the key, and what
was found. Every ``from_json_line`` in the package parses through them, so the
error grammar is declared once rather than per family.

The helpers are package-private (leading underscore) and imported by name from
the sibling record modules. They are deliberately *not* re-exported by the
package ``__init__``: only :class:`RecordParseError` is public.
"""

import json
from collections.abc import Mapping

__all__ = ["RecordParseError"]


class RecordParseError(ValueError):
    """Record content does not conform to the constitution's frozen shape."""


def _validate_schema_version(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"schema_version must be an integer >= 1, got {value!r}")


def _validate_enum(field: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {'|'.join(sorted(allowed))}, got {value!r}")


def _load_json_object(line: str, *, record: str) -> dict[str, object]:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as error:
        raise RecordParseError(f"{record} record is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise RecordParseError(f"{record} record must be a JSON object, got {type(data).__name__}")
    return data


def _required_field(data: Mapping[str, object], key: str, *, record: str) -> object:
    if key not in data:
        raise RecordParseError(f"{record} record is missing required field {key!r}")
    return data[key]


def _required_str(data: Mapping[str, object], key: str, *, record: str) -> str:
    value = _required_field(data, key, record=record)
    if not isinstance(value, str):
        raise RecordParseError(f"{record} record field {key!r} must be a string, got {value!r}")
    return value


def _optional_str(data: Mapping[str, object], key: str, *, record: str) -> str | None:
    value = _required_field(data, key, record=record)
    if value is not None and not isinstance(value, str):
        raise RecordParseError(
            f"{record} record field {key!r} must be a string or null, got {value!r}"
        )
    return value


def _optional_str_absent(data: Mapping[str, object], key: str, *, record: str) -> str | None:
    """A string field that may be wholly absent (additive-only); missing/``null`` -> ``None``."""
    if key not in data or data[key] is None:
        return None
    value = data[key]
    if not isinstance(value, str):
        raise RecordParseError(f"{record} record field {key!r} must be a string, got {value!r}")
    return value


def _optional_str_or_default(
    data: Mapping[str, object], key: str, default: str, *, record: str
) -> str:
    """A string field that may be absent (additive-only); missing/``null`` -> ``default``."""
    if key not in data or data[key] is None:
        return default
    value = data[key]
    if not isinstance(value, str):
        raise RecordParseError(f"{record} record field {key!r} must be a string, got {value!r}")
    return value


def _optional_object_absent(
    data: Mapping[str, object], key: str, *, record: str
) -> dict[str, object] | None:
    """A JSON-object field that may be wholly absent (additive-only); missing/``null`` -> ``None``."""
    if key not in data or data[key] is None:
        return None
    value = data[key]
    if not isinstance(value, dict):
        raise RecordParseError(
            f"{record} record field {key!r} must be a JSON object, got {value!r}"
        )
    return value


def _required_int(data: Mapping[str, object], key: str, *, record: str) -> int:
    value = _required_field(data, key, record=record)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecordParseError(f"{record} record field {key!r} must be an integer, got {value!r}")
    return value


def _required_bool(data: Mapping[str, object], key: str, *, record: str) -> bool:
    value = _required_field(data, key, record=record)
    if not isinstance(value, bool):
        raise RecordParseError(f"{record} record field {key!r} must be a boolean, got {value!r}")
    return value


def _required_number(data: Mapping[str, object], key: str, *, record: str) -> float:
    value = _required_field(data, key, record=record)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordParseError(f"{record} record field {key!r} must be a number, got {value!r}")
    return float(value)


def _required_str_tuple(data: Mapping[str, object], key: str, *, record: str) -> tuple[str, ...]:
    value = _required_field(data, key, record=record)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RecordParseError(
            f"{record} record field {key!r} must be an array of strings, got {value!r}"
        )
    return tuple(value)


def _required_object(data: Mapping[str, object], key: str, *, record: str) -> dict[str, object]:
    value = _required_field(data, key, record=record)
    if not isinstance(value, dict):
        raise RecordParseError(
            f"{record} record field {key!r} must be a JSON object, got {value!r}"
        )
    return value
