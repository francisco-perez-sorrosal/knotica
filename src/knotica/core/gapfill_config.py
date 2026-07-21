"""``[gapfill]`` loop-hook config -- the opt-in discovery-on-regression flags.

Side-effect-free w.r.t. ``config.toml``, mirroring
:func:`knotica.discovery.config.resolve_search_config`: reads only the
``[gapfill]`` table of ``~/.config/knotica/config.toml`` (top-level keys, siblings
of the ``[gapfill.search]`` sub-table), never a socket, never a module-level cache.
A missing file or a missing table is not an error. A present-but-malformed value
raises the typed ``NOT_CONFIGURED`` error naming the fix (a real operator mistake,
distinct from "unconfigured").

``discover_on_regression`` defaults to *on when a discovery key is present and
valid, off otherwise* (dec-029's named reversal trigger) -- an explicit
``config.toml`` value always wins over that conditional default; an explicit
``true`` with no resolvable key still fails closed to ``off`` (logged), so a
keyless install's externally-visible behavior is unchanged either way. Checking
key presence reads the environment (via
:func:`knotica.discovery.config.resolve_api_key`, imported lazily so
``knotica.discovery`` stays off the MCP cold-start path) but never opens a socket.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.config import config_file_path
from knotica.core.errors import ErrorCode, KnoticaError

__all__ = ["GAPFILL_CONFIG_SECTION", "GapfillHookConfig", "resolve_gapfill_config"]

_LOGGER = logging.getLogger(__name__)

#: The ``[gapfill]`` table this module reads from ``config.toml``.
GAPFILL_CONFIG_SECTION = "gapfill"

#: The ``config.toml`` key gating the loop-side discovery drain.
_DISCOVER_ON_REGRESSION_KEY = "discover_on_regression"

#: The fixed-budget default cap on gaps drained per regression event.
_DEFAULT_MAX_GAPS = 5


@dataclass(frozen=True, slots=True)
class GapfillHookConfig:
    """The resolved opt-in loop-side gap-fill batch settings.

    ``discover_on_regression`` gates the loop-side drain entirely; the dataclass
    default (``False``, used when a call site passes no config at all, e.g. the
    synchronous MCP-gate) stays static-off. :func:`resolve_gapfill_config` --
    the watcher's path -- instead computes a *conditional* default (on when a
    discovery key is present and valid, off otherwise); ``max_gaps`` bounds how
    many open ``genuine_gap``s one regression event drains (the fixed-budget
    defense -- never unbounded).
    """

    discover_on_regression: bool = False
    max_gaps: int = _DEFAULT_MAX_GAPS


def resolve_gapfill_config(
    config_path: str | os.PathLike[str] | None = None,
) -> GapfillHookConfig:
    """Parse ``[gapfill]`` fresh, or raise on a bad value.

    ``discover_on_regression``: an explicit ``config.toml`` value always wins.
    Explicit ``false`` stays off unconditionally; explicit ``true`` is honored
    only when a discovery key resolves, else it fails closed to ``off`` (logged
    -- a present-but-unusable key should be visible, not silently inert).
    Unspecified resolves the conditional default: on when a key is present and
    valid, off otherwise (dec-029's named reversal trigger) -- a keyless install
    resolves to ``False``, identical to the prior static default.
    """
    section = _load_gapfill_section(config_path)

    raw_flag = section.get(_DISCOVER_ON_REGRESSION_KEY)
    if raw_flag is not None and not isinstance(raw_flag, bool):
        raise _config_error(
            f"[{GAPFILL_CONFIG_SECTION}] discover_on_regression must be a boolean,"
            f" got {type(raw_flag).__name__}.",
            f"Set discover_on_regression = true or false under [{GAPFILL_CONFIG_SECTION}].",
        )
    flag = _resolve_discover_on_regression(raw_flag, config_path)

    raw_max = section.get("max_gaps", _DEFAULT_MAX_GAPS)
    if isinstance(raw_max, bool) or not isinstance(raw_max, int) or raw_max < 1:
        raise _config_error(
            f"[{GAPFILL_CONFIG_SECTION}] max_gaps must be a positive integer, got {raw_max!r}.",
            f"Set max_gaps to a positive integer under [{GAPFILL_CONFIG_SECTION}] (e.g. 5).",
        )

    return GapfillHookConfig(discover_on_regression=flag, max_gaps=raw_max)


def _resolve_discover_on_regression(
    raw_flag: bool | None, config_path: str | os.PathLike[str] | None
) -> bool:
    """Apply the explicit-wins-over-conditional-default rule (see module docstring)."""
    if raw_flag is False:
        return False
    key_available = _discovery_key_available(config_path)
    if raw_flag is True and not key_available:
        _LOGGER.info(
            "[%s] discover_on_regression=true but no valid discovery key is configured;"
            " keeping the loop-side discovery drain off (fail-closed). Configure a search"
            " provider credential to enable it.",
            GAPFILL_CONFIG_SECTION,
        )
        return False
    return raw_flag if raw_flag is not None else key_available


def _discovery_key_available(config_path: str | os.PathLike[str] | None) -> bool:
    """Return whether the configured search-provider chain has a resolvable key.

    Reads the ``[gapfill.search]`` provider chain and checks each provider's
    credential (env var, then the ``.env`` fallback files) via
    :func:`~knotica.discovery.config.resolve_api_key` -- never constructs an
    HTTP client or opens a socket. ``knotica.discovery`` is imported lazily here
    (mirroring :meth:`knotica.core.loop.LoopRunner._maybe_discover_for_gaps`'s
    lazy-import discipline) so it stays off the MCP cold-start path when this
    function is never called (the MCP-gate path never calls
    :func:`resolve_gapfill_config`). A malformed ``[gapfill.search]`` value fails
    closed to "no key" here rather than raising -- that table's own validation
    belongs to :func:`~knotica.discovery.config.resolve_search_config`'s own
    callers, not to this unrelated default's gate.
    """
    from knotica.discovery.config import resolve_api_key, resolve_search_config

    try:
        search_config = resolve_search_config(config_path)
    except KnoticaError:
        return False
    for provider in search_config.providers:
        try:
            resolve_api_key(provider)
        except KnoticaError:
            continue
        return True
    return False


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
