"""Tool-result formatting -- every outcome rides IN the result, never as an exception.

The MCP adapter must let the model *see* actionable text: a success is the read
payload (optionally with warnings); a failure is the ``{"error": {...}}`` object
from the shared error contract. This layer renders both shapes and maps the
core read layer's typed lookup exceptions onto the enum codes the model branches
on -- so a missing topic, a missing page, or a stale search cursor surface as
structured data, never a transport-level traceback.

Success and failure are distinguished exactly as :mod:`knotica.core.errors`
defines it: the presence of an ``error`` key means failure; its absence means
success. FastMCP serializes whatever dict a tool returns, so returning the
envelope dict *is* the contract.
"""

from collections.abc import Mapping
from typing import Any

from knotica.core.errors import ErrorCode, KnoticaError, err, ok
from knotica.core.page import PageNotFoundError, TopicNotFoundError
from knotica.search import InvalidCursorError


def read_ok(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render a successful read as a success envelope (reads carry no warnings)."""
    return ok(payload)


def error_envelope(error: KnoticaError) -> dict[str, Any]:
    """Render a raised :class:`KnoticaError` (e.g. ``NOT_CONFIGURED``) as an envelope."""
    return error.envelope()


def map_read_exception(exc: Exception) -> dict[str, Any]:
    """Map a core read exception to its failure envelope.

    ``KnoticaError`` already carries a code (e.g. ``NOT_CONFIGURED`` from config
    resolution, or the typed ``NOT_CONFIGURED`` a scoped lint surfaces); the
    read layer's own lookup exceptions map onto the read codes: a missing topic
    directory is ``TOPIC_NOT_FOUND``, a missing page is ``PAGE_NOT_FOUND`` (its
    message already lists nearest matches), and a malformed/stale search cursor
    is ``INVALID_CURSOR``. Anything else is a genuine bug and is re-raised.
    """
    if isinstance(exc, KnoticaError):
        return exc.envelope()
    if isinstance(exc, TopicNotFoundError):
        return err(ErrorCode.TOPIC_NOT_FOUND, str(exc))
    if isinstance(exc, PageNotFoundError):
        return err(ErrorCode.PAGE_NOT_FOUND, str(exc))
    if isinstance(exc, InvalidCursorError):
        return err(ErrorCode.INVALID_CURSOR, str(exc))
    raise exc
