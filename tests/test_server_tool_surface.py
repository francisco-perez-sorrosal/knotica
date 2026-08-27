"""Server-level integration checkpoint for the operator two-tier tool surface.

The tool-surface consolidation built action-dispatchers (`loop`,
`branches`, `compile`, `datasets`, `arena`, `golden`, `vault`, `vault_health`)
and proved each in isolation (dispatcher-vs-thin-tool suites in
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

1. tool-count/shape census -- `EXPECTED_TOOL_COUNT` unique names, none
   carrying a deprecation suffix;
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

#: Every action dispatcher whose reachability this module proves end-to-end.
#: Eight of the nine topical dispatchers are no longer registered — the lanes
#: absorbed them — so the proof routes through the lane that now carries each
#: verb. `vault` is unlaned and still flat, so it is called directly.
DISPATCHER_NAMES = (
    "arena",
    "branches",
    "compile",
    "datasets",
    "golden",
    "loop",
    "notes",
    "vault",
    "vault_health",
)

#: The thirteen Tier-1 conversational tools plus `open_dashboard` — the whole
#: flat surface that survives the lane re-cut. Tier 1 keeps its exact names
#: because each of these is called mid-turn by the client-as-brain and most are
#: multi-lane, so a lane prefix would state something false; `open_dashboard` is
#: unlaned Tier 2. `note_capture` is here rather than a `notes` action because
#: capture friction is fatal to the feature (INTERFACE_DESIGN.md §1).
CORE_AND_DASHBOARD = frozenset(
    {
        "curate_example",
        "gap_report",
        "ingest_progress",
        "list_links",
        "list_topics",
        "note_capture",
        "open_dashboard",
        "query",
        "read_page",
        "read_protocol",
        "search",
        "store_source",
        "wiki_status",
        "write_page",
    }
)

#: One representative call per dispatcher: the tool to call, its arguments, and
#: the expected `error.code` (or `None` for a plain success). Read-only where
#: the domain has one; `loop` has no `mode=dry-run` gate on any action, so
#: `baseline_policy` set to its already-idempotent value is the lightest
#: available mutation. `compile` deliberately calls `run`, not `status` --
#: `status` crashes identically on an idle topic (a pre-existing bug in
#: `compile_status_payload`, already characterized in `test_dispatch_compile.py`);
#: `run` on a fresh vault hits the deterministic, side-effect-free "no trainset"
#: `NOT_CONFIGURED` floor instead, which is a clean reachability proof rather
#: than a bug-reproduction one. `golden` load on a fresh vault (no golden set
#: bootstrapped yet) deterministically returns `PAGE_NOT_FOUND` -- still a
#: well-formed structured envelope, so still a clean reachability proof. A verb
#: that owns its own `action` parameter takes it as `<verb>_action` inside a
#: lane, because the lane's own selector is already called `action`.
REPRESENTATIVE_CALLS: dict[str, tuple[str, dict[str, Any], str | None]] = {
    "arena": ("improve", {"action": "arena", "arena_action": "status", "topic": TOPIC}, None),
    "branches": (
        "improve",
        {"action": "branches", "branches_action": "scoreboard", "topic": TOPIC},
        None,
    ),
    "compile": (
        "improve",
        {"action": "compile", "compile_action": "run", "topic": TOPIC},
        "NOT_CONFIGURED",
    ),
    "datasets": (
        "improve",
        {"action": "datasets", "datasets_action": "inventory", "topic": TOPIC},
        None,
    ),
    "golden": (
        "improve",
        {"action": "golden", "golden_action": "load", "topic": TOPIC},
        "PAGE_NOT_FOUND",
    ),
    "loop": (
        "improve",
        {
            "action": "loop",
            "loop_action": "baseline_policy",
            "topic": TOPIC,
            "policy": "latest",
        },
        None,
    ),
    "notes": ("tend", {"action": "notes", "notes_action": "list", "topic": TOPIC}, None),
    "vault": ("vault", {"action": "status"}, None),
    "vault_health": (
        "tend",
        {"action": "vault_health", "vault_health_action": "doctor", "topic": TOPIC},
        None,
    ),
}


#: The ceiling the published surface must not exceed once the operator-tier
#: flat tools are removed: the Tier-1 conversational core, the two unlaned
#: Tier-2 tools, and the six lane dispatchers (see
#: `tests/test_lane_rename_invariants.py` for the by-name preservation
#: proofs this ceiling summarizes). RED until that removal lands: today's
#: surface additively carries the six lane dispatchers ALONGSIDE the flat
#: tools they will absorb, so no intermediate commit is half-renamed.
MAX_TOOL_COUNT_AFTER_LANE_RENAME = 22


def test_tool_surface_has_no_duplicate_names(vault_config: Path, template_vault: Path) -> None:
    del vault_config, template_vault
    names = [tool.name for tool in list_tools(build_full_server())]
    assert len(names) == len(set(names)), f"duplicate tool name(s) registered: {names}"


def test_tool_surface_shrinks_to_the_tiered_ceiling_once_operator_tools_are_removed(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault
    names = [tool.name for tool in list_tools(build_full_server())]
    assert len(names) <= MAX_TOOL_COUNT_AFTER_LANE_RENAME, (
        f"expected at most {MAX_TOOL_COUNT_AFTER_LANE_RENAME} registrations after the "
        f"operator-tool removal, got {len(names)}: {sorted(names)}"
    )


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
    tool, args, expected_error_code = REPRESENTATIVE_CALLS[dispatcher]
    result = call_tool(build_full_server(), tool, args)
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
