"""Behavioral tests for the MCP read band over an in-memory client session.

These drive the FastMCP server through the official SDK's in-memory transport
(``mcp.shared.memory.create_connected_server_and_client_session``) so the
assertions are made over the *wire* contract a real MCP client sees -- not
against the internal ``core`` functions the tools delegate to. What is pinned
here is the observable protocol-band contract from INTERFACE_DESIGN:

- the server imports and instantiates **unconfigured** with zero vault access
  (§ config three-state machine; graceful boot);
- read tools return a §1.3-conformant success payload (parsed from the result's
  ``structuredContent`` or, failing that, its JSON text content);
- failures ride **in the result content** as ``{"error": {code, message, fix,
  retryable}}`` with ``isError`` set -- never a raised transport exception
  (§1.4); ``code`` is drawn from the fixed enum and ``retryable`` is typed;
- ``NOT_CONFIGURED`` when no config; a config written mid-session takes effect
  on the next call (per-call resolution);
- reads make **zero git commits and take no lock** (HEAD unchanged across a
  batch of read calls);
- ``search`` returns pointer results and honours cursor/limit with
  ``has_more``/``next_cursor``; a malformed cursor -> ``INVALID_CURSOR``;
- ``read_page``/``list_links`` on a missing page -> ``PAGE_NOT_FOUND``;
- ``lint_check`` returns violations-as-DATA (never an error).

Async coroutines are driven from sync test bodies via ``anyio.run`` (mcp
depends on anyio; there is no pytest async plugin configured). Production
imports of the server are deferred into helpers so collection succeeds while
Step 30 is still in flight (RED handshake: ImportError until the impl lands).
"""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest

from support.vault import run_git

# The §1.4 error-code enum, mirrored as wire strings. The tool result's
# error.code must be one of exactly these.
ERROR_CODES = frozenset(
    {
        "NOT_CONFIGURED",
        "TOPIC_NOT_FOUND",
        "PAGE_NOT_FOUND",
        "RESERVED_NAME",
        "SOURCE_EXISTS",
        "INVALID_FRONTMATTER",
        "SECRET_SCRUBBED",
        "LOCK_BUSY",
        "GIT_ERROR",
        "INVALID_CURSOR",
    }
)

TOPIC = "agentic-systems"
# A demo page known to exist in the vault template (carries paper frontmatter).
KNOWN_PAGE = "agent-workflow-memory"


# ---------------------------------------------------------------------------
# Harness helpers -- deferred imports keep collection green pre-impl.
# ---------------------------------------------------------------------------


def _build_server() -> Any:
    """Construct a fresh server instance.

    Prefers a ``build_server()`` factory; falls back to a module-level ``mcp``
    singleton. Either satisfies the observable contract -- the test does not
    care which the implementer chose.
    """
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    """Open an in-memory session against ``server`` and call one tool."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(tool: str, args: dict[str, Any], *, server: Any | None = None) -> Any:
    """Sync entry point: build (or reuse) a server and call one tool."""
    srv = server if server is not None else _build_server()
    return anyio.run(_call, srv, tool, args)


def run(coro_factory: Callable[[], Awaitable[Any]]) -> Any:
    """Drive an arbitrary coroutine factory from a sync test body."""
    return anyio.run(coro_factory)


# ---------------------------------------------------------------------------
# Result-envelope extraction. A CallToolResult carries the payload in
# ``structuredContent`` and/or as JSON text in ``content``; ``isError`` flags
# a failure envelope. These helpers normalise both to a plain dict.
# ---------------------------------------------------------------------------


def payload_of(result: Any) -> Any:
    """Return the tool's result payload as a Python object (dict/list)."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def error_of(result: Any) -> dict[str, Any]:
    """Assert the result is a failure envelope and return its error object."""
    body = payload_of(result)
    assert isinstance(body, dict), f"error envelope must be an object, got {body!r}"
    assert "error" in body, f"expected a failure envelope, got success: {body!r}"
    err = body["error"]
    assert getattr(result, "isError", False) is True, "an error payload must set isError=True"
    return err


def assert_success(result: Any) -> Any:
    """Assert the result is a success envelope (no error key) and return it."""
    body = payload_of(result)
    if isinstance(body, dict):
        assert "error" not in body, f"expected success, got error envelope: {body!r}"
    assert getattr(result, "isError", False) is False, "a success payload must not set isError"
    return body


def assert_error_shape(err: dict[str, Any], code: str | None = None) -> None:
    """Assert the §1.4 error object shape: code enum, message, fix, retryable."""
    assert set(err) >= {"code", "message", "fix", "retryable"}, (
        f"error object missing contract fields: {err!r}"
    )
    assert err["code"] in ERROR_CODES, f"code not in the §1.4 enum: {err['code']!r}"
    assert isinstance(err["retryable"], bool), "retryable must be a bool"
    # LOCK_BUSY is the only retryable code; reads never hit it, so every error
    # here must be non-retryable.
    assert err["retryable"] is False, f"read errors are non-retryable: {err!r}"
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["fix"], str) and err["fix"]
    if code is not None:
        assert err["code"] == code, f"expected {code}, got {err['code']!r}"


