"""The core read/write tools honor an optional per-call ``vault`` selector.

The discriminating proof is the negative case: a tool that ignored ``vault``
would resolve the configured ``default_vault`` and succeed even for a bogus
name. Requiring an *unknown* name to fail with ``NOT_CONFIGURED`` proves the
selector actually reaches ``config.resolve``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support.dispatch import build_full_server, call_tool, payload_of

_CORE_TOOL_CALLS: list[tuple[str, dict[str, object]]] = [
    ("list_topics", {}),
    ("search", {"query": "memory"}),
    ("read_page", {"topic": "agentic-systems", "page": "agent-memory"}),
    ("list_links", {"topic": "agentic-systems", "page": "agent-memory"}),
    ("lint_check", {}),
    ("create_topic", {"topic": "throwaway"}),
    (
        "store_source",
        {
            "topic": "agentic-systems",
            "citation_key": "x2024",
            "title": "t",
            "content": "c",
            "source_url": "https://example.invalid",
        },
    ),
    ("write_page", {"topic": "agentic-systems", "page": "p", "content": "# p\n", "summary": "s"}),
]


@pytest.mark.parametrize("tool,args", _CORE_TOOL_CALLS, ids=[name for name, _ in _CORE_TOOL_CALLS])
def test_core_tool_rejects_an_unknown_vault_name(
    tool: str, args: dict[str, object], vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault

    payload = payload_of(call_tool(build_full_server(), tool, {**args, "vault": "ghost"}))

    assert payload["error"]["code"] == "NOT_CONFIGURED", payload


def test_naming_the_configured_default_resolves_the_same_vault(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config, template_vault

    default = payload_of(call_tool(build_full_server(), "list_topics", {}))
    named = payload_of(call_tool(build_full_server(), "list_topics", {"vault": "main"}))

    assert "error" not in default, default
    assert named == default, "explicitly naming the default vault resolves the same vault"
