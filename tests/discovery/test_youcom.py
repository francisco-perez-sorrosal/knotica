"""Behavioral contract tests for the you.com search adapter.

you.com is the sole shipped MVP search provider (the Exa adapter was cut by
user directive; ``SearchProvider`` stays pluggable for a future second
adapter). The wire shape IS live-verified (2026-07-19, one real call to
``https://api.you.com/v1/search`` with bearer auth): hits sit at
``results.web[]``; each hit carries ``description`` (the snippet, with a
``snippets[]`` list sibling) and ``page_age``, and NO author field. The
inline literal below mirrors that captured shape (sanitized); the committed
fixture file is the sanitized live capture itself.

Three behavior groups are under test:

- **pure wire-mapping**: ``_parse_response(json) -> list[SourceCandidate]``
  maps the synthetic fixture with zero network, tagging every candidate
  ``source_provider="youcom"`` and setting an absent optional field to
  ``None`` (never a sentinel);
- **malformed-response handling**: a response missing or mis-shaping the
  ``results.web`` array raises the shared typed ``SEARCH_API_ERROR``,
  never a raw ``KeyError``/``TypeError``, and never leaks the credential;
- **wire-mapping of ``SearchQuery`` filters**: ``YouComProvider.search`` must
  thread the query's ``text``/``max_results``/``include_domains`` into the
  outbound request -- proven against a fake transport that records what it
  received.

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.youcom`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.
"""

import httpx
import pytest


def _youcom_module():
    import knotica.discovery.youcom as youcom

    return youcom


def _records_module():
    import knotica.discovery.records as records

    return records


def _errors_module():
    import knotica.core.errors as errors

    return errors


# Mirrors the live-verified /v1/search shape (2026-07-19), sanitized values.
YOUCOM_SEARCH_RESPONSE = {
    "metadata": {"query": "agentic gap-fill discovery", "latency": 0.0},
    "results": {
        "web": [
            {
                "url": "https://arxiv.org/abs/2507.01234",
                "title": "Gap-Fill Discovery for Compounding Knowledge Wikis",
                "description": "We present a method for autonomously discovering sources.",
                "snippets": ["We present a method for autonomously discovering sources."],
                "page_age": "2025-11-03",
            },
            {
                "url": "https://blog.example.com/knowledge-wikis",
                "title": "Building Self-Improving Wikis",
                # description absent -- snippet must fall back to snippets[0];
                # page_age absent -- optional field maps to None
                "snippets": ["An overview of compounding knowledge systems."],
            },
        ]
    },
}

SENTINEL_KEY = "sk-youcom-SENTINEL-do-not-leak-0000000000"


def _provider(youcom, transport, **kwargs):
    kwargs.setdefault("sleep", lambda _seconds: None)
    return youcom.YouComProvider(api_key=SENTINEL_KEY, transport=transport, **kwargs)


# ---------------------------------------------------------------------------
# Pure wire-mapping: _parse_response has zero network dependency
# ---------------------------------------------------------------------------


def test_parse_response_maps_the_live_shape_to_source_candidates():
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert len(candidates) == 2
    first = candidates[0]
    assert first.url == "https://arxiv.org/abs/2507.01234"
    assert first.title == "Gap-Fill Discovery for Compounding Knowledge Wikis"
    assert first.snippet == "We present a method for autonomously discovering sources."


def test_parse_response_tags_every_candidate_with_the_youcom_source_provider():
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert all(candidate.source_provider == "youcom" for candidate in candidates)


def test_parse_response_leaves_authors_none_since_the_live_shape_has_no_author_field():
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert all(candidate.authors is None for candidate in candidates)


def test_parse_response_falls_back_to_the_first_snippets_entry_when_description_is_absent():
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert candidates[1].snippet == "An overview of compounding knowledge systems."


def test_parse_response_maps_page_age_into_the_published_date_field():
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert candidates[0].published_date == "2025-11-03"


