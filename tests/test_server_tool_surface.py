"""Server-level integration checkpoint for the operator two-tier tool surface.

The tool-surface consolidation built seven action-dispatchers (`loop`,
`branches`, `compile`, `datasets`, `arena`, `golden`, `vault_health`) behind
additive aliases and proved each in isolation (dispatcher-vs-thin-tool suites
in `tests/test_dispatch_*.py`, each using a bare `FastMCP()` carrying only the
dispatcher under test). This module closes the loop at the level a real
client actually sees: the one, fully-wired `build_server()` instance.

Four checks, corresponding to this integration checkpoint's server-level
proof obligations:

1. tool-count/shape census -- 56 unique names, the 26 `DEPRECATED_ALIASES`
   carry the deprecation suffix, the 18 conversational-core tools +
   `open_dashboard` carry none;
2. every dispatcher is reachable end-to-end through the full server with one
   representative action each (`payload_of` requires a structured JSON
   envelope, so a raw-text protocol crash would fail this even if `isError`
   happened to be `True` for a legitimate business error);
3. a same-vault, server-level alias-equivalence spot-proof for one
   representative alias per family (loop, compile, golden) -- the existing
   dispatcher-level suites already prove this per-domain against a bare
   dispatch server and two independent vaults; this is the full-server,
   one-vault variant;
4. static coherence -- no thin-tool module imports a dispatcher module, and
   `dispatch_telemetry` (the falsifier instrument every dispatcher and thin
   tool alike imports) stays a leaf with no import back into `mcp_server`,
   so there is no import cycle through it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from knotica.mcp_server.dispatch_telemetry import DEPRECATED_ALIASES
from support.dispatch import TOPIC, build_full_server, call_tool, list_tools, payload_of

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "knotica" / "mcp_server"

DISPATCHER_NAMES = ("arena", "branches", "compile", "datasets", "golden", "loop", "vault_health")

#: The 18 conversational-core tools + `open_dashboard` -- neither a dispatcher
#: nor a deprecated alias; must carry no deprecation suffix. Derived by
#: elimination: 56 tools total, minus the 26 `DEPRECATED_ALIASES`, minus the 7
#: dispatchers, minus 4 standalone diagnostics not wrapped by any dispatcher
#: (`baseline_probe`, `ingest_activity_read`, `metrics_read`, `prompt_diff`),
#: leaves these 19.
CORE_AND_DASHBOARD = frozenset(
    {
        "create_topic",
        "curate_example",
        "gap_report",
        "ingest_progress",
        "lint_check",
        "list_links",
        "list_topics",
        "open_dashboard",
        "query",
        "read_page",
        "read_protocol",
        "search",
        "source_ingest_open",
        "source_ingest_submit",
        "store_source",
        "suggestions_read",
        "suggestions_review",
        "wiki_status",
        "write_page",
    }
)

#: One representative action per dispatcher (args, expected `error.code` or
#: `None` for a plain success). Read-only where the domain has one; `loop`
#: has no `mode=dry-run` gate on any action, so `baseline_policy` set to its
#: already-idempotent value is the lightest available mutation. `compile`
#: deliberately calls `action=run`, not `action=status` -- `status` crashes
#: identically on an idle topic through both the alias and the dispatcher (a
#: pre-existing bug in `compile_status_payload`, already characterized in
#: `test_dispatch_compile.py`); `run` on a fresh vault hits the deterministic,
#: side-effect-free "no trainset" `NOT_CONFIGURED` floor instead, which is a
#: clean reachability proof rather than a bug-reproduction one. `golden` load
#: on a fresh vault (no golden set bootstrapped yet) deterministically returns
#: `PAGE_NOT_FOUND` -- still a well-formed structured envelope, so still a
#: clean reachability proof.
REPRESENTATIVE_CALLS: dict[str, tuple[dict[str, Any], str | None]] = {
    "arena": ({"action": "status", "topic": TOPIC}, None),
    "branches": ({"action": "scoreboard", "topic": TOPIC}, None),
    "compile": ({"action": "run", "topic": TOPIC}, "NOT_CONFIGURED"),
    "datasets": ({"action": "inventory", "topic": TOPIC}, None),
    "golden": ({"action": "load", "topic": TOPIC}, "PAGE_NOT_FOUND"),
    "loop": ({"action": "baseline_policy", "topic": TOPIC, "policy": "latest"}, None),
    "vault_health": ({"action": "doctor", "topic": TOPIC}, None),
}

#: (alias, alias_args, dispatcher, dispatcher_args) -- one representative
#: alias per family named in the pre-mortem's dispatcher-arg-mapping
#: mitigation (loop, compile, golden), proven at the full-server level on a
#: single shared vault rather than the bare-dispatcher/two-vault pattern the
#: per-domain suites use.
ALIAS_EQUIVALENCE_CASES = (
    (
        "loop_baseline_policy",
        {"topic": TOPIC, "policy": "latest"},
        "loop",
        {"action": "baseline_policy", "topic": TOPIC, "policy": "latest"},
    ),
    (
        "compile_run",
        {"topic": TOPIC},
        "compile",
        {"action": "run", "topic": TOPIC},
    ),
    (
        "golden_review_load",
        {"topic": TOPIC},
        "golden",
        {"action": "load", "topic": TOPIC},
    ),
)


def test_tool_surface_has_56_unique_names(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    names = [tool.name for tool in list_tools(build_full_server())]
    assert len(names) == 56
    assert len(set(names)) == 56


def test_26_aliases_carry_the_deprecation_suffix(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    tools = {tool.name: tool for tool in list_tools(build_full_server())}
    deprecated = {name for name, tool in tools.items() if "Deprecated:" in (tool.description or "")}
    assert deprecated == set(DEPRECATED_ALIASES)
    assert len(deprecated) == 26


def test_core_18_and_open_dashboard_carry_no_deprecation_suffix(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault
    tools = {tool.name: tool for tool in list_tools(build_full_server())}
    assert CORE_AND_DASHBOARD <= set(tools)
    for name in CORE_AND_DASHBOARD:
        assert "Deprecated:" not in (tools[name].description or ""), name


@pytest.mark.parametrize("dispatcher", DISPATCHER_NAMES)
def test_dispatcher_reachable_end_to_end(
    dispatcher: str, vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault
    args, expected_error_code = REPRESENTATIVE_CALLS[dispatcher]
    result = call_tool(build_full_server(), dispatcher, args)
    payload = payload_of(result)
    if expected_error_code is None:
        assert "error" not in payload, payload
    else:
        assert payload["error"]["code"] == expected_error_code, payload


@pytest.mark.parametrize("alias, alias_args, dispatcher, dispatcher_args", ALIAS_EQUIVALENCE_CASES)
def test_alias_matches_dispatcher_on_the_same_vault(
    alias: str,
    alias_args: dict[str, Any],
    dispatcher: str,
    dispatcher_args: dict[str, Any],
    vault_config: Path,
    template_vault: Path,
) -> None:
    del vault_config, template_vault
    server = build_full_server()
    old = payload_of(call_tool(server, alias, alias_args))
    new = payload_of(call_tool(server, dispatcher, dispatcher_args))
    assert new == old


def _dispatch_module_stems() -> set[str]:
    return {path.stem for path in SRC_ROOT.glob("tools_dispatch_*.py")}


def _thin_tool_modules() -> list[Path]:
    """Every `tools_*` registration module except the dispatchers themselves."""
    return sorted(
        path for path in SRC_ROOT.glob("tools_*.py") if not path.stem.startswith("tools_dispatch_")
    )


def _imported_module_stems(tree: ast.Module) -> set[str]:
    stems: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            stems.add(node.module.rsplit(".", 1)[-1])
    return stems


def test_no_thin_tool_module_imports_a_dispatcher() -> None:
    dispatcher_stems = _dispatch_module_stems()
    for path in _thin_tool_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offending = _imported_module_stems(tree) & dispatcher_stems
        assert not offending, f"{path.name} imports dispatcher module(s): {offending}"


def test_dispatch_telemetry_stays_a_leaf_module() -> None:
    """`dispatch_telemetry` is imported by every dispatcher and several thin
    tools; if it imported back into `mcp_server`, that would be the one path
    an import cycle through the shared falsifier instrument could form."""
    path = SRC_ROOT / "dispatch_telemetry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("knotica.mcp_server"), node.module
