"""MCP tool ``prompt_diff`` — git unified diff for vault ``query.md``."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from knotica.core.prompt_diff import prompt_diff
from knotica.mcp_server import envelope
from knotica.mcp_server.vault_ctx import with_resolved_vault

_DESCRIPTION = (
    "Deterministic unified diff for the query operation prompt. "
    "`mode=git` (default): diff `query.md` between compile/loop branch and default branch, "
    "or HEAD vs the previous commit touching the file. "
    "`mode=compiled`: diff vault `query.md` against `optimized_instructions` in "
    "`.knotica/compiled/query_v1.json` — use this when compile updates the artifact, not query.md. "
    "Pass `branch` with `mode=compiled` to preview an open compile branch before promote. "
    "Read-only."
)

ToolResult = CallToolResult


def register_prompt_diff_tools(mcp: FastMCP) -> None:
    """Register the prompt diff tool."""

    @mcp.tool(name="prompt_diff", description=_DESCRIPTION)
    def prompt_diff_tool(
        topic: str,
        branch: str = "",
        base_ref: str = "",
        head_ref: str = "",
        history_id: str = "",
        mode: str = "git",
        vault: str = "",
    ) -> ToolResult:
        cleaned_branch = branch.strip() or None
        cleaned_base = base_ref.strip() or None
        cleaned_head = head_ref.strip() or None
        cleaned_history = history_id.strip() or None
        cleaned_mode = mode.strip().lower() or "git"
        if cleaned_mode not in {"git", "compiled"}:
            cleaned_mode = "git"
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
