"""Every registered tool is measured — proven against the registry, not a list.

The telemetry sink is only worth what its coverage is worth. Before this census
nine of the thirty-five registrations recorded anything, and all nine were
dispatchers, so the instrument could see *which action* a client picked inside a
domain and could not see which domain it picked at all. A baseline built on that
would answer the wrong question, and it gates a one-way door.

So the census enumerates from ``list_tools(build_full_server())`` — the same
registry the client reads — and never from a list written here. A hand-written
list is a second source of truth that goes stale the moment a tool is added, and
the one thing this file must catch *is* a tool being added.

**Nothing here can bill.** Every call is aimed at a vault path that does not
exist, so `with_resolved_vault` refuses with ``NOT_CONFIGURED`` before any tool
body runs — which is upstream of every model call on the surface, and upstream of
the two-phase confirm that guards the billed actions besides. Only the arguments
a tool's own schema marks ``required`` are supplied, so no ``confirm`` is ever
sent and no second leg is reachable; ``mode`` is pinned to ``dry-run`` wherever
it exists. The no-billing claim is not left as prose — `test_the_census_bills_nothing`
asserts it against the sink.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from support.dispatch import build_full_server, call_tool, list_tools

#: A path no vault is configured at, so every call fails closed at vault
#: resolution — upstream of every model call and every mutation on the surface.
_UNCONFIGURED_VAULT = "/nonexistent/knotica-census-vault"


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the telemetry sink at a scratch directory (the whole seam)."""
    directory = tmp_path / "telemetry"
    monkeypatch.setenv("KNOTICA_TELEMETRY_DIR", str(directory))
    return directory


