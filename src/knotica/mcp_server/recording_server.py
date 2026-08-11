"""One dispatch record per tool invocation, taken where the outcome is known.

``dispatch_telemetry`` is the sink; this is the single place that feeds it. The
server subclasses :class:`~mcp.server.fastmcp.FastMCP` and overrides
``call_tool`` — the one method every tool call passes through, flat and
dispatcher alike — so telemetry coverage is a property of the *server*, not a
line each of ~30 tool modules had to remember.

Three things follow from putting it here, and each was the point:

**Coverage is exhaustive by construction.** A newly registered tool is dispatched
through ``call_tool`` like every other, so it cannot be added without telemetry.
The alternative — a ``record_dispatch`` call inside each tool — is a convention,
and a convention is a thing a future tool forgets. The census test that guards
this asserts a property that the shape already guarantees, which is the right
relationship between a gate and its subject.

**The outcome is honest.** The nine dispatcher call sites this replaces recorded
*above* their handler call, where the terminal result is not yet knowable, so
every record they produced said ``ok`` whatever happened next. Here the handler
has returned, so the record carries the real error code — bucketed by
``record_dispatch`` itself, which accepts a raw knotica code and needs no mapping
table from this module. A baseline made of pre-dispatch records is all-``ok`` and
worth almost nothing; that is the failure this override exists to prevent.

**It is a subclass, not a monkeypatch — and that is load-bearing.**
``FastMCP.__init__`` runs ``_setup_handlers``, which registers the *bound method*
``self.call_tool`` with the low-level server. Assigning ``mcp.call_tool = ...``
after construction therefore rebinds an attribute nothing on the request path
reads: the low-level handler still holds the original. Measured — a patched
attribute intercepted **zero** of the calls a client actually made, while a
subclass intercepted all of them. The failure mode is silent and it flatters a
naive test: a test that calls ``mcp.call_tool(...)`` directly passes, because it
reaches the patched attribute, while production records nothing at all.

Only ``event="dispatch"`` records originate here. The ``rejected`` and
``two_phase`` events stay with the handlers that raise them — they carry
diagnostics (``valid_actions``, the billing ``phase``) that this layer cannot see,
and they are distinct events, so a dispatcher that emits one still emits exactly
one ``dispatch`` record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ContentBlock

from knotica.mcp_server.dispatch_telemetry import ROUTING_ERROR, ROUTING_OK, record_dispatch

__all__ = ["RecordingServer"]


class RecordingServer(FastMCP):
    """A ``FastMCP`` that records one dispatch per tool call, after the handler."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Dispatch as usual, then record what the call actually did.

        A raise is recorded as ``error`` and re-raised unchanged: a tool that
        raises has still been *selected*, and dropping the record would make the
        instrument quietest exactly when something is wrong.
        """
        try:
            result = await super().call_tool(name, arguments)
        except Exception:
            _record(name, arguments, ROUTING_ERROR)
            raise
        _record(name, arguments, _outcome_of(result))
        return result


def _record(name: str, arguments: Mapping[str, Any] | None, outcome: str) -> None:
    """Emit the one dispatch record for this call.

    Deliberately un-guarded: every field is coerced to ``str`` here, so
    ``json.dumps`` in the sink cannot fail on them, and the sink already swallows
    the write errors that telemetry must never turn into tool failures. Wrapping
    this in a bare ``except`` would only hide a programming error in *this*
    function while claiming the same safety.
    """
    args = arguments or {}
    # A dispatcher carries its own `action`; for a flat tool the tool name IS the
    # action, which keeps `tool`/`action` a uniform pair across the whole surface
    # (they differ iff the tool is a dispatcher).
    action = _text(args.get("action")) or name
    record_dispatch(name, action, _text(args.get("topic")), outcome=outcome)


def _outcome_of(result: object) -> str:
    """The routing outcome carried by a tool result.

    Reads the error *code*, not the ``isError`` flag: ``record_dispatch`` buckets
    a raw knotica code into the closed vocabulary, so an unrecognised code lands
    in ``error`` rather than being silently flattened to it here.
    """
    envelope = _envelope_of(result)
    if envelope is None:
        return ROUTING_OK
    error = envelope.get("error")
    if not isinstance(error, Mapping):
        return ROUTING_OK
    code = error.get("code")
    return str(code) if code else ROUTING_ERROR


def _envelope_of(result: object) -> Mapping[str, Any] | None:
    """The knotica envelope inside a tool result, or ``None`` when it carries none.

    Tools return an explicit ``CallToolResult`` (see ``envelope.py``), but the
    plain-mapping case is handled too so a tool that returns a bare envelope is
    measured rather than silently counted as a success.
    """
    if isinstance(result, CallToolResult):
        structured = result.structuredContent
        return structured if isinstance(structured, Mapping) else None
    if isinstance(result, Mapping):
        return result
    return None


def _text(value: Any) -> str:
    """A trimmed string field, or ``""`` when the caller passed none."""
    return value.strip() if isinstance(value, str) else ""
