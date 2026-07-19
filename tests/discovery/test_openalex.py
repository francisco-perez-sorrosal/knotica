"""Behavioral contract tests for the OpenAlex reputability enricher.

``OpenAlexEnricher.enrich`` is the batching-and-cost-discipline seam flagged
in the plan's Risk Assessment (row 3: OpenAlex's free tier is now a USD
credit budget) and pre-mortem guard 4 (a hardcoded credit ceiling would
silently exhaust mid-loop-run). Four behavior groups are under test, seeded
from SYSTEMS_PLAN.md's live-verified Confirmed-API-Shapes Appendix:

- **batching discipline**: candidates with a resolvable DOI are grouped into
  OR-filter lookups of at most 50 per request; a batch larger than 50 issues
  ``ceil(n / 50)`` requests, never one request per candidate;
- **DOI normalization**: the enricher's join key strips the
  ``https://doi.org/`` prefix and lowercases, matching a candidate's DOI
  (given in whatever case/form the provider supplied) against the response's
  DOI regardless of casing or prefix;
- **graceful degradation on 429**: a rate-limited response must not raise --
  the candidates that would have been enriched come back unenriched (scholarly
  fields ``None``), never propagating an exception into the caller;
- **DOI-less passthrough**: a candidate with no DOI is returned unchanged, with
  every scholarly field left ``None``  -- never dropped from the list.

All response payloads are constructed inline in this file (no committed
fixture file is created here -- the implementer's
own committed fixture, if any, is a separate concern) using field names
verified live per the Confirmed-API-Shapes Appendix: ``cited_by_count``,
``primary_location.source.display_name``, ``open_access.is_oa``,
``publication_date``, ``doi`` (full ``https://doi.org/10.…`` URL), ``fwci``.

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.openalex`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.
"""

import httpx

from knotica.discovery.http import SearchHttpClient


def _openalex_module():
    import knotica.discovery.openalex as openalex

    return openalex


def _records_module():
    import knotica.discovery.records as records

    return records


def _candidate(records, **overrides):
    kwargs = {
        "url": "https://example.com/papers/gap-fill",
        "title": "Gap-Fill Discovery for Compounding Wikis",
        "snippet": "A short abstract.",
        "source_provider": "exa",
    }
    kwargs.update(overrides)
    return records.SourceCandidate(**kwargs)


def _work(*, doi: str, cited_by_count: int = 10, venue: str | None = "NeurIPS") -> dict:
    """One OpenAlex ``/works`` result, shaped per the live-verified appendix."""
    return {
        "doi": doi,
        "cited_by_count": cited_by_count,
        "fwci": 1.42,
        "publication_date": "2025-12-01",
        "open_access": {"is_oa": True},
        "primary_location": {"source": {"display_name": venue}},
    }


def _enricher(openalex, transport, **client_kwargs):
    client_kwargs.setdefault("sleep", lambda _seconds: None)
    http_client = SearchHttpClient(transport=transport, **client_kwargs)
    return openalex.OpenAlexEnricher(mailto="gapfill@example.com", http_client=http_client)


# ---------------------------------------------------------------------------
# Batching: <=50 DOIs per request, ceil(n/50) requests for a larger batch
# ---------------------------------------------------------------------------


def test_a_batch_of_fifty_or_fewer_dois_produces_exactly_one_enrichment_request():
    openalex = _openalex_module()
    records = _records_module()
    seen_requests: list[httpx.Request] = []
    candidates = [
        _candidate(records, url=f"https://example.com/paper-{i}", doi=f"https://doi.org/10.1/{i}")
        for i in range(50)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        works = [_work(doi=c.doi) for c in candidates]
        return httpx.Response(200, json={"results": works})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enricher.enrich(candidates)

    assert len(seen_requests) == 1, "a <=50 candidate batch must issue exactly one request"


def test_a_batch_of_fifty_one_dois_produces_two_enrichment_requests():
    openalex = _openalex_module()
    records = _records_module()
    seen_requests: list[httpx.Request] = []
    candidates = [
        _candidate(records, url=f"https://example.com/paper-{i}", doi=f"https://doi.org/10.1/{i}")
        for i in range(51)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"results": []})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enricher.enrich(candidates)

    assert len(seen_requests) == 2, "ceil(51/50) == 2 requests, never one call per candidate"


def test_no_single_request_carries_more_than_fifty_dois():
    openalex = _openalex_module()
    records = _records_module()
    seen_requests: list[httpx.Request] = []
    candidates = [
        _candidate(records, url=f"https://example.com/paper-{i}", doi=f"https://doi.org/10.1/{i}")
        for i in range(120)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"results": []})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enricher.enrich(candidates)

    assert len(seen_requests) == 3, "ceil(120/50) == 3 requests"
    for request in seen_requests:
        query_string = str(request.url.params.get("filter", "")) or str(request.url)
        doi_count = query_string.count("10.1/")
        assert doi_count <= 50, f"a single request encoded {doi_count} DOIs, exceeding the cap"


# ---------------------------------------------------------------------------
# DOI normalization: bare 10.xxxx/... form drives the join, regardless of
# the response's URL-prefixed / mixed-case DOI shape
# ---------------------------------------------------------------------------


