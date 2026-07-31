"""``[notes]`` threshold config -- the resolution ladder's fuzzy/orphan gates.

Side-effect-free w.r.t. ``config.toml``, mirroring
:func:`knotica.core.gapfill_config.resolve_gapfill_config`: reads only the
``[notes]`` table of ``~/.config/knotica/config.toml``, never a socket, never
a module-level cache. A missing file or a missing ``[notes]`` table is not an
error -- both thresholds resolve to their defaults. A present-but-malformed
value (wrong type, or a threshold outside ``[0.0, 1.0]``) raises the typed
``NOT_CONFIGURED`` error naming the fix -- a real operator mistake, distinct
from "unconfigured".
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.config import config_file_path
from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "DEFAULT_COMPLETE_ORPHAN_THRESHOLD",
    "DEFAULT_GUESS_THRESHOLD",
    "NOTES_CONFIG_SECTION",
    "NotesConfig",
    "resolve_notes_config",
]

#: The ``[notes]`` table this module reads from ``config.toml``.
NOTES_CONFIG_SECTION = "notes"

#: Default score at/above which a resolved candidate is reported ``fuzzy``.
DEFAULT_GUESS_THRESHOLD = 0.75

#: Default score below which an orphaned anchor is offered no guess at all.
DEFAULT_COMPLETE_ORPHAN_THRESHOLD = 0.35


@dataclass(frozen=True, slots=True)
class NotesConfig:
    """The resolved ``[notes]`` resolution-ladder thresholds."""

    guess_threshold: float = DEFAULT_GUESS_THRESHOLD
    complete_orphan_threshold: float = DEFAULT_COMPLETE_ORPHAN_THRESHOLD


def resolve_notes_config(
    config_path: str | os.PathLike[str] | None = None,
) -> NotesConfig:
    """Parse ``[notes]`` fresh, or raise on a bad threshold value.

    Each key defaults independently -- overriding one leaves the other at its
    default -- and both are validated as a number in ``[0.0, 1.0]``.
    """
    section = _load_notes_section(config_path)
    return NotesConfig(
        guess_threshold=_resolve_threshold(section, "guess_threshold", DEFAULT_GUESS_THRESHOLD),
        complete_orphan_threshold=_resolve_threshold(
            section, "complete_orphan_threshold", DEFAULT_COMPLETE_ORPHAN_THRESHOLD
        ),
    )


def _resolve_threshold(section: Mapping[str, object], key: str, default: float) -> float:
    """Return ``section[key]`` validated as a ``[0.0, 1.0]`` number, or ``default``."""
    raw = section.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise _config_error(
            f"[{NOTES_CONFIG_SECTION}] {key} must be a number between 0.0 and 1.0, got {raw!r}.",
            f"Set {key} to a number between 0.0 and 1.0 under [{NOTES_CONFIG_SECTION}]"
            f" (e.g. {default}).",
        )
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise _config_error(
            f"[{NOTES_CONFIG_SECTION}] {key} must be between 0.0 and 1.0, got {value!r}.",
            f"Set {key} to a number between 0.0 and 1.0 under [{NOTES_CONFIG_SECTION}]"
            f" (e.g. {default}).",
        )
    return value


def _load_notes_section(config_path: str | os.PathLike[str] | None) -> Mapping[str, object]:
    """Return the ``[notes]`` table, or an empty mapping when absent/unreadable."""
    file = config_file_path(config_path)
    try:
        raw = file.read_bytes()
    except OSError:
        return {}
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    section = config.get(NOTES_CONFIG_SECTION)
    return section if isinstance(section, Mapping) else {}


def _config_error(message: str, fix: str) -> KnoticaError:
    """Build the typed ``NOT_CONFIGURED`` error for a malformed ``[notes]`` value."""
    return KnoticaError(ErrorCode.NOT_CONFIGURED, message, fix=fix)
