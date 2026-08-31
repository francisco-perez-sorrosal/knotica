"""MCP tool ``prompt_diff`` — git unified diff for vault ``query.md``."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.prompt_diff import prompt_diff
from knotica.mcp_server import envelope, tool_params
from knotica.mcp_server.vault_ctx import with_resolved_vault

_DESCRIPTION = (
    "Deterministic unified diff for the query operation prompt. "
    "`mode=git` (default): diff `query.md` between compile/loop branch and default branch, "
    "or HEAD vs the previous commit touching the file. "
    "`mode=compiled`: diff vault `query.md` against `optimized_instructions` in "
    "`.knotica/compiled/query_v1.json` — use this when `improve action=compile compile_action=run` "
    "updates the artifact, not query.md. "
    "Pass `branch` with `mode=compiled` to preview an open compile branch before promote. "
    "Read-only."
)

ToolResult = CallToolResult

#: Which artifact pair the diff is taken over. `_dispatch` below falls back to
#: the first entry for anything it does not recognise, so this tuple is both
#: the published enum and the accepted set.
_DIFF_MODES: tuple[str, ...] = ("git", "compiled")

_DiffMode = Annotated[
    str,
    tool_params.grounded(
        "What to diff: 'git' (the default) compares the prompt files across refs; "
        "'compiled' compares the compiled DSPy artifacts.",
        _DIFF_MODES,
    ),
]

_BaseRef = Annotated[
    str,
    tool_params.grounded(
        "Git ref to diff from; empty (the default) uses the branch's merge base.",
    ),
]

_HeadRef = Annotated[
    str,
    tool_params.grounded(
        "Git ref to diff to; empty (the default) uses the branch tip.",
    ),
]

_HistoryId = Annotated[
    str,
    tool_params.grounded(
        "Compile-history entry to diff instead of a ref pair; empty (the default) uses the refs above.",
    ),
]


def register_prompt_diff_tools(mcp: FastMCP) -> None:
    """Register the prompt diff tool."""

    @mcp.tool(name="prompt_diff", description=_DESCRIPTION)
    def prompt_diff_tool(
        topic: tool_params.Topic,
        branch: tool_params.Branch = "",
        base_ref: _BaseRef = "",
        head_ref: _HeadRef = "",
        history_id: _HistoryId = "",
        mode: _DiffMode = _DIFF_MODES[0],
        vault: tool_params.Vault = "",
    ) -> ToolResult:
        cleaned_branch = branch.strip() or None
        cleaned_base = base_ref.strip() or None
        cleaned_head = head_ref.strip() or None
        cleaned_history = history_id.strip() or None
        cleaned_mode = mode.strip().lower() or _DIFF_MODES[0]
        if cleaned_mode not in _DIFF_MODES:
            cleaned_mode = _DIFF_MODES[0]
        return with_resolved_vault(
            vault,
            lambda store, resolved: envelope.read_ok(
                prompt_diff(
                    store,
                    resolved.path,
                    topic,
                    branch=cleaned_branch,
                    base_ref=cleaned_base,
                    head_ref=cleaned_head,
                    history_id=cleaned_history,
                    mode=cleaned_mode,  # type: ignore[arg-type]
                )
            ),
        )
