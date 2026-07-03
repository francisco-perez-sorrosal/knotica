"""Tool-result formatting -- every outcome rides IN the result, never as an exception.

The MCP adapter must let the model *see* actionable text: a success is the read
payload (optionally with warnings); a failure is the ``{"error": {...}}`` object
from the shared error contract. This layer renders both shapes and maps the
core read layer's typed lookup exceptions onto the enum codes the model branches
on -- so a missing topic, a missing page, or a stale search cursor surface as
structured data, never a transport-level traceback.

Success and failure are distinguished exactly as :mod:`knotica.core.errors`
defines it: the presence of an ``error`` key means failure; its absence means
success. A success is a plain dict -- FastMCP serializes it and leaves
``isError=False``. A failure is returned as an explicit
:class:`~mcp.types.CallToolResult` with ``isError=True``: that is the
MCP-idiomatic in-band tool-error signal (exactly what "errors in result content,
never transport exceptions" means), and it stops a client mistaking, say, a
``NOT_CONFIGURED`` envelope for success. The structured ``{code, message, fix,
retryable}`` object rides in both ``structuredContent`` and the JSON text
content so every client shape can read it.
"""

import json
from collections.abc import Mapping
from typing import Any

from mcp.types import CallToolResult, TextContent

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.search import InvalidCursorError


def read_ok(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render a successful read as a success envelope dict (reads carry no warnings)."""
    return ok(payload)


def success_result(payload: Mapping[str, Any]) -> CallToolResult:
    """Wrap a success envelope as an ``isError=False`` tool result.

    Mirrors :func:`_error_result` so every tool returns a uniform
    :class:`~mcp.types.CallToolResult` (FastMCP forbids a ``dict | CallToolResult``
    union return annotation). The payload rides in ``structuredContent`` and,
    mirrored, in the JSON text content -- a success is distinguished from a
    failure exactly by the absence of an ``error`` key and ``isError=False``.
    """
    envelope = dict(payload)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structuredContent=envelope,
        isError=False,
    )


def error_envelope(error: KnoticaError) -> CallToolResult:
    """Render a raised :class:`KnoticaError` (e.g. ``NOT_CONFIGURED``) as a failure result."""
    return _error_result(error.envelope())


def map_read_exception(exc: Exception) -> CallToolResult:
    """Map a core read exception to its ``isError=True`` failure result.

    ``KnoticaError`` already carries a code (e.g. ``NOT_CONFIGURED`` from config
    resolution, or the typed ``NOT_CONFIGURED`` a scoped lint surfaces); the
    read layer's own lookup exceptions map onto the read codes: a missing topic
    directory is ``TOPIC_NOT_FOUND``, a missing page is ``PAGE_NOT_FOUND`` (its
    message already lists nearest matches), and a malformed/stale search cursor
    is ``INVALID_CURSOR``. Anything else is a genuine bug and is re-raised.
    """
    if isinstance(exc, KnoticaError):
        return _error_result(exc.envelope())
    if isinstance(exc, TopicNotFoundError):
        return _error_result(err(ErrorCode.TOPIC_NOT_FOUND, str(exc)))
    if isinstance(exc, PageNotFoundError):
        return _error_result(err(ErrorCode.PAGE_NOT_FOUND, str(exc)))
    if isinstance(exc, InvalidCursorError):
        return _error_result(err(ErrorCode.INVALID_CURSOR, str(exc)))
    raise exc


def _error_result(envelope: dict[str, Any]) -> CallToolResult:
    """Wrap a ``{"error": {...}}`` envelope as an ``isError=True`` tool result.

    The envelope rides in ``structuredContent`` (for clients that read it) and,
    mirrored, in the JSON text content (for clients that read only text) -- the
    model sees the actionable ``{code, message, fix, retryable}`` object either
    way, never a transport-level exception.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structuredContent=envelope,
        isError=True,
    )
