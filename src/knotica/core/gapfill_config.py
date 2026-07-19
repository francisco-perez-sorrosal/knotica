"""``[gapfill]`` loop-hook config -- the opt-in discovery-on-regression flags.

Side-effect-free, mirroring :func:`knotica.discovery.config.resolve_search_config`:
reads only the ``[gapfill]`` table of ``~/.config/knotica/config.toml`` (top-level
keys, siblings of the ``[gapfill.search]`` sub-table), never the environment, never
a socket, never a module-level cache. A missing file or a missing table is not an
error -- the loop-side batch is **off by default**, so an unconfigured host resolves
to the disabled default. A present-but-malformed value raises the typed
``NOT_CONFIGURED`` error naming the fix (a real operator mistake, distinct from
"unconfigured").
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.config import config_file_path
from knotica.core.errors import ErrorCode, KnoticaError

__all__ = ["GAPFILL_CONFIG_SECTION", "GapfillHookConfig", "resolve_gapfill_config"]

#: The ``[gapfill]`` table this module reads from ``config.toml``.
GAPFILL_CONFIG_SECTION = "gapfill"

#: The fixed-budget default cap on gaps drained per regression event.
_DEFAULT_MAX_GAPS = 5


@dataclass(frozen=True, slots=True)
class GapfillHookConfig:
    """The resolved opt-in loop-side gap-fill batch settings.

    ``discover_on_regression`` gates the loop-side drain entirely (default off);
    ``max_gaps`` bounds how many open ``genuine_gap``s one regression event drains
    (the fixed-budget defense -- never unbounded).
    """

    discover_on_regression: bool = False
    max_gaps: int = _DEFAULT_MAX_GAPS


def resolve_gapfill_config(
    config_path: str | os.PathLike[str] | None = None,
) -> GapfillHookConfig:
    """Parse ``[gapfill]`` fresh, side-effect-free, or raise on a bad value."""
    section = _load_gapfill_section(config_path)

    flag = section.get("discover_on_regression", False)
    if not isinstance(flag, bool):
        raise _config_error(
            f"[{GAPFILL_CONFIG_SECTION}] discover_on_regression must be a boolean,"
            f" got {type(flag).__name__}.",
            f"Set discover_on_regression = true or false under [{GAPFILL_CONFIG_SECTION}].",
        )

    raw_max = section.get("max_gaps", _DEFAULT_MAX_GAPS)
    if isinstance(raw_max, bool) or not isinstance(raw_max, int) or raw_max < 1:
        raise _config_error(
            f"[{GAPFILL_CONFIG_SECTION}] max_gaps must be a positive integer, got {raw_max!r}.",
            f"Set max_gaps to a positive integer under [{GAPFILL_CONFIG_SECTION}] (e.g. 5).",
        )

    return GapfillHookConfig(discover_on_regression=flag, max_gaps=raw_max)


def _load_gapfill_section(config_path: str | os.PathLike[str] | None) -> Mapping[str, object]:
    """Return the ``[gapfill]`` table, or an empty mapping when absent/unreadable."""
    file = config_file_path(config_path)
    try:
        raw = file.read_bytes()
    except OSError:
        return {}
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    section = config.get(GAPFILL_CONFIG_SECTION)
    return section if isinstance(section, Mapping) else {}


def _config_error(message: str, fix: str) -> KnoticaError:
    """Build the typed ``NOT_CONFIGURED`` error for a malformed ``[gapfill]`` value."""
    return KnoticaError(ErrorCode.NOT_CONFIGURED, message, fix=fix)
