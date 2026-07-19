"""Behavioral contract tests for the shared discovery HTTP wrapper.

Every test drives the wrapper against an ``httpx.MockTransport`` fake, with a
no-op ``sleep`` injected so no test ever blocks on real backoff -- no test in
this file makes a real network call or waits on a real clock. Three behavior
groups are under test:

- **auth-header injection without credential leakage**: the key must reach
  the request as a header, and must never appear in a raised exception's
  message or in the module's logging;
- **bounded retry with backoff**: a 429 honors ``Retry-After``; a 5xx that
  exhausts its retry budget surfaces the typed, retryable ``SEARCH_API_ERROR``;
- **rate-limit header parsing**: the response the wrapper returns carries the
  provider's ``x-ratelimit-*`` headers untouched, so a caller (an adapter) can
  read them dynamically rather than the wrapper hardcoding a rate.

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.http`` is still in flight (concurrent implementer). This
file was written without reading the implementer's code.
"""

import logging

import httpx
import pytest

#: A sentinel that must never appear in any exception message, log record, or
#: repr the wrapper produces. Not a real credential.
SENTINEL_KEY = "sk-search-SENTINEL-do-not-leak-0000000000"

SEARCH_URL = "https://example.test/search"


def _http_module():
    import knotica.discovery.http as http

    return http


def _errors_module():
    import knotica.core.errors as errors

    return errors


def _client(module, transport, **kwargs):
    """Build the wrapper against a fake transport with no-op sleep -- no real
    network I/O and no test blocking on real backoff delay."""
    kwargs.setdefault("sleep", lambda _seconds: None)
    return module.SearchHttpClient(
        auth_headers={"x-api-key": SENTINEL_KEY},
        transport=transport,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Auth-header injection
# ---------------------------------------------------------------------------


def test_the_api_key_is_sent_as_a_request_header_not_a_query_param():
    """Query-string keys leak into URLs (logs, proxies, referrer headers);
    the wrapper must only ever place the credential in a header."""
    http = _http_module()
    seen_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(http, httpx.MockTransport(handler))
    client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert SENTINEL_KEY not in str(request.url), "the key must never appear in the request URL"
    header_values = list(request.headers.values())
    assert any(SENTINEL_KEY in value for value in header_values), (
        "the key must be present in some request header"
    )


# ---------------------------------------------------------------------------
# Credential never appears in a raised exception's message
# ---------------------------------------------------------------------------


def test_credential_never_appears_in_the_exception_raised_on_exhausted_retries():
    http = _http_module()
    errors = _errors_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    client = _client(http, httpx.MockTransport(handler), max_retries=2)

    with pytest.raises(errors.KnoticaError) as exc_info:
        client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert SENTINEL_KEY not in exc_info.value.message
    assert SENTINEL_KEY not in (exc_info.value.fix or "")


def test_credential_never_appears_in_a_logged_record(caplog: pytest.LogCaptureFixture):
    http = _http_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with caplog.at_level(logging.DEBUG):
        client = _client(http, httpx.MockTransport(handler))
        client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    leaked = [
        record.getMessage() for record in caplog.records if SENTINEL_KEY in record.getMessage()
    ]
    assert not leaked, f"credential leaked into log records: {leaked}"


# ---------------------------------------------------------------------------
# Bounded retry / backoff honoring Retry-After
# ---------------------------------------------------------------------------


def test_a_429_with_retry_after_is_retried_and_eventually_succeeds():
    http = _http_module()
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"ok": True})

    client = _client(http, httpx.MockTransport(handler), max_retries=3)
    response = client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert response.status_code == 200
    assert len(attempts) == 2, "the wrapper must retry once after the 429 and then succeed"


def test_a_5xx_that_exhausts_retries_raises_the_typed_search_api_error():
    http = _http_module()
    errors = _errors_module()
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503, json={"error": "unavailable"})

    client = _client(http, httpx.MockTransport(handler), max_retries=2)

    with pytest.raises(errors.KnoticaError) as exc_info:
        client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert exc_info.value.code == errors.ErrorCode.SEARCH_API_ERROR
    assert len(attempts) == 3, "one initial attempt plus max_retries retries, then raise"


def test_search_api_error_is_marked_retryable_in_the_error_contract():
    """SEARCH_API_ERROR's retryable flag lives in the shared error contract
    (core.errors), not re-derived here -- this pins that the wrapper's raised
    error keeps that contract's retryable=True semantics intact."""
    errors = _errors_module()
    assert errors.ErrorCode.SEARCH_API_ERROR in errors.RETRYABLE_CODES


def test_retry_budget_is_bounded_not_unbounded():
    """A transport that always fails must not retry forever -- the wrapper's
    request must return control after exactly the configured retry budget."""
    http = _http_module()
    errors = _errors_module()
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(500, json={"error": "server error"})

    client = _client(http, httpx.MockTransport(handler), max_retries=1)

    with pytest.raises(errors.KnoticaError):
        client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert len(attempts) == 2, "one initial attempt plus exactly 1 retry, never more"


def test_a_non_retryable_4xx_raises_immediately_without_retrying():
    """An auth rejection (401/403) or not-found (404) is a caller/credential
    problem the same request will not fix -- it must fail fast, not burn the
    retry budget on a status that can never succeed."""
    http = _http_module()
    errors = _errors_module()
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _client(http, httpx.MockTransport(handler), max_retries=3)

    with pytest.raises(errors.KnoticaError) as exc_info:
        client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert len(attempts) == 1, "a non-retryable status must not be retried at all"
    assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# Rate-limit headers: the returned response carries them untouched
# ---------------------------------------------------------------------------


def test_a_successful_responses_rate_limit_headers_are_readable_by_the_caller():
    """The wrapper must not strip or hide provider rate-limit headers -- an
    adapter reads them dynamically off the response the wrapper returns,
    rather than the wrapper hardcoding a rate."""
    http = _http_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-ratelimit-limit": "1000", "x-ratelimit-remaining": "42"},
            json={"ok": True},
        )

    client = _client(http, httpx.MockTransport(handler))
    response = client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert response.headers["x-ratelimit-limit"] == "1000"
    assert response.headers["x-ratelimit-remaining"] == "42"


def test_usd_denominated_rate_limit_headers_survive_untouched():
    """OpenAlex's headers are USD-credit denominated -- the wrapper must pass
    them through as-is rather than assuming an integer request-count shape."""
    http = _http_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-usd": "0.1",
                "x-ratelimit-remaining-usd": "0.0873",
            },
            json={"ok": True},
        )

    client = _client(http, httpx.MockTransport(handler))
    response = client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert response.headers["x-ratelimit-limit-usd"] == "0.1"
    assert response.headers["x-ratelimit-remaining-usd"] == "0.0873"


def test_retry_after_is_honored_using_the_seconds_form():
    """A numeric Retry-After (delta-seconds, per HTTP spec) drives one retry;
    proven by observing exactly two attempts against a transport that
    succeeds on the second try."""
    http = _http_module()
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "slow down"})
        return httpx.Response(200, json={"ok": True})

    client = _client(http, httpx.MockTransport(handler), max_retries=3)
    response = client.request("GET", SEARCH_URL, params={"q": "agentic gap-fill"})

    assert response.status_code == 200
    assert len(attempts) == 2
