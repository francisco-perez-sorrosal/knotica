"""Config + env keys -> a real ``DiscoveryService``, or ``None``.

The one place in ``core/`` that *constructs* the outbound search chain rather than
merely borrowing its identity rule -- the bridge P2 left unbuilt. It is a module of
its own because that construction is the whole of its responsibility, and because
keeping it beside the drain would put a network-provider factory inside the
function that must stay readable as a queue transaction.

Every ``discovery`` import is lazy, inside the function that needs it, so this
module (and the package that re-exports it) stays off the MCP cold-start path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from knotica.core.errors import KnoticaError

if TYPE_CHECKING:
    from knotica.discovery.config import SearchConfig
    from knotica.discovery.provider import SearchProvider
    from knotica.discovery.service import DiscoveryService


def build_default_discovery_service(
    *,
    config_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> DiscoveryService | None:
    """Construct the real ``DiscoveryService`` from config + env keys, or ``None``.

    Resolves the ``[gapfill.search]`` provider chain, builds an adapter for each
    provider that has a resolvable API key, and composes it with the keyless
    ``OpenAlexEnricher`` + ``ReputabilityScorer``. Returns ``None`` -- never raises
    -- when no provider is configured (no key), so the drain degrades to a no-op on
    a key-less host. All of ``discovery/`` is imported lazily here so this module
    stays off the MCP cold-start path. A malformed ``[gapfill.search]`` value still
    raises (a real operator error, distinct from "unconfigured").
    """
    from knotica.discovery.openalex import OpenAlexEnricher
    from knotica.discovery.reputability import ReputabilityScorer
    from knotica.discovery.service import DiscoveryService

    search_config = _resolve_search_config(config_path)
    providers = [
        provider
        for name in search_config.providers
        for provider in (_build_provider(name, environ=environ),)
        if provider is not None
    ]
    if not providers:
        return None
    return DiscoveryService(
        providers, OpenAlexEnricher(mailto=search_config.mailto), ReputabilityScorer()
    )


def _resolve_search_config(config_path: str | None) -> SearchConfig:
    from knotica.discovery.config import resolve_search_config

    return resolve_search_config(config_path)


def _build_provider(name: str, *, environ: Mapping[str, str] | None) -> SearchProvider | None:
    """Build the search adapter for ``name`` when its credential resolves.

    Credentials resolve through :func:`~knotica.discovery.config.resolve_api_key`
    -- the process environment (or an injected ``environ``), then ``./.env`` and
    ``~/.config/knotica/.env``. Sharing that one chain with
    ``gapfill_config._discovery_key_available``, the probe computing the
    ``discover_on_regression`` conditional default from the same "is a key
    configured?" question, is load-bearing: this factory once read ``os.environ``
    alone, so a key kept in ``.env`` (which the repo's own ``.env.example``
    invites) flipped discovery *on* at config time and then yielded no provider at
    run time -- the drain reported itself enabled and did nothing. Do not narrow
    this back to the environment; the two sites must answer identically or the
    feature lies about its own state. A missing key or an unrecognized name raises
    ``NOT_CONFIGURED``, caught here so the factory returns ``None`` rather than
    raising. you.com is the sole shipped adapter (exa was cut).
    """
    from knotica.discovery.config import resolve_api_key

    try:
        api_key = resolve_api_key(name, environ=environ)
    except KnoticaError:
        return None
    if name == "youcom":
        from knotica.discovery.youcom import YouComProvider

        return YouComProvider(api_key)
    return None