READ_TOOLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("list_topics", {}),
    ("read_page", {"topic": TOPIC, "page": KNOWN_PAGE}),
    ("search", {"query": "memory"}),
    ("list_links", {"topic": TOPIC, "page": KNOWN_PAGE}),
    ("lint_check", {}),
)


# ---------------------------------------------------------------------------
# Boot / import contract.
# ---------------------------------------------------------------------------


def test_server_imports_and_builds_without_vault_access() -> None:
    """The server module imports and instantiates with no config present.

    Nothing in construction may reach for a vault -- unconfigured boot is
    graceful. Building must not raise (REQ-CFG-01 precondition).
    """
    server = _build_server()
    assert server is not None


# ---------------------------------------------------------------------------
# REQ-CFG-01 / REQ-CFG-02 -- unconfigured boot, then per-call resolution.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool,args", READ_TOOLS, ids=lambda p: p if isinstance(p, str) else "")
def test_every_read_tool_returns_not_configured_when_unconfigured(
    tool: str, args: dict[str, Any], unconfigured_env: Path
) -> None:
    """With no config.toml anywhere, every read tool returns NOT_CONFIGURED.

    The error rides in the result content (never a transport exception), so the
    call itself completes -- we inspect the envelope, not an exception.
    """
    result = call_tool(tool, args)
    err = error_of(result)
    assert_error_shape(err, code="NOT_CONFIGURED")


