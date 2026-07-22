"""Shared plumbing for dispatcher-vs-thin-tool equivalence suites.

The P-B dispatcher modules (`tools_dispatch_loop.py`, `tools_dispatch_branches.py`,
`tools_dispatch_compile.py`, ...) are pure routing over existing thin
implementations and are deliberately **not yet wired into `server.py`**
(Step 31 wires all seven at once). Equivalence tests therefore need two
independent MCP server instances per assertion:

- the **full** server (`knotica.mcp_server.server.build_server()`), which
  already carries the replaced thin tool (e.g. ``loop_run_once``), and
- a **bare** ``FastMCP()`` instance carrying only the dispatcher under test,
  built by calling its ``register_dispatch_<domain>`` function directly (this
  is the "importable and unit-testable by passing a bare FastMCP() instance"
  contract from the implementation plan).

Mutating actions need two independent, identically-seeded vaults (calling the
same mutation twice against one vault would make the second call observe
state the first call already changed) — ``dual_vault_env`` swaps ``HOME``/
``KNOTICA_CONFIG`` between two isolated configs so the same test can drive one
call against each vault, sequentially, without cross-contamination.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest

TOPIC = "agentic-systems"


def build_full_server() -> Any:
    """The real, fully-wired server -- carries every already-registered thin tool."""
    from knotica.mcp_server import server as server_mod

    return server_mod.build_server()


def build_dispatch_server(register_fn: Any) -> Any:
    """A bare ``FastMCP()`` instance carrying only the dispatcher under test."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-dispatch")
    register_fn(mcp)
    return mcp


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(server: Any, tool: str, args: dict[str, Any]) -> Any:
    """Call ``tool`` on an explicit server instance (full or dispatch-only)."""
    return anyio.run(_call, server, tool, args)


async def _list_tools(server: Any) -> list[Any]:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        listed = await session.list_tools()
        return list(listed.tools)


def list_tools(server: Any) -> list[Any]:
    """Full ``Tool`` objects (name + ``inputSchema``), not just names."""
    return anyio.run(_list_tools, server)


def list_tool_names(server: Any) -> list[str]:
    return sorted(tool.name for tool in list_tools(server))


def tool_schema(server: Any, name: str) -> dict[str, Any]:
    """``inputSchema`` of the named tool -- raises if the tool is absent."""
    for tool in list_tools(server):
        if tool.name == name:
            return tool.inputSchema
    raise AssertionError(f"tool {name!r} not found; have {[t.name for t in list_tools(server)]}")


def payload_of(result: Any) -> Any:
    """Structured success/error envelope -- requires JSON content."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"no payload: {result!r}")


def safe_payload(result: Any) -> Any:
    """The structured JSON payload, or the raw text if it isn't JSON.

    Some tool-level failures never reach the envelope machinery at all (an
    uncaught exception surfaces as plain ``str(exc)`` text -- see
    ``rendered_error_text``) -- this lets a comparison test assert on
    "identical outcome" without caring in advance whether that outcome will
    be structured JSON or raw text.
    """
    try:
        return payload_of(result)
    except (AssertionError, json.JSONDecodeError):
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                return text
        raise


def rendered_error_text(result: Any) -> str:
    """Best-effort text of an ``isError=True`` result.

    Prefers the structured ``{"error": {...}}`` envelope (the tested,
    correct pattern in this codebase — see e.g. ``compile_promote``'s
    ``mode`` validation); falls back to the raw text content for a
    validation error raised before the envelope machinery runs. Either way
    this is what a client actually sees, so asserting against it pins the
    real observable behavior.
    """
    try:
        payload = payload_of(result)
    except AssertionError:
        return ""
    if isinstance(payload, dict) and "error" in payload:
        return json.dumps(payload)
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    return ""


def fresh_vault(vault_seed: Path, tmp_path: Path, label: str) -> Path:
    """A second, independent copy of the seeded vault (own git history)."""
    dest = tmp_path / f"vault-{label}"
    shutil.copytree(vault_seed, dest)
    return dest


def build_real_compile_branch(vault_path: Path) -> str:
    """Run a real (fake-optimizer, no-LLM) compile so tests have a genuine
    ``compile/<topic>/…`` branch to promote/delete -- without needing eval
    credentials. Mirrors the arrangement in ``test_phase3a_compile.py``.
    """
    from knotica.core.compile_run import run_compile
    from knotica.programs.query import bootstrap_query_artifact
    from knotica.store import LocalFSStore
    from support.trainset import populate_query_trainset

    store = LocalFSStore(vault_path)
    populate_query_trainset(store, vault_path, TOPIC)
    result = run_compile(
        store,
        vault_path,
        TOPIC,
        use_mipro=False,
        optimize_fn=lambda s, t, train, **k: bootstrap_query_artifact(s, t, train, golden_n=20),
        compare_fn=lambda *a: (0.41, 0.72),
    )
    assert result.branch is not None
    return result.branch


def configure_default_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, label: str, vault_path: Path
) -> None:
    """Point the *default* configured vault at ``vault_path`` for this process.

    Rewrites ``HOME``/``XDG_CONFIG_HOME``/``KNOTICA_CONFIG`` on the shared
    ``monkeypatch`` fixture, so calling it a second time with a different
    ``vault_path`` (same test, sequential calls) re-targets every subsequent
    ``vault=""`` resolution -- this is what lets one test drive one call
    against each of two independent vaults.
    """
    home = tmp_path / f"home-{label}"
    config_dir = home / ".config" / "knotica"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{vault_path}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir.parent))
    monkeypatch.setenv("KNOTICA_CONFIG", str(config_path))
