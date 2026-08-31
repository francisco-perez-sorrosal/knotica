"""``[loop]`` eval cadence config -- min-interval, quiet-window, thread count.

Side-effect-free w.r.t. ``config.toml``, mirroring
:func:`knotica.core.gapfill_config.resolve_gapfill_config`: reads only the
``[loop]`` table of ``~/.config/knotica/config.toml``, never a socket, never a
module-level cache. A missing file or a missing table is not an error. A
present-but-malformed value raises the typed ``NOT_CONFIGURED`` error naming
the fix. Each key's validator is public because the *writer* of this table must
reject a bad value before the file is opened; a validator reached from a
caller-supplied argument raises ``INVALID_ARGUMENT`` instead (``from_argument``),
because a bad argument is not a broken install.

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
    "ARENA_SCORERS",
    "LOOP_CONFIG_SECTION",
    "LoopCadenceConfig",
    "resolve_loop_cadence_config",
    "validate_arena_scorer",
    "validate_eval_min_interval_hours",
    "validate_eval_num_threads",
    "validate_eval_window",
]

#: The ``[loop]`` table this module reads from ``config.toml``.
LOOP_CONFIG_SECTION = "loop"

#: The packaged default eval thread count.
_DEFAULT_NUM_THREADS = 4

#: Accepted ``arena_scorer`` values. ``heuristic`` is the default because the
#: eval-backed scorer bills a full golden-set eval **per variant** -- a 4-variant
#: race over a 21-question set is 84 worker+judge pairs. Opting in is a spending
#: decision, so it is a config choice rather than a silent upgrade.
ARENA_SCORERS: frozenset[str] = frozenset({"heuristic", "eval"})
_DEFAULT_ARENA_SCORER = "heuristic"

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
    #: Which scorer the prompt arena races with. ``heuristic`` (default) is
    #: free, deterministic, and **not** comparable to the gate baseline, so the
    #: arena refuses to rank against it rather than reverting every variant.
    #: ``eval`` runs the real golden-set harness per variant -- comparable, and
    #: billed accordingly.
    arena_scorer: str = _DEFAULT_ARENA_SCORER

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
    interval = validate_eval_min_interval_hours(raw_interval)

    raw_window = section.get("eval_window")
    window = validate_eval_window(raw_window)

    raw_threads = section.get("eval_num_threads", _DEFAULT_NUM_THREADS)
    threads = validate_eval_num_threads(raw_threads)

    raw_scorer = section.get("arena_scorer", _DEFAULT_ARENA_SCORER)
    scorer = validate_arena_scorer(raw_scorer)

    return LoopCadenceConfig(
        eval_min_interval_hours=interval,
        eval_window=window,
        eval_num_threads=threads,
        arena_scorer=scorer,
    )


def validate_arena_scorer(raw_scorer: object, *, from_argument: bool = False) -> str:
    """Normalize an ``arena_scorer`` value, or raise the typed error naming the fix.

    Public because a *writer* must reject a bad value before it reaches
    ``config.toml`` -- validating only on the next read would leave a config
    file the resolver refuses to parse, breaking every unrelated ``[loop]``
    consumer until a human edits the file by hand.
    """
    if not isinstance(raw_scorer, str) or raw_scorer.strip().lower() not in ARENA_SCORERS:
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] arena_scorer must be one of"
            f" {'|'.join(sorted(ARENA_SCORERS))}, got {raw_scorer!r}.",
            f'Set arena_scorer = "heuristic" (free, not gate-comparable) or "eval"'
            f" (real golden-set eval per variant, billed) under [{LOOP_CONFIG_SECTION}].",
            from_argument=from_argument,
            argument_fix='Pass arena_scorer="heuristic" or "eval".',
        )
    return raw_scorer.strip().lower()


def validate_eval_min_interval_hours(raw_interval: object, *, from_argument: bool = False) -> float:
    """Normalize ``eval_min_interval_hours``, or raise the typed error naming the fix."""
    if isinstance(raw_interval, bool) or not isinstance(raw_interval, (int, float)):
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_min_interval_hours must be a number,"
            f" got {raw_interval!r}.",
            f"Set eval_min_interval_hours to a non-negative number under"
            f" [{LOOP_CONFIG_SECTION}] (e.g. 24).",
            from_argument=from_argument,
        )
    if raw_interval < 0:
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_min_interval_hours must be non-negative,"
            f" got {raw_interval!r}.",
            f"Set eval_min_interval_hours to a non-negative number under"
            f" [{LOOP_CONFIG_SECTION}] (e.g. 24).",
            from_argument=from_argument,
        )
    return float(raw_interval)


def validate_eval_window(raw_window: object, *, from_argument: bool = False) -> str | None:
    """Normalize ``eval_window``, or raise the typed error naming the fix."""
    if raw_window is None:
        return None
    if not isinstance(raw_window, str):
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_window must be a string, got"
            f" {type(raw_window).__name__}.",
            f'Set eval_window to a "HH:MM-HH:MM" range under [{LOOP_CONFIG_SECTION}]'
            f' (e.g. "22:00-02:00").',
            from_argument=from_argument,
        )
    _parse_window(raw_window, from_argument=from_argument)  # raises on malformed input
    return raw_window


def validate_eval_num_threads(raw_threads: object, *, from_argument: bool = False) -> int:
    """Normalize ``eval_num_threads``, or raise the typed error naming the fix."""
    if isinstance(raw_threads, bool) or not isinstance(raw_threads, int):
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_num_threads must be an integer, got {raw_threads!r}.",
            f"Set eval_num_threads to an integer between 1 and {MAX_NUM_THREADS}"
            f" under [{LOOP_CONFIG_SECTION}].",
            from_argument=from_argument,
        )
    if not 1 <= raw_threads <= MAX_NUM_THREADS:
        raise _config_error(
            f"[{LOOP_CONFIG_SECTION}] eval_num_threads must be between 1 and"
            f" {MAX_NUM_THREADS}, got {raw_threads!r}.",
            f"Set eval_num_threads to an integer between 1 and {MAX_NUM_THREADS}"
            f" under [{LOOP_CONFIG_SECTION}].",
            from_argument=from_argument,
        )
    return raw_threads


def _parse_window(raw_window: str, *, from_argument: bool = False) -> tuple[time, time]:
    """Parse a ``"HH:MM-HH:MM"`` string into ``(start, end)`` times.

    Raises the typed error naming the fix on any unparseable input.
    """
    parts = raw_window.split(_WINDOW_SEPARATOR)
    if len(parts) != 2:
        raise _malformed_window_error(raw_window, from_argument=from_argument)
    try:
        start = _parse_time(parts[0])
        end = _parse_time(parts[1])
    except ValueError as exc:
        raise _malformed_window_error(raw_window, from_argument=from_argument) from exc
    return start, end


def _parse_time(raw_time: str) -> time:
    hour_str, _, minute_str = raw_time.partition(_TIME_SEPARATOR)
    if not minute_str:
        raise ValueError(f"missing minute component in {raw_time!r}")
    return time(hour=int(hour_str), minute=int(minute_str))


def _malformed_window_error(raw_window: str, *, from_argument: bool = False) -> KnoticaError:
    return _config_error(
        f'[{LOOP_CONFIG_SECTION}] eval_window is not a valid "HH:MM-HH:MM"'
        f" range, got {raw_window!r}.",
        f'Set eval_window to a "HH:MM-HH:MM" range under [{LOOP_CONFIG_SECTION}]'
        f' (e.g. "22:00-02:00").',
        from_argument=from_argument,
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


def _config_error(
    message: str,
    fix: str,
    *,
    from_argument: bool = False,
    argument_fix: str | None = None,
) -> KnoticaError:
    """Build the typed error for a rejected ``[loop]`` value, coded by caller.

    A malformed *file* is a misconfiguration (``NOT_CONFIGURED``, whose fix is
    "edit the config"). A value the caller just passed to the tool that writes
    that file is not: telling an agent to hand-edit ``config.toml`` sends it
    down a setup path when the correction is one argument away.
    """
    if from_argument:
        return KnoticaError(ErrorCode.INVALID_ARGUMENT, message, fix=argument_fix or fix)
    return KnoticaError(ErrorCode.NOT_CONFIGURED, message, fix=fix)
