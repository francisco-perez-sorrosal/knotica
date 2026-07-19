"""The one outbound-network boundary for discovery -- a thin ``httpx`` wrapper.

Every discovery adapter (Exa, you.com) and the OpenAlex enricher reach the network
only through :class:`SearchHttpClient`. It concentrates four concerns the adapters
would otherwise each re-implement, so the credential-hygiene and rate-limit rules
are enforced in exactly one place:

* **Auth-header injection.** The credential is supplied once at construction, as a
  header (``x-api-key`` for Exa, ``Authorization: Bearer`` for you.com), and injected
  into every request. The wrapper never accepts a credential in a URL query string,
  so a key cannot leak into a logged URL.
* **Credential never logged or surfaced.** The auth headers live only on the private
  transport client; they are never logged, never echoed, and never included in a
  raised :class:`~knotica.core.errors.KnoticaError` message. Error messages carry the
  method, the query-stripped URL, and the HTTP status only -- never headers, never a
  raw request repr. This mirrors ``evals.llm``'s never-surface-the-credential rule.
* **Bounded retry honoring ``Retry-After``.** Transient statuses (429 + 5xx) and
  connection errors retry up to :data:`DEFAULT_MAX_RETRIES` times, sleeping for the
  ``Retry-After`` header when the server sends one and exponential backoff otherwise.
  The retry cadence is read from the response, never hardcoded to an aggressive rate.
* **Typed failure.** Exhausted retries, a non-retryable 4xx (auth rejection), or a
  final connection error raise a typed ``SEARCH_API_ERROR`` -- the shared envelope
  contract -- so adapters render an actionable result, never a raw traceback.

``httpx`` is imported lazily inside :meth:`SearchHttpClient.__init__`, so ``import
knotica.discovery.http`` succeeds with only the base environment and never drags a
heavy client onto the MCP cold-start path (the import-boundary fitness test asserts
this). Tests inject an ``httpx.MockTransport`` and a fake ``sleep`` so no test path
touches the real network or blocks on a real backoff.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from knotica.core.errors import ErrorCode, KnoticaError

if TYPE_CHECKING:  # httpx is imported lazily at runtime -- never at module import.
    import httpx

__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT_SECONDS",
    "RETRYABLE_STATUS_CODES",
    "SearchHttpClient",
]

#: Statuses worth retrying: rate-limit (429) plus the transient server errors.
#: A 5xx or 429 may clear on its own; every 4xx below 429 (400/401/403/404) is a
#: caller/credential problem that the same request will not fix, so it fails fast.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})

#: How many *additional* attempts a transient failure gets after the first try.
DEFAULT_MAX_RETRIES = 3

#: Base of the exponential backoff (seconds) used when the server sends no
#: ``Retry-After`` header: sleep ``base * 2**attempt``, capped at :data:`_MAX_BACKOFF_SECONDS`.
DEFAULT_BACKOFF_BASE_SECONDS = 0.5

#: Per-request timeout (seconds) -- a stuck socket must not hang the loop.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Upper bound on any single backoff sleep, so a hostile ``Retry-After`` cannot
#: park the loop for minutes.
_MAX_BACKOFF_SECONDS = 30.0


class SearchHttpClient:
    """A thin, credential-hygienic ``httpx.Client`` wrapper for discovery adapters.

    ``auth_headers`` supplies the credential once (as request headers -- never a URL
    query param); it lands only on the private ``httpx`` client and is never logged
    or surfaced in an error. ``transport`` and ``sleep`` are injection seams for
    tests: pass an ``httpx.MockTransport`` to serve canned responses with no network,
    and a no-op ``sleep`` to skip real backoff. ``httpx`` is imported here, lazily,
    so merely importing this module never pulls it onto the cold-start path.
    """

    def __init__(
        self,
        *,
        auth_headers: Mapping[str, str] | None = None,
        base_headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        import httpx

        headers: dict[str, str] = dict(base_headers or {})
        headers.update(auth_headers or {})
        self._max_retries = max_retries
        self._backoff_base = DEFAULT_BACKOFF_BASE_SECONDS
        self._sleep = sleep
        self._client = httpx.Client(headers=headers, timeout=timeout, transport=transport)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        """Issue one request with bounded retry, or raise ``SEARCH_API_ERROR``.

        A ``2x``/``3xx`` (any non-error status) response is returned to the caller
        so it can read ``x-ratelimit-*`` headers and the body. A retryable status or
        a connection error is retried up to ``max_retries`` times, honoring
        ``Retry-After``; a non-retryable status or exhausted retries raise a typed
        error. The credential never appears in that error.
        """
        import httpx

        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method, url, headers=dict(headers or {}), params=params, json=json
                )
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise self._connection_error(method, url) from exc
                self._sleep(self._backoff_delay(attempt))
                attempt += 1
                continue

            if response.status_code < 400:
                return response
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                self._sleep(self._retry_after_delay(response, attempt))
                attempt += 1
                continue
            raise self._status_error(method, url, response)

    def close(self) -> None:
        """Close the underlying ``httpx`` client and release its connections."""
        self._client.close()

    def __enter__(self) -> SearchHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- retry timing -------------------------------------------------------

    def _retry_after_delay(self, response: httpx.Response, attempt: int) -> float:
        """Seconds to wait before the next attempt: ``Retry-After`` if sent, else backoff.

        The server's ``Retry-After`` (delta-seconds or an HTTP date) is honored so
        the cadence is read from the response, never hardcoded; a missing or
        unparseable header falls back to exponential backoff. Either way the wait is
        capped at :data:`_MAX_BACKOFF_SECONDS`.
        """
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        if retry_after is not None:
            return min(retry_after, _MAX_BACKOFF_SECONDS)
        return self._backoff_delay(attempt)

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff ``base * 2**attempt``, capped at :data:`_MAX_BACKOFF_SECONDS`."""
        return min(self._backoff_base * (2**attempt), _MAX_BACKOFF_SECONDS)

    # -- typed errors (credential never appears in any message) -------------

    def _status_error(self, method: str, url: str, response: httpx.Response) -> KnoticaError:
        """Map a final non-OK status to ``SEARCH_API_ERROR`` -- retryable only for 429/5xx.

        The message carries the method, the query-stripped URL, and the status only;
        headers and the request body (which hold the credential) never appear.
        """
        status = response.status_code
        retryable = status in RETRYABLE_STATUS_CODES
        detail = "exhausted retries" if retryable else "a non-retryable response"
        return KnoticaError(
            ErrorCode.SEARCH_API_ERROR,
            (
                f"Search request {method.upper()} {_strip_query(url)} failed because the"
                f" provider returned HTTP {status} ({detail})."
            ),
            retryable=retryable,
        )

    def _connection_error(self, method: str, url: str) -> KnoticaError:
        """Map an exhausted connection failure to a retryable ``SEARCH_API_ERROR``."""
        return KnoticaError(
            ErrorCode.SEARCH_API_ERROR,
            (
                f"Search request {method.upper()} {_strip_query(url)} failed because the"
                " connection to the provider dropped before a response after retries."
            ),
            retryable=True,
        )


def _strip_query(url: str) -> str:
    """Drop any ``?query`` from ``url`` so a query-carried value never lands in a log.

    Discovery uses header auth (no key in the URL), but stripping the query is a
    belt-and-suspenders guard: no future query param -- a ``mailto``, a token -- can
    leak through an error message.
    """
    return url.split("?", 1)[0]


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP date) into seconds.

    Returns ``None`` when the header is absent or unparseable, so the caller falls
    back to exponential backoff. A date in the past clamps to ``0.0``.
    """
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(stripped)
    try:
        when = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    now = datetime.now(timezone.utc) if when.tzinfo is not None else datetime.now()
    return max((when - now).total_seconds(), 0.0)