def _records(sink: Path) -> list[dict[str, Any]]:
    """Every record the sink holds, in write order across day files."""
    return [
        json.loads(line)
        for path in sorted(sink.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _placeholder(spec: dict[str, Any]) -> Any:
    """A schema-valid value for one required argument.

    Type-driven rather than name-driven: a name-keyed table would need an entry
    per argument and would silently stop covering a tool that renamed one.
    """
    declared = spec.get("type")
    if isinstance(declared, list):
        declared = next((item for item in declared if item != "null"), "string")
    return {
        "string": "census",
        "integer": 1,
        "number": 1.0,
        "boolean": False,
        "array": [],
        "object": {},
    }.get(str(declared), "census")


def _arguments_for(schema: dict[str, Any]) -> dict[str, Any]:
    """Minimal schema-valid arguments that dispatch and cannot bill."""
    properties = schema.get("properties") or {}
    arguments = {
        name: _placeholder(properties.get(name) or {}) for name in schema.get("required") or []
    }
    if "vault" in properties:
        arguments["vault"] = _UNCONFIGURED_VAULT
    if "mode" in properties:
        arguments["mode"] = "dry-run"
    return arguments


def _drive_every_tool(sink: Path) -> tuple[dict[str, bool], list[dict[str, Any]]]:
    """Call every registered tool once.

    Returns each tool's real ``isError`` verdict alongside what the sink recorded,
    so a test can compare the two rather than guess what the surface should do.
    """
    server = build_full_server()
    tools = list_tools(server)
    assert tools, "the registry came back empty — this census would pass vacuously"
    failed: dict[str, bool] = {}
    for tool in tools:
        result = call_tool(server, tool.name, _arguments_for(tool.inputSchema or {}))
        failed[tool.name] = bool(getattr(result, "isError", False))
    return failed, _records(sink)


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------


def test_every_registered_tool_emits_exactly_one_dispatch_record(sink: Path) -> None:
    """The whole point: coverage is total, and nothing double-counts.

    Asserted as an equality between two multisets rather than a count, so the
    failure message names the tool that is missing or the one that recorded
    twice — a bare ``len(...) == 35`` would only say the number moved.
    """
    failed, records = _drive_every_tool(sink)

    dispatched = Counter(r["tool"] for r in records if r["event"] == "dispatch")

    assert dispatched == Counter(failed.keys())


def test_the_census_covers_flat_tools_and_dispatchers_alike(sink: Path) -> None:
    """The gap this closes was shaped: only dispatchers were instrumented.

    A dispatcher records the action it was given; a flat tool records its own
    name as the action. Asserting both shapes appear keeps a future change that
    silently reverts to dispatcher-only coverage from passing the census above.
    """
    _failed, records = _drive_every_tool(sink)
    dispatch = [r for r in records if r["event"] == "dispatch"]

    flat = [r for r in dispatch if r["action"] == r["tool"]]
    dispatchers = [r for r in dispatch if r["action"] != r["tool"]]

    assert flat, "no flat tool recorded — coverage has regressed to dispatchers only"
    assert dispatchers, "no dispatcher recorded its own action"


def test_a_recorded_outcome_agrees_with_what_the_tool_actually_returned(sink: Path) -> None:
    """The exact property the old pre-dispatch call sites could not have.

    They recorded ``ok`` *above* the handler, where the terminal result does not
    exist yet, so a run in which every call failed still reported all-``ok``.
    Recording after the handler makes the label checkable against the thing it
    labels — so this compares the record to each tool's real ``isError``, rather
    than assuming what the surface ought to do. It is deliberately not "nothing
    reads ok": `open_dashboard` genuinely succeeds without a vault, and a test
    that forbade that would be asserting a fact about the surface instead of
    about the instrument.
    """
    failed, records = _drive_every_tool(sink)
    recorded = {r["tool"]: r["outcome"] for r in records if r["event"] == "dispatch"}

    disagreed = {
        name: (recorded[name], "isError" if failed[name] else "success")
        for name in failed
        if (recorded[name] == "ok") is failed[name]
    }

    assert disagreed == {}, f"outcome disagrees with the tool's own result: {disagreed}"


def test_a_refused_action_is_recorded_as_invalid_argument_beside_its_rejection(
    sink: Path,
) -> None:
    """The sharpest in-domain mis-selection signal, and it must not double-count.

    A dispatcher handed an unknown action emits two records with different
    events: the ``dispatch`` the census counts, carrying ``INVALID_ARGUMENT``,
    and a ``rejected`` carrying the valid set the client could have used. Two
    events, one dispatch — that separation is what lets the census stay exact
    while the rejection keeps its diagnostics.
    """
    _failed, records = _drive_every_tool(sink)

    rejected = [r for r in records if r["event"] == "rejected"]
    assert rejected, "no dispatcher refused the placeholder action"

    for rejection in rejected:
        siblings = [
            r for r in records if r["event"] == "dispatch" and r["tool"] == rejection["tool"]
        ]
        assert [r["outcome"] for r in siblings] == ["INVALID_ARGUMENT"]
        assert rejection["valid_actions"], "a rejection must name what would have worked"


def test_the_census_bills_nothing(sink: Path) -> None:
    """The safety claim in this module's docstring, checked rather than asserted in prose."""
    _failed, records = _drive_every_tool(sink)

    billed = [r for r in records if r.get("billed")]

    assert billed == []


# ---------------------------------------------------------------------------
# The property that makes the census redundant — which is the point
# ---------------------------------------------------------------------------


def test_a_newly_registered_tool_is_measured_without_being_told_to(sink: Path) -> None:
    """Coverage is a property of the server, not a convention tools must follow.

    The census above proves today's surface is covered. This proves the *next*
    tool is too: a tool registered on the recording server records without any
    telemetry call of its own. That is the difference between a gate that
    catches an omission and a shape in which the omission cannot occur.
    """
    from knotica.mcp_server.recording_server import RecordingServer

    server = RecordingServer("census-probe")

    @server.tool(name="a_brand_new_tool", description="registered without any telemetry call")
    def a_brand_new_tool(topic: str) -> dict[str, Any]:
        return {"topic": topic}

    call_tool(server, "a_brand_new_tool", {"topic": "t"})

    dispatch = [r for r in _records(sink) if r["event"] == "dispatch"]
    assert [(r["tool"], r["action"], r["topic"], r["outcome"]) for r in dispatch] == [
        ("a_brand_new_tool", "a_brand_new_tool", "t", "ok")
    ]


def test_the_recorder_is_a_subclass_because_a_patched_attribute_is_never_reached() -> None:
    """Pins the mechanism, because the wrong one fails silently and looks fine.

    ``FastMCP.__init__`` registers the *bound* ``self.call_tool`` with the
    low-level server, so assigning ``server.call_tool = ...`` afterwards rebinds
    an attribute nothing on the request path reads. Measured: a patched attribute
    intercepted zero client calls while a subclass intercepted all of them — and
    a test that invoked ``server.call_tool`` directly would have passed either
    way. If someone simplifies this to a patch, this fails instead of the
    telemetry quietly going dead.
    """
    from mcp.server.fastmcp import FastMCP

    from knotica.mcp_server.recording_server import RecordingServer

    server = build_full_server()

    assert isinstance(server, RecordingServer)
    assert type(server).call_tool is RecordingServer.call_tool
    assert RecordingServer.call_tool is not FastMCP.call_tool
