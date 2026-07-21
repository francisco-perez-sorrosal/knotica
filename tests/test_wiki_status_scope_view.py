"""Behavioral tests for ``wiki_status``'s progressive ``view`` parameter.

``view="scope"`` is the new cheapest view for the routing scope-check: a
stateless, deterministic ``{schema_version, vault_name, topics, totals}``
read with no liveness/eval side effects. The pre-existing default (omitted
``view``, i.e. today's "summary" shape) and its nested ``loop`` sub-object
must stay byte-for-byte unchanged — the additive-view back-compat guarantee.

Production imports are deferred into test bodies so collection succeeds even
while ``view`` support is still in flight on the paired implementation step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

TOPIC = "agentic-systems"
SECOND_TOPIC = "robotics"

# The full key set of today's (pre-``view``) default wiki_status payload —
# frozen from `knotica.core.status.gather_wiki_status` before this step
# landed. Back-compat requires the default call keep exactly this shape.
DEFAULT_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "vault",
        "vault_name",
        "vault_path",
        "default_vault",
        "available_vaults",
        "compile_ready_threshold",
        "eval_min_golden",
        "topics",
        "totals",
        "last_lint",
        "unpushed",
        "gate",
        "loop",
        "compile",
        "llm",
    }
)

# The nested ``loop`` sub-object's key set for a single-topic scope, frozen
# from `knotica.core.status._gate_and_loop` before this step landed. This is
# the shape the interface design calls the "loop view" — it already exists
# today as a nested key, not a new top-level `view` value.
LOOP_SUBOBJECT_KEYS = frozenset(
    {
        "runner",
        "progress",
        "baseline_policy",
        "stage",
        "candidate_branch",
        "last_decision",
        "arena_race_id",
        "arena_stage",
        "baseline_frozen",
        "baseline_scalar",
        "pending_candidates",
        "metrics_hint",
    }
)

SCOPE_VIEW_KEYS = frozenset({"schema_version", "vault_name", "topics", "totals"})


def _build_server() -> Any:
    from knotica.mcp_server import server as server_mod

    if hasattr(server_mod, "build_server"):
        return server_mod.build_server()
    return server_mod.mcp


async def _call(server: Any, tool: str, args: dict[str, Any]) -> Any:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def call_tool(tool: str, args: dict[str, Any], *, server: Any | None = None) -> Any:
    srv = server if server is not None else _build_server()
    return anyio.run(_call, srv, tool, args)


def payload_of(result: Any) -> Any:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return json.loads(text)
    raise AssertionError(f"result carried no structured or text payload: {result!r}")


def error_of(result: Any) -> dict[str, Any]:
    body = payload_of(result)
    assert isinstance(body, dict) and "error" in body
    assert getattr(result, "isError", False) is True
    return body["error"]


def assert_success(result: Any) -> Any:
    body = payload_of(result)
    if isinstance(body, dict):
        assert "error" not in body, f"expected success, got error: {body!r}"
    assert getattr(result, "isError", False) is False
    return body


# ---------------------------------------------------------------------------
# Default call (no `view`) — back-compat with today's shape
# ---------------------------------------------------------------------------


def test_default_call_omits_view_and_matches_todays_summary_shape(vault_config: Path) -> None:
    """Calling wiki_status with no `view` arg keeps today's full key set."""
    del vault_config
    body = assert_success(call_tool("wiki_status", {}))
    assert set(body) == DEFAULT_PAYLOAD_KEYS


def test_omitted_view_and_explicit_summary_view_are_identical(vault_config: Path) -> None:
    """`view` defaults to "summary" — omitting it and passing it explicitly agree."""
    del vault_config
    implicit = assert_success(call_tool("wiki_status", {}))
    explicit = assert_success(call_tool("wiki_status", {"view": "summary"}))
    assert implicit == explicit


def test_default_calls_loop_subobject_shape_is_unchanged(vault_config: Path) -> None:
    """The nested `loop` block (single-topic scope) keeps today's field set.

    This is the "loop view" shape named in the interface design's progressive
    view table — it already exists as a nested key in the default payload,
    not a new top-level `view` value in this step.
    """
    del vault_config
    body = assert_success(call_tool("wiki_status", {"topic": TOPIC}))
    assert set(body["loop"]) == LOOP_SUBOBJECT_KEYS


# ---------------------------------------------------------------------------
# view="scope" — shape
# ---------------------------------------------------------------------------


