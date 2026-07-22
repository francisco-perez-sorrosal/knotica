"""Equivalence suite for the `branches` dispatcher vs. its four thin tools.

The dispatcher lives in `mcp_server/tools_dispatch_branches.py`
(`register_dispatch_branches_tools`) and wraps `branch_scoreboard`,
`loop_promote`, `branch_promote`, and `branch_delete` from
`mcp_server/tools_scoreboard.py`. Every test imports it lazily so collection
stays green even before the paired implementer step lands (the concurrent
BDD/TDD RED handshake).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support.dispatch import (
    TOPIC,
    build_dispatch_server,
    build_full_server,
    build_real_compile_branch,
    call_tool,
    configure_default_vault,
    fresh_vault,
    list_tools,
    payload_of,
    rendered_error_text,
)
from support.vault import git_commit_count

VALID_ACTIONS = {"scoreboard", "promote_loop", "promote", "delete"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_branches import register_dispatch_branches_tools

    return build_dispatch_server(register_dispatch_branches_tools)


def test_branches_dispatcher_registers_a_single_tool_documenting_the_four_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "branches" in tools
    rendered = f"{tools['branches'].description or ''} {tools['branches'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "branches", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_promote_loop_missing_branch_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "branches", {"action": "promote_loop", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "branch" in text


def test_delete_missing_branch_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "branches", {"action": "delete", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "branch" in text


def test_promote_missing_kind_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(
        _dispatch_server(),
        "branches",
        {"action": "promote", "topic": TOPIC, "branch": "loop/agentic-systems/deadbeef"},
    )
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "kind" in text


def test_scoreboard_action_matches_branch_scoreboard_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    old = payload_of(call_tool(build_full_server(), "branch_scoreboard", {"topic": TOPIC}))
    new = payload_of(
        call_tool(_dispatch_server(), "branches", {"action": "scoreboard", "topic": TOPIC})
    )
    assert "error" not in old and "error" not in new
    assert new == old


def test_promote_loop_dry_run_action_matches_loop_promote_tool(
    vault_config: Path, template_vault: Path
) -> None:
    """A wrong-prefix branch is a deterministic, side-effect-free error path --
    both the old tool and the dispatcher route to the same core `loop_promote`
    validation, so the same vault can serve both calls safely."""
    del vault_config
    args = {"topic": TOPIC, "branch": "not-a-loop-branch", "mode": "dry-run"}
    old = payload_of(call_tool(build_full_server(), "loop_promote", args))
    new = payload_of(call_tool(_dispatch_server(), "branches", {"action": "promote_loop", **args}))
    assert old["error"]["code"] == "INVALID_ARGUMENT"
    assert new == old


def test_promote_compile_kind_dry_run_action_matches_branch_promote_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    args = {"topic": TOPIC, "branch": "not-a-compile-branch", "mode": "dry-run"}
    old = payload_of(call_tool(build_full_server(), "branch_promote", {"kind": "compile", **args}))
    new = payload_of(
        call_tool(_dispatch_server(), "branches", {"action": "promote", "kind": "compile", **args})
    )
    assert old["error"]["code"] == "INVALID_ARGUMENT"
    assert new == old


def test_delete_dry_run_action_matches_branch_delete_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    args = {"topic": TOPIC, "branch": "not-a-compile-branch", "mode": "dry-run"}
    old = payload_of(call_tool(build_full_server(), "branch_delete", args))
    new = payload_of(call_tool(_dispatch_server(), "branches", {"action": "delete", **args}))
    assert old["error"]["code"] == "INVALID_ARGUMENT"
    assert new == old


def test_delete_apply_action_matches_branch_delete_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Representative mutating-action proof: apply actually deletes the branch,
    identically, whichever surface performed the call.

    Each independent compile run embeds its own artifact timestamp in the
    resulting commit, so ``branch_a``/``branch_b`` are never byte-identical --
    the ``branch``/``message`` fields are compared separately (each against
    its own input) rather than folded into the blanket payload-equality check.
    """
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")
    branch_a = build_real_compile_branch(vault_a)
    branch_b = build_real_compile_branch(vault_b)
    before_a = git_commit_count(vault_a)
    before_b = git_commit_count(vault_b)

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    old = payload_of(
        call_tool(
            build_full_server(),
            "branch_delete",
            {"topic": TOPIC, "branch": branch_a, "mode": "apply"},
        )
    )

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "branches",
            {"action": "delete", "topic": TOPIC, "branch": branch_b, "mode": "apply"},
        )
    )

    assert "error" not in old and "error" not in new
    assert old["branch"] == branch_a
    assert new["branch"] == branch_b
    old_rest = {k: v for k, v in old.items() if k not in {"branch", "message"}}
    new_rest = {k: v for k, v in new.items() if k not in {"branch", "message"}}
    assert new_rest == old_rest
    assert old["deleted"] is True and new["deleted"] is True
    # apply commits a compile-state.json update (clearing the deleted branch's
    # pointer) in addition to the ref removal -- both surfaces must produce the
    # exact same number of new commits.
    assert git_commit_count(vault_a) - before_a == git_commit_count(vault_b) - before_b
