"""Server-level integration checkpoint for the operator two-tier tool surface.

The tool-surface consolidation built seven action-dispatchers (`loop`,
`branches`, `compile`, `datasets`, `arena`, `golden`, `vault_health`) and
proved each in isolation (dispatcher-vs-thin-tool suites in
`tests/test_dispatch_*.py`, each using a bare `FastMCP()` carrying only the
dispatcher under test). The 26 deprecated flat-tool aliases that once
coexisted with the dispatchers (kept for one release cycle, per
`.ai-state/decisions/045-tiered-tool-surface-topology.md`) were removed
outright once the migration-window premise (external clients) no longer
held — the dispatchers are now the sole entry points. This module closes the
loop at the level a real client actually sees: the one, fully-wired
`build_server()` instance.

Three checks, corresponding to this integration checkpoint's server-level
proof obligations:

1. tool-count/shape census -- 30 unique names, none carrying a deprecation
   suffix;
2. every dispatcher is reachable end-to-end through the full server with one
   representative action each (`payload_of` requires a structured JSON
   envelope, so a raw-text protocol crash would fail this even if `isError`
   happened to be `True` for a legitimate business error);
3. static coherence -- no thin-tool module imports a dispatcher module, and
   `dispatch_telemetry` (the mis-selection instrument every dispatcher
   imports) stays a leaf with no import back into `mcp_server`, so there is
   no import cycle through it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from support.dispatch import TOPIC, build_full_server, call_tool, list_tools, payload_of

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "knotica" / "mcp_server"

DISPATCHER_NAMES = ("arena", "branches", "compile", "datasets", "golden", "loop", "vault_health")

#: The 19 conversational-core tools + `open_dashboard` -- neither a
#: dispatcher nor a standalone diagnostic. Derived by elimination: 30 tools
#: total, minus the 7 dispatchers, minus 4 standalone diagnostics not
#: wrapped by any dispatcher (`baseline_probe`, `ingest_activity_read`,
#: `metrics_read`, `prompt_diff`), leaves these 19.
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
#: identically on an idle topic (a pre-existing bug in
#: `compile_status_payload`, already characterized in
#: `test_dispatch_compile.py`); `run` on a fresh vault hits the
#: deterministic, side-effect-free "no trainset" `NOT_CONFIGURED` floor
#: instead, which is a clean reachability proof rather than a
#: bug-reproduction one. `golden` load on a fresh vault (no golden set
#: bootstrapped yet) deterministically returns `PAGE_NOT_FOUND` -- still a
#: well-formed structured envelope, so still a clean reachability proof.
REPRESENTATIVE_CALLS: dict[str, tuple[dict[str, Any], str | None]] = {
    "arena": ({"action": "status", "topic": TOPIC}, None),
    "branches": ({"action": "scoreboard", "topic": TOPIC}, None),
    "compile": ({"action": "run", "topic": TOPIC}, "NOT_CONFIGURED"),
    "datasets": ({"action": "inventory", "topic": TOPIC}, None),
    "golden": ({"action": "load", "topic": TOPIC}, "PAGE_NOT_FOUND"),
    "loop": ({"action": "baseline_policy", "topic": TOPIC, "policy": "latest"}, None),
    "vault_health": ({"action": "doctor", "topic": TOPIC}, None),
}


def test_tool_surface_has_30_unique_names(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    names = [tool.name for tool in list_tools(build_full_server())]
    assert len(names) == 30
    assert len(set(names)) == 30


def test_no_tool_carries_a_deprecation_suffix(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    tools = {tool.name: tool for tool in list_tools(build_full_server())}
    assert CORE_AND_DASHBOARD <= set(tools)
    for name, tool in tools.items():
        assert "Deprecated:" not in (tool.description or ""), name


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
    """`dispatch_telemetry` is imported by every dispatcher; if it imported
    back into `mcp_server`, that would be the one path an import cycle
    through the shared instrument could form."""
    path = SRC_ROOT / "dispatch_telemetry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("knotica.mcp_server"), node.module