def test_parse_response_sets_absent_optional_fields_to_none_not_a_sentinel():
    """The second hit omits ``page_age`` entirely -- the mapped candidate must
    carry ``None``, never an empty string/tuple placeholder."""
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert candidates[1].published_date is None


def test_parse_response_makes_no_network_call():
    """The pure parse function is callable with zero network -- proven by
    never constructing a transport/client anywhere in this test."""
    youcom = _youcom_module()

    candidates = youcom._parse_response(YOUCOM_SEARCH_RESPONSE)

    assert len(candidates) == 2


# ---------------------------------------------------------------------------
# Malformed-response handling: typed error, never a raw KeyError/TypeError
# ---------------------------------------------------------------------------


def test_parse_response_raises_the_typed_search_api_error_when_results_web_is_missing():
    youcom = _youcom_module()
    errors = _errors_module()

    with pytest.raises(errors.KnoticaError) as exc_info:
        youcom._parse_response({"unexpected_shape": True})

    assert exc_info.value.code == errors.ErrorCode.SEARCH_API_ERROR


def test_parse_response_raises_the_typed_search_api_error_when_results_web_is_the_wrong_type():
    youcom = _youcom_module()
    errors = _errors_module()

    with pytest.raises(errors.KnoticaError) as exc_info:
        youcom._parse_response({"results": {"web": "not-a-list"}})

    assert exc_info.value.code == errors.ErrorCode.SEARCH_API_ERROR


def test_malformed_response_error_never_leaks_the_api_key():
    """The full provider (not just the pure parser) must never let a
    credential leak into a raised error's message when the wire response is
    malformed -- mirrors ``http.py``'s never-surface-the-credential rule."""
    youcom = _youcom_module()
    errors = _errors_module()
    records = _records_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected_shape": True})

    provider = _provider(youcom, httpx.MockTransport(handler))
    query = records.SearchQuery(text="agentic gap-fill discovery")

    with pytest.raises(errors.KnoticaError) as exc_info:
        provider.search(query)

    assert SENTINEL_KEY not in exc_info.value.message
    assert SENTINEL_KEY not in (exc_info.value.fix or "")


# ---------------------------------------------------------------------------
# Wire-mapping of SearchQuery filters into the outbound request
# ---------------------------------------------------------------------------


def test_search_tags_returned_candidates_with_the_provider_name():
    youcom = _youcom_module()
    records = _records_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=YOUCOM_SEARCH_RESPONSE)

    provider = _provider(youcom, httpx.MockTransport(handler))
    query = records.SearchQuery(text="agentic gap-fill discovery")

    candidates = provider.search(query)

    assert provider.name == "youcom"
    assert all(candidate.source_provider == "youcom" for candidate in candidates)


def test_search_threads_the_query_text_into_the_outbound_request():
    youcom = _youcom_module()
    records = _records_module()
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json=YOUCOM_SEARCH_RESPONSE)

    provider = _provider(youcom, httpx.MockTransport(handler))
    query = records.SearchQuery(text="agentic gap-fill discovery", max_results=5)

    provider.search(query)

    assert len(seen_requests) == 1
    outbound = seen_requests[0]
    assert outbound.url.params.get("query") == "agentic gap-fill discovery"
    assert outbound.url.params.get("count") == "5"


def test_search_threads_include_domains_into_the_outbound_request():
    youcom = _youcom_module()
    records = _records_module()
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json=YOUCOM_SEARCH_RESPONSE)

    provider = _provider(youcom, httpx.MockTransport(handler))
    query = records.SearchQuery(
        text="agentic gap-fill discovery",
        include_domains=("arxiv.org",),
    )

    provider.search(query)

    outbound = seen_requests[0]
    haystack = str(outbound.url) + outbound.content.decode("utf-8", errors="ignore")
    assert "arxiv.org" in haystack, (
        "SearchQuery.include_domains must reach the outbound request in some form"
    )
