"""Equivalence suite for the `vault_health` dispatcher vs. its six thin tools.

The dispatcher lives in `mcp_server/tools_dispatch_vault_health.py`
(`register_dispatch_vault_health_tools`) and wraps `doctor_run`,
`doctor_repair`, `okf_check`, `okf_repair`, `vault_lint`, and
`vault_metadata_tree` from `mcp_server/tools_vault.py`. Every test imports it
lazily so collection stays green even before the paired implementer step
lands (the concurrent BDD/TDD RED handshake).

Every wrapped param already carries a default in its original thin tool (no
sub-arg is required-with-no-default the way `scalar`/`policy`/`branch` are in
the `loop`/`branches`/`compile` domains) -- so this suite adds no
missing-required-arg guard test, only the unknown-action guard shared by
every dispatcher.
"""

from __future__ import annotations

import json
from pathlib import Path

from support.dispatch import (
    TOPIC,
    build_dispatch_server,
    build_full_server,
    call_tool,
    list_tools,
    payload_of,
    rendered_error_text,
)

VALID_ACTIONS = {"doctor", "repair", "okf_check", "okf_repair", "lint", "metadata_tree"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_vault_health import (
        register_dispatch_vault_health_tools,
    )

    return build_dispatch_server(register_dispatch_vault_health_tools)


def test_vault_health_dispatcher_registers_a_single_tool_documenting_the_six_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "vault_health" in tools
    rendered = f"{tools['vault_health'].description or ''} {tools['vault_health'].inputSchema}"
    for action in VALID_ACTIONS:
        assert action in rendered


def test_unknown_action_is_rejected_naming_every_valid_action(vault_config: Path) -> None:
    result = call_tool(_dispatch_server(), "vault_health", {"action": "explode"})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    for action in VALID_ACTIONS:
        assert action in text


def test_doctor_action_matches_doctor_run_tool(vault_config: Path) -> None:
    old = payload_of(call_tool(build_full_server(), "doctor_run", {"quick": True}))
    new = payload_of(
        call_tool(_dispatch_server(), "vault_health", {"action": "doctor", "quick": True})
    )
    assert "error" not in old and "error" not in new
    assert new == old


def test_repair_dry_run_action_matches_doctor_repair_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    target = template_vault / "SCHEMA.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n# dirty\n", encoding="utf-8")

    old = payload_of(call_tool(build_full_server(), "doctor_repair", {"mode": "dry-run"}))
    new = payload_of(
        call_tool(_dispatch_server(), "vault_health", {"action": "repair", "mode": "dry-run"})
    )
    assert "error" not in old and "error" not in new
    assert new == old
    assert new["dirty_count"] >= 1


def test_repair_apply_action_matches_doctor_repair_tool(
    vault_config: Path, template_vault: Path
) -> None:
    """Representative mutating-action proof: apply actually restores the
    dirtied file to HEAD, identically, whichever surface performed the call.

    Both calls reuse the same vault sequentially -- restoring to HEAD is
    idempotent, so re-dirtying the same file the same way before each call
    reproduces the exact same payload for both surfaces.
    """
    del vault_config
    target = template_vault / "SCHEMA.md"
    original = target.read_text(encoding="utf-8")

    target.write_text(original + "\n# dispatch-repair-test\n", encoding="utf-8")
    old = payload_of(
        call_tool(
            build_full_server(),
            "doctor_repair",
            {"mode": "apply", "paths_json": json.dumps(["SCHEMA.md"])},
        )
    )
    assert target.read_text(encoding="utf-8") == original

    target.write_text(original + "\n# dispatch-repair-test\n", encoding="utf-8")
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "vault_health",
            {"action": "repair", "mode": "apply", "paths_json": json.dumps(["SCHEMA.md"])},
        )
    )
    assert target.read_text(encoding="utf-8") == original

    assert "error" not in old and "error" not in new
    assert new == old
    assert old["restored"] == ["SCHEMA.md"] and new["restored"] == ["SCHEMA.md"]


def test_okf_check_action_matches_okf_check_tool(vault_config: Path) -> None:
    old = payload_of(call_tool(build_full_server(), "okf_check", {}))
    new = payload_of(call_tool(_dispatch_server(), "vault_health", {"action": "okf_check"}))
    assert "error" not in old and "error" not in new
    assert new == old


def test_okf_repair_dry_run_action_matches_okf_repair_tool(vault_config: Path) -> None:
    old = payload_of(call_tool(build_full_server(), "okf_repair", {"mode": "dry-run"}))
    new = payload_of(
        call_tool(_dispatch_server(), "vault_health", {"action": "okf_repair", "mode": "dry-run"})
    )
    assert "error" not in old and "error" not in new
    assert new == old
    assert new["dry_run"] is True


def test_lint_action_matches_vault_lint_tool(vault_config: Path) -> None:
    old = payload_of(call_tool(build_full_server(), "vault_lint", {"topic": TOPIC}))
    new = payload_of(
        call_tool(_dispatch_server(), "vault_health", {"action": "lint", "topic": TOPIC})
    )
    assert "error" not in old and "error" not in new
    assert new == old


def test_metadata_tree_action_matches_vault_metadata_tree_tool(vault_config: Path) -> None:
    old = payload_of(call_tool(build_full_server(), "vault_metadata_tree", {"topic": TOPIC}))
    new = payload_of(
        call_tool(_dispatch_server(), "vault_health", {"action": "metadata_tree", "topic": TOPIC})
    )
    assert "error" not in old and "error" not in new
    assert new == old
