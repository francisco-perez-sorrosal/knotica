"""MCP arena_status / arena_history tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.arena import ScorerInfo, VariantSpec, race_variants
from knotica.store import LocalFSStore


#: `arena_status`/`arena_history` were removed -- the flat aliases were fully
#: retired, not deprecated; route each through the `arena` dispatcher.
_DISPATCHER_ACTIONS = {
    "arena_status": ("arena", "status"),
    "arena_history": ("arena", "history"),
}


def _build_server() -> Any:
    """The verb surface: the published server plus the verbs the lanes absorbed.

    See ``support.dispatch.build_verb_server`` -- this is not the published
    surface, and the tests in this module assert verb *behaviour*, not
    registration.
    """
    from support.dispatch import build_verb_server

    return build_verb_server()


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    dispatcher, action = _DISPATCHER_ACTIONS.get(tool, (tool, None))
    call_args = args if action is None else {"action": action, **args}
    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(dispatcher, call_args)


def call_tool(tool: str, args: dict[str, Any]) -> Any:
    return anyio.run(_call, _build_server(), tool, args)


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"no payload: {result!r}")


# Registration-existence assertions for the verbs the lanes absorbed were
# removed with the flat registrations themselves. What replaced them is
# stronger and lives in one place: `test_lane_rename_invariants.py` proves
# no absorbed name is registered under any alias, `test_lane_dispatchers.py`
# proves every declared verb is reachable as a lane action with an identical
# payload, and `test_server_tool_surface.py` pins the surface ceiling.


def test_arena_status_idle_then_after_race(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    idle = payload_of(call_tool("arena_status", {"topic": "agentic-systems"}))
    assert "error" not in idle
    assert idle["stage"] == "idle"
    assert idle["variants"] == []

    store = LocalFSStore(template_vault)
    race_variants(
        store,
        template_vault,
        "agentic-systems",
        [
            VariantSpec(id="v1", label="a", body="# a\n"),
            VariantSpec(id="v2", label="b", body="# b\n"),
        ],
        baseline_scalar=0.5,
        score=lambda _t, _r, body: 0.9 if "# b" in body else 0.1,
        # The arena refuses to rank the default heuristic against a gate
        # baseline, so a race that must reach "completed" declares a
        # comparable scorer.
        scorer=ScorerInfo(id="fake-arena", comparable_to_eval=True),
    )
    status = payload_of(call_tool("arena_status", {"topic": "agentic-systems"}))
    assert status["stage"] == "completed"
    assert status["winner_id"] == "v2"
    history = payload_of(call_tool("arena_history", {"topic": "agentic-systems", "limit": 5}))
    assert history["races"]
