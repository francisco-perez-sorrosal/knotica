"""Shared additive writer for ``~/.config/knotica/config.toml``.

The single source of truth for *mutating* the config file. Consumers today are
``knotica init`` (scaffold a first vault), the ``loop action=cadence`` MCP path
(write the ``[loop]`` table), and the ``vault`` dispatcher (``add`` / ``create``
/ ``use``). Reads live in :mod:`knotica.core.config`; this module only writes.

Because every one of those consumers names a vault, and the name is emitted
verbatim as a ``[vaults.<name>]`` header, the bare-key constraint is enforced
*here* rather than at each of them -- see :func:`_emit_table`.

Writes are **additive and atomic**: every pre-existing vault and sibling table
(``[loop]``, ``[models]``, ...) is preserved, and the file is replaced via a
same-directory temp file + ``os.replace`` so a torn write can never leave a
truncated config that drops vaults. ``config.toml`` lives *outside* the vault,
so writing it is a plain file write -- not a vault git mutation -- and never
takes the vault lock or makes a commit (the exemption ``knotica init``
documents).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "BARE_KEY_DESCRIPTION",
    "atomic_write",
    "dump_config_toml",
    "is_bare_key",
    "read_config",
    "set_default_vault",
    "upsert_vault",
]

#: Config schema version this writer emits.
SCHEMA_VERSION = 1

#: A TOML *bare* key -- the only shape a table-header segment may take here.
_BARE_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

#: Human-facing rendering of :data:`_BARE_KEY_PATTERN`, reused by every caller
#: that rejects a name, so the allowed set is described in exactly one place.
BARE_KEY_DESCRIPTION = "letters, digits, '-' and '_' (i.e. [A-Za-z0-9_-]+)"


def is_bare_key(name: str) -> bool:
    """True when ``name`` can be written verbatim as a TOML table-header segment.

    The predicate behind every "invalid vault name" rejection in the tree. Callers
    that want a tailored message check this first; callers that forget are still
    caught at the write seam by :func:`_emit_table`.
    """
    return _BARE_KEY_PATTERN.fullmatch(name) is not None


def read_config(path: Path) -> dict[str, Any]:
    """Read the existing config table, or an empty table if absent/invalid.

    An unreadable or malformed file yields ``{}`` so a mutation still produces a
    well-formed config rather than propagating a parse error -- the caller is
    about to re-serialize it additively anyway.
    """
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return {}


def upsert_vault(
    config_path: Path,
    name: str,
    vault_path: Path | str,
    *,
    make_default: bool,
) -> None:
    """Add or update ``[vaults.<name>]`` in ``config_path`` (additive, atomic).

    Ensures ``schema_version`` is set and, when ``make_default`` is true, points
    ``default_vault`` at ``name``. Sibling vaults and tables round-trip intact.
    Creates the parent directory if missing.

    Raises :class:`~knotica.core.errors.KnoticaError` (``INVALID_ARGUMENT``) when
    ``name`` is not a TOML bare key, *before* the file is touched -- the write is
    all-or-nothing, so a rejected name leaves the config byte-identical.
    """
    data = read_config(config_path)
    data["schema_version"] = SCHEMA_VERSION
    if make_default:
        data["default_vault"] = name
    vaults = data.setdefault("vaults", {})
    vaults[name] = {"path": str(vault_path)}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, dump_config_toml(data))


def set_default_vault(config_path: Path, name: str) -> None:
    """Point ``default_vault`` at an already-configured vault (additive, atomic).

    Raises :class:`~knotica.core.errors.KnoticaError` (``INVALID_ARGUMENT``)
    when ``name`` has no ``[vaults.<name>]`` entry -- flipping the default to an
    unknown vault would write a config that resolves to ``NOT_CONFIGURED`` on the
    very next call. Switching to a *configured-but-not-yet-initialized* vault is
    allowed (that surfaces as a readiness problem in ``vault status``/``doctor``,
    not a write error).
    """
    data = read_config(config_path)
    vaults = data.get("vaults")
    if not isinstance(vaults, dict) or name not in vaults:
        configured = sorted(vaults) if isinstance(vaults, dict) else []
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=(
                f"Cannot switch the active vault to {name!r} because it is not "
                f"configured. Configured vaults: {configured or '(none)'}."
            ),
            fix="Add it first with `vault action=add`, or pass a configured vault name.",
        )
    data["schema_version"] = SCHEMA_VERSION
    data["default_vault"] = name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, dump_config_toml(data))


def dump_config_toml(data: dict[str, Any]) -> str:
    """Serialize the config table additively.

    Handles three shapes: top-level scalars, the ``[vaults.<name>]`` nested-table
    family, and any other dict-valued top-level key rendered as a ``[<key>]``
    table (e.g. ``[loop]``, ``[models]``, ``[gapfill]``). Tables nest to any
    depth: a dict inside a table becomes its own dotted-header table, so the
    shipped ``[gapfill.search]`` section round-trips. Callers mutate only the one
    key they own, so every untouched sibling section round-trips intact.

    Raises :class:`~knotica.core.errors.KnoticaError` (``INVALID_ARGUMENT``) if any
    table key -- a vault name, most often -- is not a TOML bare key, rather than
    emitting a file that will not parse. See :func:`_emit_table`.
    """
    lines: list[str] = []
    for key, value in data.items():
        if key == "vaults" or isinstance(value, dict):
            continue
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key} = {_toml_scalar(value)}")
    for name, entry in data.get("vaults", {}).items():
        _emit_table("vaults", name, entry, lines)
    for key, value in data.items():
        if key == "vaults" or not isinstance(value, dict):
            continue
        _emit_table("", key, value, lines)
    return "\n".join(lines) + "\n"


def _emit_table(parent: str, segment: str, table: dict[str, Any], lines: list[str]) -> None:
    """Emit ``[parent.segment]`` with its own keys, then recurse into its sub-tables.

    ``segment`` is interpolated verbatim into the header, so **this is the seam
    that decides whether the emitted file parses at all** -- and it rejects any
    segment that is not a TOML bare key rather than writing one. Validating here
    instead of at each caller is deliberate (``dec-078``): ``knotica init``,
    ``vault action=add`` and ``vault action=create`` all name a vault today, a
    fourth caller will not remember, and the failure is silent in both directions
    -- ``[vaults.my name]`` does not parse at all, while ``[vaults.my.name]``
    parses as a *nested* table and yields a phantom vault with no path. The first
    is the destructive one: :func:`read_config` answers the parse error with
    ``{}``, so the *next* write rebuilds the file from nothing and drops every
    vault, ``default_vault``, ``[loop]``, ``[models]`` and ``[gapfill]``.

    The two passes are load-bearing and must not be collapsed into one. TOML
    attributes every key to the most recently opened header, so a scalar written
    *after* a sub-table header is silently reparented into that sub-table --
    ``enabled`` following ``[gapfill.search]`` would come back as
    ``gapfill.search.enabled``. Emitting all of this table's own keys before any
    sub-table header is what keeps the round-trip faithful.

    Recursing (rather than rendering a nested dict as a value) is what the
    ``[vaults.<name>]`` family always did; this generalizes it to arbitrary depth
    so the shipped ``[gapfill.search]`` table survives a rewrite. Rendering it
    inline instead produced JSON object syntax, which TOML cannot parse -- the
    same destruction path described above, reached from the other side.
    """
    header = f"{parent}.{segment}" if parent else segment
    if not is_bare_key(segment):
        raise KnoticaError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=(
                f"Config table key {segment!r} cannot be written: a TOML table "
                f"header accepts only {BARE_KEY_DESCRIPTION}. Emitting "
                f"[{header}] would produce a config file that no longer parses, "
                "which the next write would then rebuild from nothing."
            ),
            fix=(
                "Rename it to use only letters, digits, '-' and '_' — for a vault, "
                "re-register it under such a name with `vault action=add`."
            ),
        )
    lines.append("")
    lines.append(f"[{header}]")
    for key, value in table.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_toml_scalar(value)}")
    for key, value in table.items():
        if isinstance(value, dict):
            _emit_table(header, key, value, lines)


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file in the same dir + rename.

    Writing a sibling temp file and ``os.replace``-ing it (an atomic rename on
    the same filesystem) guarantees readers see either the old file or the fully
    written new one -- never a partial additive merge that drops vaults.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _toml_scalar(value: str | int | float | bool) -> str:
    """Render a scalar as a TOML value (bool before int/float -- ``bool`` is an int)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # `ensure_ascii=False` is load-bearing, not cosmetic. At the default, an
    # astral-plane character -- an emoji in a vault path, reachable on macOS --
    # is escaped as a UTF-16 surrogate pair (`"📚"`), and TOML rejects
    # surrogates as "not a Unicode scalar value". That unparseable file then
    # feeds the same amplifier the bare-key guard above exists to stop:
    # `read_config` answers a parse error with {}, so the next write rebuilds
    # the config from nothing. Emitting the character literally round-trips.
    return json.dumps(value, ensure_ascii=False)  # basic string with correct escaping
