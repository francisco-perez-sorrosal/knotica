"""MCP ``prompt_diff`` tool registration and payload."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio

from knotica.core.vcs import VaultVcs
from support.vault import run_git

TOPIC = "agentic-systems"
ROOT_QUERY = ".knotica/prompts/query.md"


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


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


def test_prompt_diff_tool_registered() -> None:
    from knotica.mcp_server.server import build_server

    mcp = build_server()
    names = {tool.name for tool in mcp._tool_manager.list_tools()}  # noqa: SLF001
    assert "prompt_diff" in names


def test_prompt_diff_mcp_branch_payload(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = "loop/c/prompt-diff-mcp"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    target = vcs.root / ROOT_QUERY
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# wounded query\n\nSkip citations.\n", encoding="utf-8")
    run_git(vcs.root, "add", ROOT_QUERY)
    run_git(vcs.root, "commit", "-m", "test: loop candidate prompt")
    vcs.checkout_branch(default)

    payload = payload_of(
        call_tool("prompt_diff", {"topic": TOPIC, "branch": branch}),
    )
    assert payload["topic"] == TOPIC
    assert payload["head_ref"] == branch
    assert payload["path"] == ROOT_QUERY
    assert payload["hunks"]


def test_prompt_diff_mcp_compiled_mode(vault_config: Path, template_vault: Path) -> None:
    del vault_config
    from knotica.core.compiled import (
        CompiledArtifact,
        CompiledDemo,
        artifact_write_bodies,
        compiled_artifact_path,
    )

    vcs = VaultVcs(template_vault)
    vault_body = vcs.read_file_at("HEAD", ROOT_QUERY) or ""
    artifact = CompiledArtifact(
        optimized_instructions=vault_body + "\n## MCP compiled\nMCP test marker.\n",
        demos=(
            CompiledDemo(
                "What gains does AWM report on SWE-bench?",
                "AWM reports roughly 12% absolute gains on SWE-bench.",
                ("wang2024awm",),
            ),
        ),
    )
    art_body, man_body = artifact_write_bodies(artifact)
    art_path = compiled_artifact_path(TOPIC)
    target = vcs.root / art_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(art_body, encoding="utf-8")
    run_git(vcs.root, "add", art_path)
    run_git(vcs.root, "commit", "-m", "test: mcp compiled diff")
    man_path = f"{TOPIC}/.knotica/compiled/MANIFEST.json"
    (vcs.root / man_path).write_text(man_body, encoding="utf-8")
    run_git(vcs.root, "add", man_path)
    run_git(vcs.root, "commit", "-m", "test: mcp compiled manifest")

    payload = payload_of(
        call_tool("prompt_diff", {"topic": TOPIC, "mode": "compiled"}),
    )
    assert payload["source"] == "compiled"
    assert payload["demo_count"] == 1
    assert payload["empty"] is False
    joined = "\n".join(line["text"] for hunk in payload["hunks"] for line in hunk["lines"])
    assert "MCP compiled" in joined
    assert "## Compiled few-shot demos" in joined
