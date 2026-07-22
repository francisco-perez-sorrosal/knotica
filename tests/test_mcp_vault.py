"""MCP vault health / remediation tools — thin wrappers over existing CLI paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio


#: The flat aliases this suite exercised were removed -- fully retired, not
#: deprecated; route each old tool name through the `vault_health` dispatcher
#: action it used to alias.
_DISPATCHER_ACTIONS = {
    "doctor_run": ("vault_health", "doctor"),
    "doctor_repair": ("vault_health", "repair"),
    "okf_check": ("vault_health", "okf_check"),
    "okf_repair": ("vault_health", "okf_repair"),
    "vault_lint": ("vault_health", "lint"),
    "vault_metadata_tree": ("vault_health", "metadata_tree"),
    "loop_run_once": ("loop", "run_once"),
    "loop_set_baseline": ("loop", "set_baseline"),
}


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


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


def test_vault_tools_registered() -> None:
    async def _list() -> list[str]:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(_build_server()) as session:
            await session.initialize()
            listed = await session.list_tools()
            return sorted(t.name for t in listed.tools)

    names = anyio.run(_list)
    assert "vault_health" in names
    assert "loop" in names


def test_doctor_run_matches_cli_json_shape(vault_config: Path) -> None:
    del vault_config
    payload = payload_of(call_tool("doctor_run", {"quick": True}))
    assert "error" not in payload
    assert payload["schema_version"] == 1
    assert payload["quick"] is True
    assert "checks" in payload
    assert "summary" in payload
    assert {row["name"] for row in payload["checks"]} >= {"config", "schema"}
    assert "fix_guidance" in payload
    assert payload["fix_guidance"] is None


def test_doctor_run_fix_includes_guidance_key(vault_config: Path) -> None:
    del vault_config
    payload = payload_of(call_tool("doctor_run", {"fix": True}))
    assert "error" not in payload
    assert "fix_guidance" in payload
    # Clean fixture trees yield null; dirty trees return scoped restore commands.
    guidance = payload["fix_guidance"]
    assert guidance is None or (guidance["kind"] == "scoped_git_restore" and guidance["commands"])


def test_doctor_repair_dry_run_and_apply_paths(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    # Dirty a tracked file; dry-run must list it; apply must restore content.
    target = template_vault / "SCHEMA.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "\n# doctor-repair-test\n", encoding="utf-8")

    dry = payload_of(call_tool("doctor_repair", {"mode": "dry-run"}))
    assert "error" not in dry
    assert dry["mode"] == "dry-run"
    assert dry["dirty_count"] >= 1
    assert "SCHEMA.md" in dry["tracked_paths"]

    applied = payload_of(
        call_tool(
            "doctor_repair",
            {
                "mode": "apply",
                "paths_json": json.dumps(["SCHEMA.md"]),
            },
        )
    )
    assert "error" not in applied
    assert applied["mode"] == "apply"
    assert "SCHEMA.md" in applied["restored"]
    assert target.read_text(encoding="utf-8") == original


def test_doctor_repair_apply_requires_selection(vault_config: Path) -> None:
    del vault_config
    result = call_tool("doctor_repair", {"mode": "apply"})
    assert result.isError
    payload = payload_of(result)
    assert "error" in payload
    assert "paths" in payload["error"]["message"] or "all-tracked" in payload["error"]["message"]
    assert payload["error"]["code"] == "INVALID_ARGUMENT", (
        "no selection is an argument problem, not a stale cursor"
    )


def test_doctor_repair_rejects_a_bad_mode_as_invalid_argument(vault_config: Path) -> None:
    del vault_config
    result = call_tool("doctor_repair", {"mode": "yolo"})
    assert result.isError
    payload = payload_of(result)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_doctor_repair_rejects_malformed_paths_json_as_invalid_argument(
    vault_config: Path,
) -> None:
    del vault_config
    result = call_tool("doctor_repair", {"mode": "apply", "paths_json": "not-json"})
    assert result.isError
    payload = payload_of(result)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_okf_repair_rejects_a_bad_mode_as_invalid_argument(vault_config: Path) -> None:
    del vault_config
    result = call_tool("okf_repair", {"mode": "yolo"})
    assert result.isError
    payload = payload_of(result)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_okf_check_and_dry_run_repair(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    check = payload_of(call_tool("okf_check", {}))
    assert "error" not in check
    assert "status" in check
    assert "concept_files_checked" in check

    repair = payload_of(call_tool("okf_repair", {"mode": "dry-run"}))
    assert "error" not in repair
    assert repair["dry_run"] is True
    assert repair["mode"] == "dry-run"
    assert isinstance(repair["files_changed"], list)


def test_vault_lint_topic_scope(vault_config: Path) -> None:
    del vault_config
    payload = payload_of(call_tool("vault_lint", {"topic": "agentic-systems"}))
    assert "error" not in payload
    assert payload["topic"] == "agentic-systems"
    assert isinstance(payload["violations"], list)


def test_loop_run_once_observes_first_and_captures_eval_failure(vault_config: Path) -> None:
    """One tick = observe default branch, then gate candidates.

    ``loop_run_once`` is a billed two-phase trigger: the bare first call mints
    a preview envelope and never runs the tick; confirming with the minted
    nonce runs it for real. In this credential-less test env the observation's
    real eval fails fast; the failure must land in the payload/loop-state,
    never raise out of the tool.
    """
    del vault_config
    preview = payload_of(call_tool("loop_run_once", {"topic": "agentic-systems"}))
    assert "error" not in preview
    assert preview["action"] == "run_once"
    nonce = preview["confirm_nonce"]

    payload = payload_of(call_tool("loop_run_once", {"topic": "agentic-systems", "confirm": nonce}))
    assert "error" not in payload
    assert payload["topic"] == "agentic-systems"
    observed = payload["observed"]
    assert observed["acted"] is True
    assert "eval failed" in observed["message"]
    # The observation consumed the tick; no candidate work happened after it.
    assert payload["decision"] == "fail"


def test_loop_set_baseline_freezes_scalar(vault_config: Path) -> None:
    del vault_config
    payload = payload_of(
        call_tool("loop_set_baseline", {"topic": "agentic-systems", "scalar": 0.5707})
    )
    assert "error" not in payload
    assert payload["topic"] == "agentic-systems"
    assert payload["baseline_scalar"] == 0.5707
    assert "frozen" in payload["message"]

    status = payload_of(call_tool("wiki_status", {"topic": "agentic-systems"}))
    assert status["loop"]["baseline_frozen"] is True
    assert status["loop"]["baseline_scalar"] == 0.5707
    assert status["gate"]["baseline"] == 0.5707
