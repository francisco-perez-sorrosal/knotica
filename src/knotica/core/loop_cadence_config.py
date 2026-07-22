"""``[loop]`` eval cadence config -- min-interval, quiet-window, thread count.

Side-effect-free w.r.t. ``config.toml``, mirroring
:func:`knotica.core.gapfill_config.resolve_gapfill_config`: reads only the
``[loop]`` table of ``~/.config/knotica/config.toml``, never a socket, never a
module-level cache. A missing file or a missing table is not an error. A
present-but-malformed value raises the typed ``NOT_CONFIGURED`` error naming
the fix.

At all-defaults (``eval_min_interval_hours=0``, ``eval_window=None``,
``eval_num_threads=4``) this resolver's callers must observe byte-identical
scheduling behavior to a pre-cadence install -- the defaults are chosen to
match today's implicit behavior exactly.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time

from knotica.core.config import config_file_path
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.evals.config import MAX_NUM_THREADS

__all__ = [
    "LOOP_CONFIG_SECTION",
    "LoopCadenceConfig",
    "resolve_loop_cadence_config",
]

#: The ``[loop]`` table this module reads from ``config.toml``.
LOOP_CONFIG_SECTION = "loop"

#: The packaged default eval thread count.
_DEFAULT_NUM_THREADS = 4

_WINDOW_SEPARATOR = "-"
_TIME_SEPARATOR = ":"


@dataclass(frozen=True, slots=True)
class LoopCadenceConfig:
    """The resolved ``[loop]`` eval cadence/throttle settings.

    ``eval_min_interval_hours=0`` and ``eval_window=None`` are the byte-identical
    defaults: no cadence hold is ever applied. ``eval_num_threads`` bounds the
    loop's eval thread count, matching :data:`knotica.evals.config.NUM_THREADS`'s
    default.
    """

    eval_min_interval_hours: float = 0.0
    eval_window: str | None = None
    eval_num_threads: int = _DEFAULT_NUM_THREADS

    def parsed_window(self) -> tuple[time, time] | None:
        """Parse ``eval_window`` into ``(start, end)`` bounds, or ``None`` if unset.

        Supports midnight wrap (``start > end``, e.g. ``"22:00-02:00"``) -- the
        caller is responsible for interpreting wrap semantics; this method only
        parses the two bounds.
        """
        if self.eval_window is None:
            return None
        return _parse_window(self.eval_window)


def resolve_loop_cadence_config(
    config_path: str | os.PathLike[str] | None = None,
) -> LoopCadenceConfig:
    """Parse ``[loop]`` fresh, or raise on a bad value.

    Missing file/table returns an all-defaults instance. Each key is
    independently optional. A malformed ``eval_window`` or an
    ``eval_num_threads`` outside ``1..MAX_NUM_THREADS`` raises the typed
    ``NOT_CONFIGURED`` error naming the fix.
    """
    section = _load_loop_section(config_path)

    raw_interval = section.get("eval_min_interval_hours", 0.0)
    interval = _resolve_interval(raw_interval)

    raw_window = section.get("eval_window")
    window = _resolve_window(raw_window)

    raw_threads = section.get("eval_num_threads", _DEFAULT_NUM_THREADS)
    threads = _resolve_num_threads(raw_threads)

    return LoopCadenceConfig(
        eval_min_interval_hours=interval,
        eval_window=window,
        eval_num_threads=threads,
    )


def _resolve_interval(raw_interval: object) -> float:
    if isinstance(raw_interval, bool) or not isinstance(raw_interval, (int, float)):
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_min_interval_hours must be a number,"
            f" got {raw_interval!r}.",
            f"Set eval_min_interval_hours to a non-negative number under"
            f" [{LOOP_CONFIG_SECTION}] (e.g. 24).",
        )
    if raw_interval < 0:
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_min_interval_hours must be non-negative,"
            f" got {raw_interval!r}.",
            f"Set eval_min_interval_hours to a non-negative number under"
            f" [{LOOP_CONFIG_SECTION}] (e.g. 24).",
        )
    return float(raw_interval)


def _resolve_window(raw_window: object) -> str | None:
    if raw_window is None:
        return None
    if not isinstance(raw_window, str):
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_window must be a string, got"
            f" {type(raw_window).__name__}.",
            f'Set eval_window to a "HH:MM-HH:MM" range under [{LOOP_CONFIG_SECTION}]'
            f' (e.g. "22:00-02:00").',
        )
    _parse_window(raw_window)  # raises NOT_CONFIGURED on malformed input
    return raw_window


def _resolve_num_threads(raw_threads: object) -> int:
    if isinstance(raw_threads, bool) or not isinstance(raw_threads, int):
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_num_threads must be an integer, got {raw_threads!r}.",
            f"Set eval_num_threads to an integer between 1 and {MAX_NUM_THREADS}"
            f" under [{LOOP_CONFIG_SECTION}].",
        )
    if not 1 <= raw_threads <= MAX_NUM_THREADS:
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_num_threads must be between 1 and"
            f" {MAX_NUM_THREADS}, got {raw_threads!r}.",
            f"Set eval_num_threads to an integer between 1 and {MAX_NUM_THREADS}"
            f" under [{LOOP_CONFIG_SECTION}].",
        )
    return raw_threads


def _parse_window(raw_window: str) -> tuple[time, time]:
    """Parse a ``"HH:MM-HH:MM"`` string into ``(start, end)`` times.

    Raises the typed ``NOT_CONFIGURED`` error on any unparseable input.
    """
    parts = raw_window.split(_WINDOW_SEPARATOR)
    if len(parts) != 2:
        raise _malformed_window_error(raw_window)
    try:
        start = _parse_time(parts[0])
        end = _parse_time(parts[1])
    except ValueError as exc:
        raise _malformed_window_error(raw_window) from exc
    return start, end


def _parse_time(raw_time: str) -> time:
    hour_str, _, minute_str = raw_time.partition(_TIME_SEPARATOR)
    if not minute_str:
        raise ValueError(f"missing minute component in {raw_time!r}")
    return time(hour=int(hour_str), minute=int(minute_str))


def _malformed_window_error(raw_window: str) -> KnoticaError:
    return _config_error(
        f'[{LOOP_CONFIG_SECTION}] eval_window is not a valid "HH:MM-HH:MM"'
        f" range, got {raw_window!r}.",
        f'Set eval_window to a "HH:MM-HH:MM" range under [{LOOP_CONFIG_SECTION}]'
        f' (e.g. "22:00-02:00").',
    )


def _load_loop_section(config_path: str | os.PathLike[str] | None) -> Mapping[str, object]:
    """Return the ``[loop]`` table, or an empty mapping when absent/unreadable."""
    file = config_file_path(config_path)
    try:
        raw = file.read_bytes()
    except OSError:
        return {}
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    section = config.get(LOOP_CONFIG_SECTION)
    return section if isinstance(section, Mapping) else {}


def _config_error(message: str, fix: str) -> KnoticaError:
    """Build the typed ``NOT_CONFIGURED`` error for a malformed ``[loop]`` value."""
    return KnoticaError(ErrorCode.NOT_CONFIGURED, message, fix=fix)
