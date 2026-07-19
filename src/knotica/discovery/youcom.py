"""``YouComProvider`` -- the sole shipped MVP search adapter (you.com Search REST).

Exa was cut from this pipeline's scope (user directive); you.com is the sole
shipped :class:`~knotica.discovery.provider.SearchProvider` adapter, though the
protocol stays provider-pluggable so a second adapter can join without touching
:class:`~knotica.discovery.service.DiscoveryService`.

**The wire shape here is NOT live-verified** (see ``SYSTEMS_PLAN.md``'s
Confirmed-API-Shapes Appendix, "you.com -- NOT verified"): it is authored from
the documented you.com Search REST convention (bearer auth, a ``hits`` array of
``url``/``title``/``snippet``/``author``/``page_age``). Deferred Step 31
(user-executed, requires a real ``KNOTICA_YOUCOM_API_KEY``) replaces this
module's fixtures with a live capture and adjusts :func:`_parse_response` if
the real shape differs.

The wire-to-record mapping is isolated in the pure :func:`_parse_response`, so
it is testable with zero network. Because the response shape is unconfirmed, a
response missing or mis-shaping the documented ``hits`` key raises the shared
typed ``SEARCH_API_ERROR`` -- never a raw ``KeyError``/``TypeError`` escaping to
the caller -- and the credential never appears in that error's message.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from knotica.discovery.http import DEFAULT_MAX_RETRIES, SearchHttpClient
from knotica.discovery.records import SearchQuery, SourceCandidate
from knotica.core.errors import ErrorCode, KnoticaError

if TYPE_CHECKING:
    import httpx

__all__ = ["YouComProvider"]

#: The documented you.com Search REST endpoint.
SEARCH_URL = "https://api.you.com/search"

#: The ``source_provider`` tag stamped on every candidate this adapter returns.
PROVIDER_NAME = "youcom"


class YouComProvider:
    """A :class:`~knotica.discovery.provider.SearchProvider` over you.com Search REST.

    Constructed with the resolved API key (see
    :func:`knotica.discovery.config.resolve_api_key`); the key is injected as a
    bearer header via :class:`~knotica.discovery.http.SearchHttpClient` and
    never appears in a URL, a log, or an error message. ``transport`` and
    ``sleep`` inject a fake transport and a no-op sleep in tests -- no test
    path touches the real network or a real backoff delay.
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        client_kwargs: dict[str, object] = {"transport": transport, "max_retries": max_retries}
        if sleep is not None:
            client_kwargs["sleep"] = sleep
        self._client = SearchHttpClient(
            auth_headers={"Authorization": f"Bearer {api_key}"},
            **client_kwargs,
        )

    def search(self, query: SearchQuery) -> list[SourceCandidate]:
        """Search you.com and map the response to :class:`SourceCandidate`s."""
        response = self._client.request("GET", SEARCH_URL, params=_build_params(query))
        return _parse_response(response.json())


def _build_params(query: SearchQuery) -> dict[str, object]:
    """Map a :class:`SearchQuery` to the documented you.com query params.

    Only ``query``/``num_web_results``/domain filters are sent -- date
    filtering is marked unconfirmed in you.com's primary docs (Confirmed-API-
    Shapes Appendix), so this provider does not guess at an unverified date
    param name; that filter degrades gracefully (silently unhonored) rather
    than risking a malformed request.
    """
    params: dict[str, object] = {
        "query": query.text,
        "num_web_results": query.max_results,
    }
    if query.include_domains:
        params["include_domains"] = ",".join(query.include_domains)
    if query.exclude_domains:
        params["exclude_domains"] = ",".join(query.exclude_domains)
    return params


def _parse_response(payload: Mapping[str, object]) -> list[SourceCandidate]:
    """Pure wire-to-record mapping -- no network, callable directly from tests.

    Raises the typed ``SEARCH_API_ERROR`` when the documented ``hits`` key is
    missing or the wrong type, rather than letting a raw ``KeyError``/
    ``TypeError`` escape -- the wire shape is unconfirmed, so a malformed
    response is an expected failure mode, not a programming bug.
    """
    hits = payload.get("hits")
    if not isinstance(hits, Sequence) or isinstance(hits, (str, bytes)):
        raise KnoticaError(
            ErrorCode.SEARCH_API_ERROR,
            "you.com search response is missing the documented 'hits' array.",
            retryable=False,
        )
    return [_parse_hit(hit) for hit in hits if isinstance(hit, Mapping)]


def _parse_hit(hit: Mapping[str, object]) -> SourceCandidate:
    return SourceCandidate(
        url=str(hit.get("url", "")),
        title=str(hit.get("title", "")),
        snippet=str(hit.get("snippet", "")),
        source_provider=PROVIDER_NAME,
        authors=_authors(hit.get("author")),
        published_date=_optional_str(hit.get("page_age")),
    )


def _authors(author: object) -> tuple[str, ...] | None:
    """Split a ``"A. Researcher, B. Scholar"`` author string into a tuple."""
    if not isinstance(author, str) or not author:
        return None
    names = tuple(name.strip() for name in author.split(",") if name.strip())
    return names or None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
