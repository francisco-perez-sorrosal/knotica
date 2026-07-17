"""Per-call config resolution -- ``config.toml`` schema and the unconfigured contract.

The server is stateless: ``config.toml`` and the vault are the only state, and
both are resolved **fresh on every call** (no module-level cache, deliberately
-- a config written after boot must take effect without a restart; an
mtime-cache is a sanctioned later optimization, not MVP). The only I/O here is
reading that one TOML file plus existence checks on the configured vault path.

Schema (``~/.config/knotica/config.toml``)::

    schema_version = 1
    default_vault  = "main"

    [vaults.main]
    path = "~/dev/data/knotica"   # ~ and $ENV expanded at resolution time

Three internal states collapse to one external contract:

* ``UNCONFIGURED`` -- no config file, unreadable/invalid TOML, or the
  requested/default vault is unresolvable.
* ``CONFIGURED_NO_VAULT`` -- the configured path is missing or not an
  initialized knotica vault (no git repo / no root ``SCHEMA.md``).
* ``READY`` -- the path resolves to an initialized vault.

Externally, anything short of ``READY`` is the single ``NOT_CONFIGURED``
result with state-specific remediation; :func:`diagnose` preserves the
three-state distinction for ``knotica doctor``.
"""

import os
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from knotica.core.errors import DEFAULT_FIX, ErrorCode, KnoticaError

#: Environment variable overriding the config file location (test hook and
#: power-user escape hatch). An explicit function argument wins over it.
CONFIG_PATH_ENV_VAR = "KNOTICA_CONFIG"

#: Default config location, unexpanded (``~`` resolved at call time -- never
#: bind the home directory at import time).
DEFAULT_CONFIG_PATH = "~/.config/knotica/config.toml"


class ConfigState(StrEnum):
    """The three internal configuration states (``doctor`` reports these)."""

    UNCONFIGURED = "UNCONFIGURED"
    CONFIGURED_NO_VAULT = "CONFIGURED_NO_VAULT"
    READY = "READY"


@dataclass(frozen=True, slots=True)
class ResolvedVault:
    """A vault that resolution found ready to use."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class ConfigDiagnosis:
    """Full resolution outcome, preserving the internal three-state machine.

    ``vault`` is populated exactly when ``state`` is ``READY``. ``detail``
    states what was found (or what is wrong and why); ``remediation`` is the
    state-specific fix text (empty when ``READY``).
    """

    state: ConfigState
    detail: str
    remediation: str
    vault: ResolvedVault | None = None


def config_file_path(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the config file location: argument > ``$KNOTICA_CONFIG`` > default.

    ``$ENV`` references and ``~`` in the chosen value are expanded at call
    time, so tests and setup flows can redirect the config without touching
    the real home directory.
    """
    raw = os.fspath(override) if override is not None else os.environ.get(CONFIG_PATH_ENV_VAR)
    if not raw:
        raw = DEFAULT_CONFIG_PATH
    return Path(os.path.expandvars(raw)).expanduser()


