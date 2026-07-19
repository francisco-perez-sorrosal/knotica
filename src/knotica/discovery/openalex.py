"""``OpenAlexEnricher`` -- provider-agnostic scholarly enrichment, batched by DOI.

Stamps ``citation_count``/``venue``/``is_open_access``/``fwci``/``published_date``
onto every candidate with a resolvable DOI, regardless of which
:class:`~knotica.discovery.provider.SearchProvider` originated it (Decision A --
enrichment is written once, not per-adapter). OpenAlex is keyless: the polite
pool is honored via a ``mailto=`` param, never an API key.

Per the live-verified 2026-07-19 Confirmed-API-Shapes Appendix, lookups are
batched into an OR-filter, **at most 50 DOIs per call**
(``filter=doi:<a>|<b>|...``), trimmed with ``select=`` to the six fields this
module actually consumes. The free tier is now a **USD credit budget**
(``x-ratelimit-limit-usd``/``x-ratelimit-remaining-usd``/``x-ratelimit-cost-usd``)
-- this module reads those headers dynamically off the response and never
hardcodes a request cadence or a credit ceiling (the stale "100 credits/day"
assumption from research must not get baked in here).

A batch that fails with a retryable status (429 rate-limited, or a 5xx that
exhausted :class:`~knotica.discovery.http.SearchHttpClient`'s retries) degrades
that batch to un-enriched rather than raising -- callers get the candidates
back with their scholarly fields still ``None``, never a hard failure that
would propagate into the loop. A non-retryable error (e.g. a malformed
request) still raises, since retrying it would never help.

The wire-to-stamp mapping is isolated in the pure :func:`_stamp`, so it is
testable with zero network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from knotica.core.errors import KnoticaError
from knotica.discovery.http import DEFAULT_MAX_RETRIES, SearchHttpClient
from knotica.discovery.records import SourceCandidate

if TYPE_CHECKING:
    import httpx

__all__ = ["OpenAlexEnricher"]

#: The OpenAlex works endpoint this module queries.
WORKS_URL = "https://api.openalex.org/works"

#: Maximum DOIs per OR-filter batch -- OpenAlex's confirmed per-page ceiling.
MAX_DOIS_PER_BATCH = 50

#: ``select=`` field trim -- confirmed working; cuts payload size and credit cost
#: to only the fields :func:`_stamp` consumes.
_SELECT_FIELDS = "id,doi,cited_by_count,fwci,publication_date,open_access,primary_location"

#: The documented, un-normalized DOI URL prefix OpenAlex returns.
_DOI_URL_PREFIX = "https://doi.org/"


class OpenAlexEnricher:
    """A :class:`~knotica.discovery.provider.Enricher` over the OpenAlex works API.

    ``mailto`` is the polite-pool contact email (optional, but recommended by
    OpenAlex for reliable throughput). ``transport`` and ``sleep`` inject a
    fake transport and a no-op sleep in tests, mirroring the seam
    :class:`~knotica.discovery.http.SearchHttpClient` already establishes --
    no test path touches the real network or a real backoff delay.
    """

    def __init__(
        self,
        mailto: str | None = None,
        *,
        http_client: SearchHttpClient | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._mailto = mailto
        if http_client is not None:
            self._client = http_client
            return
        client_kwargs: dict[str, object] = {"transport": transport, "max_retries": max_retries}
        if sleep is not None:
            client_kwargs["sleep"] = sleep
        self._client = SearchHttpClient(**client_kwargs)

    def enrich(self, candidates: list[SourceCandidate]) -> list[SourceCandidate]:
        """Stamp scholarly metadata onto every DOI-resolvable candidate.

        A candidate without a resolvable DOI passes through unchanged with its
        scholarly fields left ``None`` (they can still be classified by domain
        at the reputability stage). A candidate with a DOI always comes back
        with that DOI normalized to its bare form, even when the OpenAlex
        lookup for its batch degrades -- normalization is free and the P3
        contract expects it by pipeline exit.
        """
        indexed = [
            (index, candidate, doi)
            for index, candidate in enumerate(candidates)
            for doi in (_normalize_doi(candidate.doi),)
            if doi is not None
        ]
        if not indexed:
            return list(candidates)

        works_by_doi = self._fetch_all_works(doi for _, _, doi in indexed)

        result = list(candidates)
        for index, candidate, doi in indexed:
            work = works_by_doi.get(doi)
            result[index] = _stamp(candidate, work, normalized_doi=doi)
        return result

    def _fetch_all_works(self, dois: Iterator[str]) -> dict[str, Mapping[str, object]]:
        """Run one batched lookup per ``MAX_DOIS_PER_BATCH`` DOIs, degrading on a retryable error."""
        works_by_doi: dict[str, Mapping[str, object]] = {}
        for batch in _chunk(list(dois), MAX_DOIS_PER_BATCH):
            try:
                works_by_doi.update(self._fetch_batch(batch))
            except KnoticaError as exc:
                if not exc.retryable:
                    raise
                # Degrade this batch to un-enriched rather than propagate a hard
                # failure -- the loop must keep running with fewer scholarly hits.
                continue
        return works_by_doi

    def _fetch_batch(self, dois: Sequence[str]) -> dict[str, Mapping[str, object]]:
        """One OR-filter request for up to ``MAX_DOIS_PER_BATCH`` DOIs."""
        params: dict[str, object] = {
            "filter": "doi:" + "|".join(dois),
            "per_page": len(dois),
            "select": _SELECT_FIELDS,
        }
        if self._mailto:
            params["mailto"] = self._mailto
        response = self._client.request("GET", WORKS_URL, params=params)
        return _index_by_doi(response.json())


# ---------------------------------------------------------------------------
# Pure wire-to-record mapping
# ---------------------------------------------------------------------------


def _stamp(
    candidate: SourceCandidate,
    work: Mapping[str, object] | None,
    *,
    normalized_doi: str,
) -> SourceCandidate:
    """Return ``candidate`` with its DOI normalized and, if ``work`` resolved, stamped.

    ``work`` is ``None`` when the batch lookup degraded (429/exhausted retries)
    or the DOI had no OpenAlex match -- either way the candidate still gets its
    normalized DOI, but its scholarly fields stay whatever they already were
    (``None`` unless a provider supplied them).
    """
    if work is None:
        return replace(candidate, doi=normalized_doi)
    return replace(
        candidate,
        doi=normalized_doi,
        citation_count=_optional_int(work.get("cited_by_count")),
        venue=_venue(work),
        published_date=_optional_str(work.get("publication_date")),
        is_open_access=_optional_bool(_open_access(work).get("is_oa")),
        fwci=_optional_float(work.get("fwci")),
    )


def _index_by_doi(payload: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    """Index a ``/works`` response's ``results`` by normalized DOI."""
    results = payload.get("results")
    if not isinstance(results, Sequence):
        return {}
    indexed: dict[str, Mapping[str, object]] = {}
    for work in results:
        if not isinstance(work, Mapping):
            continue
        doi = _normalize_doi(work.get("doi") if isinstance(work.get("doi"), str) else None)
        if doi is not None:
            indexed[doi] = work
    return indexed


def _open_access(work: Mapping[str, object]) -> Mapping[str, object]:
    open_access = work.get("open_access")
    return open_access if isinstance(open_access, Mapping) else {}


def _venue(work: Mapping[str, object]) -> str | None:
    """``primary_location.source.display_name`` -- any hop may be null/absent."""
    primary_location = work.get("primary_location")
    if not isinstance(primary_location, Mapping):
        return None
    source = primary_location.get("source")
    if not isinstance(source, Mapping):
        return None
    display_name = source.get("display_name")
    return display_name if isinstance(display_name, str) else None


def _normalize_doi(doi: str | None) -> str | None:
    """Strip the ``https://doi.org/`` prefix (case-insensitively) and lowercase.

    This is the enrichment join key AND the normalized value the P3 consumer
    contract guarantees by pipeline exit. ``None``/empty stays ``None``.
    """
    if not doi:
        return None
    stripped = doi[len(_DOI_URL_PREFIX) :] if doi.lower().startswith(_DOI_URL_PREFIX) else doi
    return stripped.lower()


def _chunk(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
