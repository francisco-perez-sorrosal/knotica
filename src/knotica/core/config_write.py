"""Shared additive writer for ``~/.config/knotica/config.toml``.

The single source of truth for *mutating* the config file. Consumers today are
``knotica init`` (scaffold a first vault) and the ``loop action=cadence`` MCP
path (write the ``[loop]`` table); the ``vault`` dispatcher (add / switch
vaults) joins them next. Reads live in :mod:`knotica.core.config`; this module
only writes.

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
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "atomic_write",
    "dump_config_toml",
    "read_config",
    "set_default_vault",
    "upsert_vault",
]

#: Config schema version this writer emits.
SCHEMA_VERSION = 1


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
    family, and any other dict-valued top-level key rendered as a flat ``[<key>]``
    table (e.g. ``[loop]``, ``[models]``, ``[gapfill]``). Callers mutate only the
    one key they own, so every untouched sibling section round-trips intact.
    """
    lines: list[str] = []
    for key, value in data.items():
        if key == "vaults" or isinstance(value, dict):
            continue
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key} = {_toml_scalar(value)}")
    for name, entry in data.get("vaults", {}).items():
        lines.append("")
        lines.append(f"[vaults.{name}]")
        for key, value in entry.items():
            lines.append(f"{key} = {_toml_scalar(value)}")
    for key, value in data.items():
        if key == "vaults" or not isinstance(value, dict):
            continue
        lines.append("")
        lines.append(f"[{key}]")
        for sub_key, sub_value in value.items():
            lines.append(f"{sub_key} = {_toml_scalar(sub_value)}")
    return "\n".join(lines) + "\n"


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
    return json.dumps(value)  # basic string with correct escaping
