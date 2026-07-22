"""Equivalence suite for the `compile` dispatcher vs. its three thin tools.

The dispatcher lives in `mcp_server/tools_dispatch_compile.py`
(`register_dispatch_compile_tools`) and wraps `compile_run`, `compile_status`,
and `compile_promote` from `mcp_server/tools_compile.py`. Every test imports
it lazily so collection stays green even before the paired implementer step
lands (the concurrent BDD/TDD RED handshake).

`action=run`'s success path needs real LLM credentials (post-eval compare),
which this hermetic suite cannot provide -- its equivalence is proven via the
deterministic, side-effect-free "trainset floor not met" error path instead
(the failure fires before any state is written, so the identical error on
both surfaces is still a genuine routing proof, not a weaker one).

``action=status`` on an idle topic currently crashes identically through
*both* surfaces -- ``CompileState.render()`` always includes an ``"error"``
key (``None`` when idle), which collides with the envelope contract's
reserved ``"error"`` key (``core/errors.py::ok()`` rejects it unconditionally,
regardless of value) and escapes uncaught as generic MCP-protocol text. This
is a pre-existing defect in ``tools_compile.py::compile_status`` -- unrelated
to the dispatcher (the dispatcher reproduces it faithfully, which is the
correct equivalence result) -- see LEARNINGS.md for the full writeup.
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
    safe_payload,
)
from support.vault import git_commit_count

VALID_ACTIONS = {"run", "status", "promote"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_compile import register_dispatch_compile_tools

    return build_dispatch_server(register_dispatch_compile_tools)


def test_compile_dispatcher_registers_a_single_tool_documenting_the_three_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "compile" in tools
    rendered = f"{tools['compile'].description or ''} {tools['compile'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "compile", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_promote_missing_branch_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "compile", {"action": "promote", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "branch" in text


def test_status_action_matches_compile_status_tool(
    vault_config: Path, template_vault: Path
) -> None:
    """Both surfaces call the exact same `compile_status_payload` -- whatever
    it produces (see the module docstring for a pre-existing crash on idle
    topics), the dispatcher must reproduce it identically."""
    del vault_config
    old = safe_payload(call_tool(build_full_server(), "compile_status", {"topic": TOPIC}))
    new = safe_payload(
        call_tool(_dispatch_server(), "compile", {"action": "status", "topic": TOPIC})
    )
    if isinstance(old, str) and isinstance(new, str):
        # the generic MCP crash message embeds the tool name ("compile_status"
        # vs "compile") -- everything after that is the shared, load-bearing text.
        assert old.split(": ", 1)[1] == new.split(": ", 1)[1]
    else:
        assert new == old


def test_run_action_matches_compile_run_tool_on_the_deterministic_error_path(
    vault_config: Path, template_vault: Path
) -> None:
    """No curated trainset exists on a fresh vault -- both surfaces must hit
    the exact same "not enough train examples" floor check before any clone,
    optimize, or write happens (side-effect-free, hermetic, deterministic)."""
    del vault_config
    old = payload_of(call_tool(build_full_server(), "compile_run", {"topic": TOPIC}))
    new = payload_of(call_tool(_dispatch_server(), "compile", {"action": "run", "topic": TOPIC}))
    assert old["error"]["code"] == "NOT_CONFIGURED"
    assert new == old


def test_promote_dry_run_action_matches_compile_promote_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    args = {"topic": TOPIC, "branch": "not-a-compile-branch", "mode": "dry-run"}
    old = payload_of(call_tool(build_full_server(), "compile_promote", args))
    new = payload_of(call_tool(_dispatch_server(), "compile", {"action": "promote", **args}))
    assert old["error"]["code"] == "INVALID_ARGUMENT"
    assert new == old


def test_promote_apply_action_matches_compile_promote_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Representative mutating-action proof: apply actually merges the compile
    branch into the default branch, identically, on both surfaces.

    Each independent compile run embeds its own artifact timestamp, so
    ``branch_a``/``branch_b`` are never byte-identical -- compared separately
    against their own input rather than folded into the payload-equality check.
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
            "compile_promote",
            {"topic": TOPIC, "branch": branch_a, "mode": "apply"},
        )
    )

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "compile",
            {"action": "promote", "topic": TOPIC, "branch": branch_b, "mode": "apply"},
        )
    )

    assert "error" not in old and "error" not in new
    assert old["merged"] is True and new["merged"] is True
    assert old["branch"] == branch_a
    assert new["branch"] == branch_b
    old_rest = {k: v for k, v in old.items() if k not in {"branch", "commit_sha", "message"}}
    new_rest = {k: v for k, v in new.items() if k not in {"branch", "commit_sha", "message"}}
    assert new_rest == old_rest
    # the merge (plus any bookkeeping commits compile_promote makes) landed
    # identically -- same number of new commits on both vaults.
    delta_a = git_commit_count(vault_a) - before_a
    delta_b = git_commit_count(vault_b) - before_b
    assert delta_a > 0
    assert delta_a == delta_b