def test_a_url_prefixed_mixed_case_doi_in_the_response_still_joins_and_stamps_the_candidate():
    openalex = _openalex_module()
    records = _records_module()
    candidate = _candidate(records, doi="https://doi.org/10.1234/ABCDE")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [_work(doi="https://doi.org/10.1234/abcde", cited_by_count=142)]}
        )

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([candidate])

    assert enriched[0].citation_count == 142, (
        "the join must be case-insensitive and prefix-agnostic, matching the "
        "candidate's DOI against the response's DOI"
    )


def test_the_stamped_doi_is_normalized_to_the_bare_form():
    openalex = _openalex_module()
    records = _records_module()
    candidate = _candidate(records, doi="https://doi.org/10.1234/ABCDE")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_work(doi="https://doi.org/10.1234/abcde")]})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([candidate])

    assert enriched[0].doi == "10.1234/abcde", (
        "by pipeline exit the DOI must be the bare, lowercased join key -- "
        "no https://doi.org/ prefix, no uppercase"
    )


def test_enrichment_stamps_venue_open_access_and_fwci_from_the_response():
    openalex = _openalex_module()
    records = _records_module()
    candidate = _candidate(records, doi="https://doi.org/10.1234/abcde")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_work(doi="https://doi.org/10.1234/abcde")]})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([candidate])[0]

    assert enriched.venue == "NeurIPS"
    assert enriched.is_open_access is True
    assert enriched.fwci == 1.42
    assert enriched.published_date == "2025-12-01"


# ---------------------------------------------------------------------------
# DOI-less passthrough: the DOI-less passthrough behavior
# ---------------------------------------------------------------------------


def test_a_candidate_with_no_doi_returns_unchanged_with_scholarly_fields_none():
    openalex = _openalex_module()
    records = _records_module()
    candidate = _candidate(records, doi=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([candidate])

    assert len(enriched) == 1
    assert enriched[0].doi is None
    assert enriched[0].citation_count is None
    assert enriched[0].is_open_access is None
    assert enriched[0].fwci is None


def test_a_mix_of_doi_and_doi_less_candidates_returns_both_correctly_stamped():
    openalex = _openalex_module()
    records = _records_module()
    with_doi = _candidate(
        records, url="https://example.com/paper-a", doi="https://doi.org/10.1234/abcde"
    )
    without_doi = _candidate(records, url="https://example.com/paper-b", doi=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_work(doi="https://doi.org/10.1234/abcde")]})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([with_doi, without_doi])

    assert len(enriched) == 2, "both candidates must survive enrichment, doi-less included"
    by_url = {c.url: c for c in enriched}
    assert by_url["https://example.com/paper-a"].citation_count is not None
    assert by_url["https://example.com/paper-b"].citation_count is None


# ---------------------------------------------------------------------------
# Graceful degradation on 429 -- never an exception, never a hard failure
# ---------------------------------------------------------------------------


def test_a_429_response_degrades_to_unenriched_candidates_instead_of_raising():
    """Pre-mortem guard 4: a USD-credit-exhausted 429 must not propagate as a
    hard failure into the caller -- the loop's discovery pass is best-effort,
    so an enrichment outage degrades to un-enriched candidates, not a crash."""
    openalex = _openalex_module()
    records = _records_module()
    candidate = _candidate(records, doi="https://doi.org/10.1234/abcde")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-ratelimit-remaining-usd": "0.0", "x-ratelimit-limit-usd": "0.1"},
            json={"error": "rate limited"},
        )

    enricher = _enricher(openalex, httpx.MockTransport(handler), max_retries=0)

    enriched = enricher.enrich([candidate])

    assert len(enriched) == 1, "the candidate must still be returned, not dropped"
    assert enriched[0].citation_count is None, "un-enriched -- scholarly fields stay None"


def test_low_remaining_usd_budget_does_not_block_enrichment_from_succeeding():
    """The enricher must read rate-limit headers dynamically rather than
    hardcoding a credit ceiling (Risk Assessment row 3) -- a response that
    reports an almost-exhausted USD budget on a *successful* (200) call must
    still enrich normally; the header value alone must never gate success."""
    openalex = _openalex_module()
    records = _records_module()
    candidate = _candidate(records, doi="https://doi.org/10.1234/abcde")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-ratelimit-remaining-usd": "0.0001", "x-ratelimit-limit-usd": "0.1"},
            json={"results": [_work(doi="https://doi.org/10.1234/abcde", cited_by_count=7)]},
        )

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([candidate])

    assert enriched[0].citation_count == 7, (
        "a successful response must be trusted regardless of how little USD "
        "budget remains in its headers -- no hardcoded threshold blocks it"
    )


def test_enrich_of_an_empty_candidate_list_makes_no_request_and_returns_empty():
    openalex = _openalex_module()
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"results": []})

    enricher = _enricher(openalex, httpx.MockTransport(handler))
    enriched = enricher.enrich([])

    assert enriched == []
    assert not seen_requests, "an empty candidate list must not trigger a network call"