def test_config_written_mid_session_takes_effect_on_next_call(
    isolated_home: Path,
    template_vault: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config written after an unconfigured call makes the next call succeed.

    Proves per-call resolution (REQ-CFG-02): the server holds no resolved-once
    vault handle. First call (no config) -> NOT_CONFIGURED; we then write the
    config and the very next call to the same tool succeeds.
    """
    monkeypatch.delenv("KNOTICA_CONFIG", raising=False)

    first = call_tool("list_topics", {})
    assert_error_shape(error_of(first), code="NOT_CONFIGURED")

    # Write the config mid-test, pointing the default vault at the template.
    config_dir = isolated_home / ".config" / "knotica"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'schema_version = 1\ndefault_vault = "main"\n\n[vaults.main]\npath = "{template_vault}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOTICA_CONFIG", str(config_path))

    second = call_tool("list_topics", {})
    assert_success(second)


# ---------------------------------------------------------------------------
# REQ-TOOL-05 / REQ-TOOL-06 -- read-tool payloads (configured).
# ---------------------------------------------------------------------------


def test_list_topics_reports_the_template_topic_with_a_page_count(
    vault_config: Path,
) -> None:
    """list_topics surfaces the template's topic and a numeric page count."""
    body = assert_success(call_tool("list_topics", {}))
    blob = json.dumps(body)
    assert TOPIC in blob, f"{TOPIC!r} not listed by list_topics: {body!r}"
    # A page count must appear as an integer >= the demo pages present.
    counts = _integers_in(body)
    assert any(c >= 3 for c in counts), (
        f"expected a page count >= 3 for the demo topic, saw {counts}: {body!r}"
    )


def test_read_page_returns_markdown_and_parsed_frontmatter(
    vault_config: Path,
) -> None:
    """read_page returns the known page's frontmatter fields and body text.

    Asserted through the payload's serialised form so the test is robust to the
    exact key naming the adapter chose, while still requiring the specific page
    content (frontmatter ``type: paper`` and a known body phrase).
    """
    body = assert_success(call_tool("read_page", {"topic": TOPIC, "page": KNOWN_PAGE}))
    blob = json.dumps(body)
    assert "paper" in blob, f"expected frontmatter type 'paper' in {body!r}"
    assert "Demo sample" in blob, f"expected the known page body text in {body!r}"


def test_read_page_optional_md_extension_resolves_same_page(
    vault_config: Path,
) -> None:
    """The '.md' suffix is optional; both forms resolve to the same page."""
    without = assert_success(call_tool("read_page", {"topic": TOPIC, "page": KNOWN_PAGE}))
    with_ext = assert_success(call_tool("read_page", {"topic": TOPIC, "page": f"{KNOWN_PAGE}.md"}))
    assert json.dumps(without) == json.dumps(with_ext)


def test_read_page_missing_page_returns_page_not_found(vault_config: Path) -> None:
    """read_page on a nonexistent page returns a PAGE_NOT_FOUND envelope."""
    result = call_tool("read_page", {"topic": TOPIC, "page": "no-such-page"})
    assert_error_shape(error_of(result), code="PAGE_NOT_FOUND")


@pytest.mark.parametrize("direction", ["out", "in", "both"])
def test_list_links_honours_direction(direction: str, vault_config: Path) -> None:
    """list_links accepts each direction and returns a success envelope."""
    result = call_tool("list_links", {"topic": TOPIC, "page": KNOWN_PAGE, "direction": direction})
    assert_success(result)


def test_list_links_missing_page_returns_page_not_found(vault_config: Path) -> None:
    """list_links on a nonexistent page returns a PAGE_NOT_FOUND envelope."""
    result = call_tool("list_links", {"topic": TOPIC, "page": "no-such-page"})
    assert_error_shape(error_of(result), code="PAGE_NOT_FOUND")


def test_lint_check_returns_violations_as_data_never_an_error(
    vault_config: Path,
) -> None:
    """lint_check is a successful call returning a violations list (empty=clean).

    A clean template yields an empty list; the key point is that violations are
    DATA on a success envelope, never an error.
    """
    body = assert_success(call_tool("lint_check", {}))
    violations = _first_list(body)
    assert violations is not None, f"lint_check must return a list of violations: {body!r}"
    assert isinstance(violations, list)


# ---------------------------------------------------------------------------
# REQ-SRCH-01 -- search pointer results + cursor pagination (§1.6).
# ---------------------------------------------------------------------------


def test_search_returns_pointer_results_with_the_pagination_envelope(
    vault_config: Path,
) -> None:
    """search returns pointer results and the {has_more, next_cursor} envelope.

    Results are POINTERS (topic/path/snippet/score), never page bodies.
    """
    body = assert_success(call_tool("search", {"query": "memory"}))
    assert isinstance(body, dict), f"search envelope must be an object: {body!r}"
    assert "results" in body and isinstance(body["results"], list)
    assert "has_more" in body, f"search envelope missing has_more: {body!r}"
    assert isinstance(body["has_more"], bool)
    assert "next_cursor" in body, f"search envelope missing next_cursor: {body!r}"
    if body["results"]:
        pointer = body["results"][0]
        assert {"topic", "snippet", "score"} <= set(pointer), (
            f"pointer missing contract fields: {pointer!r}"
        )
        # The pointer names a location (path/page), not a page body.
        assert "path" in pointer or "page" in pointer, (
            f"pointer must carry a page location: {pointer!r}"
        )


def test_search_limit_one_paginates_with_a_usable_next_cursor(
    vault_config: Path,
) -> None:
    """limit=1 over a multi-hit query yields has_more + a working next_cursor.

    Feeding the returned cursor back advances the walk to a distinct result --
    proving the opaque cursor round-trips (§1.6).
    """
    first = assert_success(call_tool("search", {"query": "memory", "limit": 1}))
    assert len(first["results"]) <= 1
    if not first["has_more"]:
        pytest.skip("template corpus yielded a single hit for this query")

    assert first["next_cursor"], "has_more implies a non-empty next_cursor"
    second = assert_success(
        call_tool("search", {"query": "memory", "limit": 1, "cursor": first["next_cursor"]})
    )
    first_paths = {json.dumps(r) for r in first["results"]}
    second_paths = {json.dumps(r) for r in second["results"]}
    assert first_paths.isdisjoint(second_paths), (
        "the second page must not repeat the first page's result"
    )


def test_search_malformed_cursor_returns_invalid_cursor(vault_config: Path) -> None:
    """A malformed/stale cursor yields an INVALID_CURSOR envelope, not a crash."""
    result = call_tool("search", {"query": "memory", "cursor": "not-a-real-cursor!!!"})
    assert_error_shape(error_of(result), code="INVALID_CURSOR")


# ---------------------------------------------------------------------------
# REQ-TOOL-02 -- reads make zero commits and take no lock.
# ---------------------------------------------------------------------------


def test_reads_make_zero_git_commits(vault_config: Path, template_vault: Path) -> None:
    """A batch of every read tool leaves the vault HEAD unchanged.

    Reads are side-effect-free: no commit, no lock. We compare git HEAD before
    and after exercising all five read tools against the configured vault.
    """
    before = run_git(template_vault, "rev-parse", "HEAD").strip()

    for tool, args in READ_TOOLS:
        call_tool(tool, args)

    after = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert after == before, "read tools must not create git commits"


# ---------------------------------------------------------------------------
# REQ-ERR-01 -- error object shape across a representative error.
# ---------------------------------------------------------------------------


def test_error_object_carries_typed_retryable_and_enum_code(vault_config: Path) -> None:
    """Any read error carries a §1.4 enum code and a bool retryable flag."""
    result = call_tool("read_page", {"topic": TOPIC, "page": "definitely-missing"})
    err = error_of(result)
    assert_error_shape(err)
    assert err["code"] == "PAGE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Small payload-introspection helpers (naming-agnostic assertions).
# ---------------------------------------------------------------------------


def _integers_in(obj: Any) -> list[int]:
    """Collect every int (excluding bools) reachable in a nested structure."""
    found: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found


def _first_list(obj: Any) -> list[Any] | None:
    """Return the payload itself if it is a list, else the first list value."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            if isinstance(value, list):
                return value
    return None