def resolve(
    vault: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> ResolvedVault:
    """Resolve the vault for one call, or raise the ``NOT_CONFIGURED`` error.

    Reads ``config.toml`` fresh, picks the explicit ``vault`` name if given
    (else ``default_vault``), expands the path, and verifies it is an
    initialized vault. Anything short of ``READY`` raises
    :class:`~knotica.core.errors.KnoticaError` with code ``NOT_CONFIGURED``
    and the state-specific remediation -- adapters render it into the result
    envelope, so all surfaces degrade identically.
    """
    diagnosis = diagnose(vault=vault, config_path=config_path)
    if diagnosis.vault is not None:
        return diagnosis.vault
    raise KnoticaError(
        code=ErrorCode.NOT_CONFIGURED,
        message=diagnosis.detail,
        fix=diagnosis.remediation,
    )


def diagnose(
    vault: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
) -> ConfigDiagnosis:
    """Run the resolution walk and report which of the three states holds.

    Never raises for configuration problems -- every failure shape maps to a
    diagnosis, so ``doctor`` can distinguish the states that :func:`resolve`
    collapses into one external contract.
    """
    file = config_file_path(config_path)
    config = _load_toml(file)
    if isinstance(config, str):
        return _unconfigured(config)

    vault_name = vault if vault is not None else config.get("default_vault")
    if not isinstance(vault_name, str) or not vault_name:
        return _unconfigured(
            f"Config file {file} names no usable default_vault and no vault was requested."
        )
    raw_path = _vault_path_entry(config, vault_name)
    if raw_path is None:
        return _unconfigured(f"Vault '{vault_name}' has no [vaults.{vault_name}] path in {file}.")

    root = Path(os.path.expandvars(raw_path)).expanduser()
    problem = _vault_problem(root)
    if problem is not None:
        return ConfigDiagnosis(
            state=ConfigState.CONFIGURED_NO_VAULT,
            detail=f"Vault '{vault_name}' points at {root}, but {problem}.",
            remediation=(
                f"Fix the path for vault '{vault_name}' in {file}, or run"
                " `/knotica:setup` (Claude Code) or `knotica init` (CLI)"
                " to initialize a vault there."
            ),
        )
    return ConfigDiagnosis(
        state=ConfigState.READY,
        detail=f"Vault '{vault_name}' ready at {root}.",
        remediation="",
        vault=ResolvedVault(name=vault_name, path=root),
    )


def _load_toml(file: Path) -> dict[str, Any] | str:
    """Read and parse the config file; return the table, or a problem string."""
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return f"No config file found at {file}."
    except OSError as error:
        return f"Config file {file} could not be read ({error})."
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        return f"Config file {file} is not valid TOML ({error})."


def list_vaults(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """List configured vaults for UI switchers (name, path, readiness).

    Returns ``{default_vault, vaults: [{name, path, ready, detail}, …]}``.
    Paths are expanded absolute strings. Does not raise for unconfigured hosts —
    returns an empty ``vaults`` list and empty ``default_vault`` instead.
    """
    file = config_file_path(config_path)
    config = _load_toml(file)
    if isinstance(config, str):
        return {"default_vault": "", "vaults": []}

    default = config.get("default_vault")
    default_vault = default if isinstance(default, str) else ""
    vaults_table = config.get("vaults")
    if not isinstance(vaults_table, dict):
        return {"default_vault": default_vault, "vaults": []}

    rows: list[dict[str, Any]] = []
    for name in sorted(vaults_table):
        if not isinstance(name, str) or not name:
            continue
        raw_path = _vault_path_entry(config, name)
        if raw_path is None:
            rows.append(
                {
                    "name": name,
                    "path": "",
                    "ready": False,
                    "detail": f"no path configured under [vaults.{name}]",
                }
            )
            continue
        root = Path(os.path.expandvars(raw_path)).expanduser()
        problem = _vault_problem(root)
        rows.append(
            {
                "name": name,
                "path": str(root),
                "ready": problem is None,
                "detail": "" if problem is None else problem,
            }
        )
    return {"default_vault": default_vault, "vaults": rows}


def _vault_path_entry(config: dict[str, Any], vault_name: str) -> str | None:
    """Return the raw ``[vaults.<name>] path`` string, or None if absent/invalid."""
    vaults = config.get("vaults")
    entry = vaults.get(vault_name) if isinstance(vaults, dict) else None
    raw_path = entry.get("path") if isinstance(entry, dict) else None
    if isinstance(raw_path, str) and raw_path:
        return raw_path
    return None


def _vault_problem(root: Path) -> str | None:
    """Return why ``root`` is not an initialized vault, or None if it is.

    Existence checks only (no content reads) -- this runs on every tool call.
    Deep validation of ``SCHEMA.md`` belongs to ``doctor``/``lint``.
    """
    if not root.is_dir():
        return "that path does not exist or is not a directory"
    if not (root / ".git").exists():
        return "that directory is not a git repository"
    if not (root / "SCHEMA.md").is_file():
        return "that directory has no root SCHEMA.md (not an initialized knotica vault)"
    return None


def _unconfigured(detail: str) -> ConfigDiagnosis:
    """Build the ``UNCONFIGURED`` diagnosis with the canonical setup fix."""
    return ConfigDiagnosis(
        state=ConfigState.UNCONFIGURED,
        detail=detail,
        remediation=DEFAULT_FIX[ErrorCode.NOT_CONFIGURED],
    )
