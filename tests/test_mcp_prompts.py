"""Behavioral tests for the MCP prompt band over an in-memory client session.

These drive the FastMCP server through the official SDK's in-memory transport
(``mcp.shared.memory.create_connected_server_and_client_session``) so the
assertions pin the *wire* contract a real MCP client sees — ``prompts/list``
and ``prompts/get`` — not the internal ``core.prompts`` resolver (its unit
contract is pinned separately in ``test_prompts.py``).

The pinned contract (from INTERFACE_DESIGN §2, the pre-plan first-use section,
and pre-mortem #4):

- ``prompts/list`` is STATIC: the four locked operation names register at
  startup and are listed with zero vault access, even unconfigured;
- prompt bodies resolve LAZILY per ``prompts/get`` — a vault prompt file edited
  between two gets is reflected on the second, no server restart;
- an unconfigured ``prompts/get`` returns the uniform setup guidance (naming
  both the ``/knotica:setup`` and ``knotica init`` paths) instead of a hard
  failure — graceful boot at the prompt layer;
- the prompt↔tool-name contract: every tool the shipped prompt bodies instruct
  the client to call is a tool the server actually registers. This catches a
  server-side tool rename silently diverging from the prompt outlines (a
  client would then call a nonexistent tool and dead-end).

Async coroutines are driven from sync test bodies via ``anyio.run``. Production
imports of the server are deferred into helpers so collection succeeds while
Step 34 is still in flight (RED handshake: ImportError until the impl lands).
"""

import re
from pathlib import Path
from typing import Any

import anyio
import pytest

from test_errors import assert_names_both_setup_paths

OPERATIONS = frozenset({"ingest", "query", "lint", "curate"})

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "vault-template" / ".knotica" / "prompts"

#: The verb vocabulary a valid tool name is built from (INTERFACE_DESIGN §6.1:
#: tools are ``verb_noun`` snake_case, plus the one justified bare verb
#: ``search``). A backticked token in a prompt body is treated as a *tool
#: reference* iff it matches this grammar — which excludes argument/field names
#: like ``index_entry`` or ``citation_key`` while still flagging a stale tool
#: name if the server renames one out from under the prompt outline.
TOOL_VERBS = frozenset({"read", "write", "store", "create", "list", "lint", "curate"})


# ---------------------------------------------------------------------------
# Harness helpers — deferred imports keep collection green pre-impl.
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _list_prompts(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.list_prompts()


async def _get_prompt(server: Any, name: str, arguments: dict[str, str] | None) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.get_prompt(name, arguments)


async def _list_tools(server: Any) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.list_tools()


def prompt_names(*, server: Any | None = None) -> list[str]:
    result = anyio.run(_list_prompts, server if server is not None else _build_server())
    return [p.name for p in result.prompts]


def prompt_body(
    name: str, arguments: dict[str, str] | None = None, *, server: Any | None = None
) -> str:
    srv = server if server is not None else _build_server()
    result = anyio.run(_get_prompt, srv, name, arguments)
    parts = [
        m.content.text for m in result.messages if getattr(m.content, "text", None) is not None
    ]
    return "".join(parts)


def registered_tool_names(*, server: Any | None = None) -> set[str]:
    result = anyio.run(_list_tools, server if server is not None else _build_server())
    return {t.name for t in result.tools}


# ---------------------------------------------------------------------------
# Prompt-body scanning (whitespace/blockquote-normalized).
# ---------------------------------------------------------------------------


def _normalized(text: str) -> str:
    """Strip blockquote markers and collapse whitespace before token scanning."""
    unquoted = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", unquoted).strip()


#: ``ingest_progress`` stage names that look like tool calls but are not tools.
#: (Stages that ARE tools — ``store_source``, ``write_page`` — stay checkable.)
_STAGE_ONLY_TOKENS = frozenset({"read_schema", "resolve_topic"})


def _is_tool_reference(token: str) -> bool:
    if token in _STAGE_ONLY_TOKENS:
        return False
    if token == "search":
        return True
    return "_" in token and token.split("_", 1)[0] in TOOL_VERBS


def _tool_references(text: str) -> set[str]:
    tokens = re.findall(r"`([a-z][a-z_]*)`", _normalized(text))
    return {t for t in tokens if _is_tool_reference(t)}


# ---------------------------------------------------------------------------
# prompts/list is static and names the four locked operations.
# ---------------------------------------------------------------------------


def test_prompt_list_is_static_and_names_the_four_operations(
    unconfigured_env: Path,
) -> None:
    """The four operation prompts are listed with zero vault access — names
    register at startup, independent of any configured vault."""
    assert set(prompt_names()) == OPERATIONS


# ---------------------------------------------------------------------------
# Lazy body: a vault edit between two gets is reflected on the next get.
# ---------------------------------------------------------------------------


def test_prompt_body_reflects_a_vault_edit_between_two_gets(
    vault_config: Path, template_vault: Path
) -> None:
    """Bodies resolve per invocation: an evolved prompt file (DSPy/SIA writing
    the substrate) is served on the very next get — no server restart."""
    first = prompt_body("query")

    evolved = "# Query — freshly evolved between two gets\n\nServe me next call.\n"
    (template_vault / ".knotica" / "prompts" / "query.md").write_text(evolved, encoding="utf-8")

    second = prompt_body("query")
    assert "freshly evolved between two gets" in second
    assert second != first, "the second get must reflect the mid-session edit"


# ---------------------------------------------------------------------------
# Unconfigured get → setup guidance, not a hard failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_unconfigured_get_prompt_returns_setup_guidance(
    unconfigured_env: Path, operation: str
) -> None:
    """Graceful boot at the prompt layer: with no config, a get still returns
    actionable content naming both setup paths — never a transport failure."""
    body = prompt_body(operation)
    assert_names_both_setup_paths(body)


# ---------------------------------------------------------------------------
# Prompt↔tool-name contract (pre-mortem #4).
# ---------------------------------------------------------------------------


def test_shipped_prompts_reference_tools() -> None:
    """Guard against a vacuous contract test: the shipped prompt bodies must
    actually reference some tools (else the check below asserts nothing)."""
    referenced: set[str] = set()
    for prompt_file in sorted(PROMPTS_DIR.glob("*.md")):
        referenced |= _tool_references(prompt_file.read_text(encoding="utf-8"))
    assert len(referenced) >= 5, (
        f"expected the prompt outlines to reference several tools, saw {referenced!r}"
    )


def test_every_prompt_tool_reference_is_a_registered_tool() -> None:
    """Every tool a shipped prompt body instructs the client to call must be a
    tool the server registers — otherwise a rename dead-ends the client."""
    registered = registered_tool_names()
    for prompt_file in sorted(PROMPTS_DIR.glob("*.md")):
        referenced = _tool_references(prompt_file.read_text(encoding="utf-8"))
        unknown = referenced - registered
        assert not unknown, (
            f"{prompt_file.name} references unregistered tools {sorted(unknown)}; "
            f"server registers {sorted(registered)}"
        )