def test_view_scope_returns_exactly_the_documented_minimal_shape(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("wiki_status", {"view": "scope"}))
    assert set(body) == SCOPE_VIEW_KEYS


def test_view_scope_reports_schema_version_and_vault_name(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("wiki_status", {"view": "scope"}))
    full = assert_success(call_tool("wiki_status", {}))
    assert body["schema_version"] == full["schema_version"]
    assert body["vault_name"] == full["vault_name"]


def test_view_scope_lists_the_single_configured_topic(vault_config: Path) -> None:
    del vault_config
    body = assert_success(call_tool("wiki_status", {"view": "scope"}))
    assert body["topics"] == [TOPIC]
    assert body["totals"]["topics"] == 1


def test_view_scope_enumerates_every_topic_in_a_multi_topic_vault(vault_config: Path) -> None:
    """A vault with two topics lists both in the scope view (no topic filter)."""
    del vault_config
    assert_success(call_tool("create_topic", {"topic": SECOND_TOPIC}))
    body = assert_success(call_tool("wiki_status", {"view": "scope"}))
    assert set(body["topics"]) == {TOPIC, SECOND_TOPIC}
    assert body["totals"]["topics"] == 2


# ---------------------------------------------------------------------------
# view="scope" — determinism
# ---------------------------------------------------------------------------


def test_view_scope_is_deterministic_across_two_calls(vault_config: Path) -> None:
    del vault_config
    first = assert_success(call_tool("wiki_status", {"view": "scope"}))
    second = assert_success(call_tool("wiki_status", {"view": "scope"}))
    assert first == second


# ---------------------------------------------------------------------------
# view="scope" — cheapness by construction (no liveness/eval side effects)
# ---------------------------------------------------------------------------


def test_view_scope_never_probes_loop_runner_liveness(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`view=scope` must not read the loop-runner heartbeat.

    Canary is proven non-vacuous: the same patch DOES fire for the default
    (single-topic) view, which calls `read_runner_liveness` today.
    """
    del vault_config
    from knotica.core import status as status_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read_runner_liveness must not be called for view=scope")

    monkeypatch.setattr(status_module, "read_runner_liveness", _boom)

    # Non-vacuity: the default view (single topic) does call it. FastMCP
    # catches the injected AssertionError inside the tool call and surfaces
    # it as an error result rather than letting it propagate to the caller.
    triggered = call_tool("wiki_status", {"topic": TOPIC})
    assert triggered.isError is True
    assert "must not be called" in triggered.content[0].text

    # The actual assertion: scope view is untouched by the patch.
    assert_success(call_tool("wiki_status", {"view": "scope"}))


def test_view_scope_never_probes_llm_availability(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`view=scope` must not run the LLM-credential/dependency probe.

    Canary is proven non-vacuous: the same patch DOES fire for the default
    view, which always computes `llm` availability today.
    """
    del vault_config
    from knotica.core import status as status_module

    def _boom() -> None:
        raise AssertionError("_llm_availability must not be called for view=scope")

    monkeypatch.setattr(status_module, "_llm_availability", _boom)

    # Non-vacuity: the default view does call it (see the sibling test's
    # note on how FastMCP surfaces an injected exception as an error result).
    triggered = call_tool("wiki_status", {})
    assert triggered.isError is True
    assert "must not be called" in triggered.content[0].text

    # The actual assertion: scope view is untouched by the patch.
    assert_success(call_tool("wiki_status", {"view": "scope"}))


def test_view_scope_works_with_no_runner_heartbeat_state_present(vault_config: Path) -> None:
    """`view=scope` succeeds with no `.knotica/locks/` heartbeat state at all."""
    del vault_config
    body = assert_success(call_tool("wiki_status", {"view": "scope"}))
    assert body["topics"] == [TOPIC]


def test_view_scope_is_read_only(vault_config: Path, template_vault: Path) -> None:
    from support.vault import run_git

    del vault_config
    before = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert_success(call_tool("wiki_status", {"view": "scope"}))
    after = run_git(template_vault, "rev-parse", "HEAD").strip()
    assert before == after


# ---------------------------------------------------------------------------
# Invalid `view`
# ---------------------------------------------------------------------------


def test_invalid_view_value_returns_invalid_argument_naming_the_argument(
    vault_config: Path,
) -> None:
    del vault_config
    err = error_of(call_tool("wiki_status", {"view": "bogus"}))
    assert err["code"] == "INVALID_ARGUMENT"
    assert "view" in err["message"]
    assert "scope" in err["message"]
    assert err["retryable"] is False
