"""Equivalence suite for the `golden` dispatcher vs. its two thin tools.

The dispatcher lives in `mcp_server/tools_dispatch_golden.py`
(`register_dispatch_golden_tools`) and wraps `golden_review_load`/
`golden_review_save` from `mcp_server/tools_golden.py`. Every test imports it
lazily so collection stays green even before the paired implementer step
lands (the concurrent BDD/TDD RED handshake).

`save` commits directly (no `mode=dry-run|apply` gate exists on
`golden_review_save` today) -- the representative mutating-action proof below
is a real apply on two independent, identically-seeded vaults.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support.dispatch import (
    TOPIC,
    build_dispatch_server,
    build_full_server,
    call_tool,
    configure_default_vault,
    fresh_vault,
    list_tools,
    payload_of,
    rendered_error_text,
)
from support.vault import git_commit_count

VALID_ACTIONS = {"load", "save"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_golden import register_dispatch_golden_tools

    return build_dispatch_server(register_dispatch_golden_tools)


def _seed_staging(vault: Path, *, n: int = 2) -> Path:
    path = vault / TOPIC / ".knotica" / "datasets" / "golden.staging.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "question": f"What is concept {i}?",
            "reference_answer": f"Concept {i} is defined in the wiki.",
            "citations": [],
            "pages_used": [],
        }
        for i in range(n)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_golden_dispatcher_registers_a_single_tool_documenting_the_two_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "golden" in tools
    rendered = f"{tools['golden'].description or ''} {tools['golden'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "golden", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_save_missing_accepted_json_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "golden", {"action": "save", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "accepted_json" in text


def test_load_action_matches_golden_review_load_tool_when_staging_is_absent(
    vault_config: Path,
) -> None:
    old = payload_of(call_tool(build_full_server(), "golden_review_load", {"topic": TOPIC}))
    new = payload_of(call_tool(_dispatch_server(), "golden", {"action": "load", "topic": TOPIC}))
    assert old["error"]["code"] == "PAGE_NOT_FOUND"
    assert new == old


def test_load_action_matches_golden_review_load_tool_with_seeded_staging(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    _seed_staging(template_vault, n=2)

    old = payload_of(call_tool(build_full_server(), "golden_review_load", {"topic": TOPIC}))
    new = payload_of(call_tool(_dispatch_server(), "golden", {"action": "load", "topic": TOPIC}))
    assert "error" not in old and "error" not in new
    assert new == old
    assert new["floor"] == 20


def test_save_action_matches_golden_review_save_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Representative mutating-action proof: apply actually commits
    `golden.staging.reviewed.jsonl`, identically, on both surfaces.
    `commit_sha` is compared separately since each vault's own commit hash
    necessarily differs.
    """
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")
    before_a = git_commit_count(vault_a)
    before_b = git_commit_count(vault_b)

    accepted_json = json.dumps(
        [
            {
                "question": "What is concept 0?",
                "reference_answer": "Concept 0 is defined in the wiki.",
                "citations": [],
                "pages_used": [],
            }
        ]
    )

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    old = payload_of(
        call_tool(
            build_full_server(),
            "golden_review_save",
            {"topic": TOPIC, "accepted_json": accepted_json},
        )
    )

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "golden",
            {"action": "save", "topic": TOPIC, "accepted_json": accepted_json},
        )
    )

    assert "error" not in old and "error" not in new
    vault_specific = {"commit_sha", "written"}
    old_rest = {k: v for k, v in old.items() if k not in vault_specific}
    new_rest = {k: v for k, v in new.items() if k not in vault_specific}
    assert new_rest == old_rest
    # `written` embeds each vault's absolute root by design; equivalence holds
    # on the vault-relative suffix.
    assert Path(old["written"]).relative_to(vault_a) == Path(new["written"]).relative_to(vault_b)
    assert old["count"] == 1 and new["count"] == 1
    assert git_commit_count(vault_a) - before_a == git_commit_count(vault_b) - before_b
