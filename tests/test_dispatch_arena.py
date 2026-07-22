"""Equivalence suite for the `arena` dispatcher vs. its two thin tools.

The dispatcher lives in `mcp_server/tools_dispatch_arena.py`
(`register_dispatch_arena_tools`) and wraps `arena_status`/`arena_history`
from `mcp_server/tools_arena.py`. Every test imports it lazily so collection
stays green even before the paired implementer step lands (the concurrent
BDD/TDD RED handshake).

Both wrapped tools are read-only -- no dry-run/apply split applies to this
domain (unlike `branches`/`compile`).
"""

from __future__ import annotations

from pathlib import Path

from knotica.core.arena import VariantSpec, race_variants
from knotica.store import LocalFSStore
from support.dispatch import (
    TOPIC,
    build_dispatch_server,
    build_full_server,
    call_tool,
    list_tools,
    payload_of,
    rendered_error_text,
)

VALID_ACTIONS = {"status", "history"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_arena import register_dispatch_arena_tools

    return build_dispatch_server(register_dispatch_arena_tools)


def test_arena_dispatcher_registers_a_single_tool_documenting_the_two_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "arena" in tools
    rendered = f"{tools['arena'].description or ''} {tools['arena'].inputSchema}"
    for action in VALID_ACTIONS:
        assert action in rendered


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "arena", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    for action in VALID_ACTIONS:
        assert action in text


def test_status_action_matches_arena_status_tool_on_an_idle_topic(
    vault_config: Path, template_vault: Path
) -> None:
    """`updated_at` is a fresh `datetime.now(UTC)` stamp on every idle read (no
    arena state exists yet to carry a stored one) -- excluded from the
    equality check since the two calls happen microseconds apart, same as
    `commit_sha`/`branch` are excluded in the mutating-action proofs below.
    """
    del template_vault
    old = payload_of(call_tool(build_full_server(), "arena_status", {"topic": TOPIC}))
    new = payload_of(call_tool(_dispatch_server(), "arena", {"action": "status", "topic": TOPIC}))
    assert "error" not in old and "error" not in new
    old_rest = {k: v for k, v in old.items() if k != "updated_at"}
    new_rest = {k: v for k, v in new.items() if k != "updated_at"}
    assert new_rest == old_rest
    assert new["stage"] == "idle"


def test_history_action_matches_arena_history_tool_after_a_race(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    race_variants(
        store,
        template_vault,
        TOPIC,
        [
            VariantSpec(id="v1", label="a", body="# a\n"),
            VariantSpec(id="v2", label="b", body="# b\n"),
        ],
        baseline_scalar=0.5,
        score=lambda _t, _r, body: 0.9 if "# b" in body else 0.1,
    )

    old = payload_of(call_tool(build_full_server(), "arena_history", {"topic": TOPIC, "limit": 5}))
    new = payload_of(
        call_tool(_dispatch_server(), "arena", {"action": "history", "topic": TOPIC, "limit": 5})
    )
    assert "error" not in old and "error" not in new
    assert new == old
    assert new["races"]
