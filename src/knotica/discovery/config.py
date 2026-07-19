"""``[gapfill.search]`` config resolution -- provider chain now, API keys at use time.

Two resolutions with deliberately different timing and trust, mirroring the split
``core.config`` / ``evals.llm`` already establish:

* :func:`resolve_search_config` parses the ``[gapfill.search]`` table of the same
  ``~/.config/knotica/config.toml`` **side-effect-free**: it reads one TOML file and
  nothing else -- no environment, no network, no client construction, no
  module-level cache (resolved fresh per call, like ``core.config.resolve``). It
  yields the provider chain (a single provider or an ordered fallback list) and the
  OpenAlex polite-pool ``mailto``. A missing file or a missing table is not an error:
  discovery is best-effort, so it defaults to :data:`DEFAULT_PROVIDER`.

* :func:`resolve_api_key` resolves a provider's credential from the **environment
  only, at use time**, raising the typed ``NOT_CONFIGURED`` error naming the exact
  env var **before** any HTTP client is constructed or any socket opened. Keys live
  in the environment only -- never in ``config.toml``, never in the vault, never in a
  log or an error message. This is the ``evals.llm`` env-only, fail-before-network,
  never-log discipline applied to the search providers.

The provider chain drives which adapter(s) ``DiscoveryService`` runs; the key gate
is what a genuinely offline run trips on before it can reach the network.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from knotica.core.config import config_file_path
from knotica.core.errors import ErrorCode, KnoticaError

__all__ = [
    "DEFAULT_PROVIDER",
    "PROVIDER_ENV_VARS",
    "SEARCH_CONFIG_SECTION",
    "SearchConfig",
    "env_var_for",
    "resolve_api_key",
    "resolve_search_config",
]

#: The ``[gapfill.search]`` table this module reads from ``config.toml``.
SEARCH_CONFIG_SECTION = "gapfill.search"

#: Per-provider credential env var -- keys resolve from the environment only, never
#: from ``config.toml`` or the vault. The map's keys are also the set of recognized
#: provider names a config chain may reference.
PROVIDER_ENV_VARS: Mapping[str, str] = {
    "youcom": "KNOTICA_YOUCOM_API_KEY",
    "exa": "KNOTICA_EXA_API_KEY",
}

#: The provider used when the config names none. you.com is the MVP's sole shipped
#: adapter, so it is the expected primary; the chain remains provider-pluggable.
DEFAULT_PROVIDER = "youcom"


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """The resolved ``[gapfill.search]`` settings -- provider chain + OpenAlex mailto.

    ``providers`` is the ordered fallback chain (at least one entry, each a
    recognized provider name); it defaults to a :data:`DEFAULT_PROVIDER` chain when
    the config names none, since discovery is best-effort and an unconfigured host
    still resolves to a usable primary provider. ``mailto`` is the OpenAlex
    polite-pool email when the config supplies one. Holds no credential -- keys
    resolve separately, at use time.
    """

    providers: tuple[str, ...]
    mailto: str | None = None


def resolve_search_config(
    config_path: str | os.PathLike[str] | None = None,
) -> SearchConfig:
    """Parse ``[gapfill.search]`` fresh, side-effect-free, or raise on a bad value.

    Reads only the one TOML file (via :func:`~knotica.core.config.config_file_path`,
    so ``$KNOTICA_CONFIG`` and an explicit ``config_path`` both redirect it). A
    missing file, unreadable file, invalid TOML, or absent table all fall back to a
    :data:`DEFAULT_PROVIDER` chain -- not an error, since discovery is best-effort. A
    present-but-malformed ``provider``/``mailto`` value or an unrecognized provider
    name raises ``NOT_CONFIGURED`` naming the fix. Never reads the environment, never
    opens a socket, never constructs a client.
    """
    section = _load_search_section(config_path)
    providers = _resolve_providers(section.get("provider"))
    mailto = section.get("mailto")
    if mailto is not None and not isinstance(mailto, str):
        raise _config_error(
            f"[{SEARCH_CONFIG_SECTION}] mailto must be a string, got {type(mailto).__name__}.",
            f"Set mailto to your contact email under [{SEARCH_CONFIG_SECTION}], or remove it.",
        )
    return SearchConfig(providers=providers, mailto=mailto)


def env_var_for(provider: str) -> str:
    """Return the credential env var for ``provider``, or raise for an unknown one."""
    env_var = PROVIDER_ENV_VARS.get(provider)
    if env_var is None:
        raise _config_error(
            f"Unknown search provider {provider!r}.",
            f"Use one of: {', '.join(sorted(PROVIDER_ENV_VARS))}.",
        )
    return env_var


def resolve_api_key(provider: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Resolve ``provider``'s API key from the environment, or raise before the network.

    Reads the provider's env var (:data:`PROVIDER_ENV_VARS`) and nothing else. A
    missing key raises ``NOT_CONFIGURED`` naming the exact variable **before** any
    HTTP client is built -- so an offline run never reaches the network. The key
    value is never logged or echoed; the error names the variable, never its content.
    """
    env = os.environ if environ is None else environ
    env_var = env_var_for(provider)
    key = env.get(env_var)
    if not key:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            (
                f"Search provider {provider!r} is not configured: {env_var} is not set,"
                " so the discovery layer cannot authenticate its search calls."
            ),
            fix=(
                f"Set {env_var} in your environment (never in config.toml or the vault)"
                f" to a valid {provider} API key."
            ),
        )
    return key


# ---------------------------------------------------------------------------
# TOML section loading + provider-chain normalization
# ---------------------------------------------------------------------------


def _load_search_section(config_path: str | os.PathLike[str] | None) -> Mapping[str, object]:
    """Return the ``[gapfill.search]`` table, or an empty mapping when absent.

    A missing file, unreadable file, invalid TOML, or absent/non-table section all
    collapse to an empty mapping -- discovery is best-effort, so those are normal
    states, and the caller then applies the :data:`DEFAULT_PROVIDER` default.
    """
    import tomllib

    file = config_file_path(config_path)
    try:
        raw = file.read_bytes()
    except OSError:
        return {}
    try:
        config = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}

    section = config.get("gapfill")
    if not isinstance(section, Mapping):
        return {}
    search = section.get("search")
    return search if isinstance(search, Mapping) else {}


def _resolve_providers(raw: object) -> tuple[str, ...]:
    """Normalize a ``provider`` value (str, list of str, or absent) into a chain.

    Absent or empty -> the :data:`DEFAULT_PROVIDER` chain. A string is a
    single-provider chain; a list is a fallback chain in order. Every name must be
    recognized; a wrong type or an unknown name raises ``NOT_CONFIGURED``.
    """
    if raw is None:
        return (DEFAULT_PROVIDER,)
    names = [raw] if isinstance(raw, str) else raw
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise _config_error(
            f"[{SEARCH_CONFIG_SECTION}] provider must be a string or a list of strings.",
            'Set provider = "youcom" or provider = ["youcom", "exa"].',
        )
    if not names:
        return (DEFAULT_PROVIDER,)
    for name in names:
        env_var_for(name)  # validates each name; raises NOT_CONFIGURED on an unknown one
    return tuple(names)


def _config_error(message: str, fix: str) -> KnoticaError:
    """Build the typed ``NOT_CONFIGURED`` error for a malformed ``[gapfill.search]``."""
    return KnoticaError(ErrorCode.NOT_CONFIGURED, message, fix=fix)
