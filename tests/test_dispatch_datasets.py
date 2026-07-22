"""Equivalence suite for the `datasets` dispatcher vs. its five thin tools.

The dispatcher lives in `mcp_server/tools_dispatch_datasets.py`
(`register_dispatch_datasets_tools`) and wraps `datasets_inventory`,
`datasets_records`, `datasets_bootstrap`, `datasets_bootstrap_train`, and
`datasets_freeze` from `mcp_server/tools_datasets.py`. Every test imports it
lazily so collection stays green even before the paired implementer step
lands (the concurrent BDD/TDD RED handshake).

`bootstrap`/`bootstrap_train` both construct an `AnthropicClient()` before any
network attempt -- construction resolves credentials from the environment and
raises a typed `NOT_CONFIGURED` `KnoticaError` before importing the SDK or
making a request. This hermetic suite proves equivalence via that
deterministic, network-free error path (mirroring `test_dispatch_compile.py`'s
"trainset floor not met" leg for `compile(action=run)`), never a real LLM call.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from knotica.core.golden_review import save_golden_review
from knotica.store import LocalFSStore
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
from support.trainset import populate_query_trainset
from support.vault import git_commit_count

VALID_ACTIONS = {"inventory", "records", "bootstrap", "bootstrap_train", "freeze"}

_BASELINE_PROBE_TARGET = "knotica.core.baseline_probe.maybe_auto_baseline_probe"


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_datasets import register_dispatch_datasets_tools

    return build_dispatch_server(register_dispatch_datasets_tools)


def test_datasets_dispatcher_registers_a_single_tool_documenting_the_five_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "datasets" in tools
    rendered = f"{tools['datasets'].description or ''} {tools['datasets'].inputSchema}"
    for action in VALID_ACTIONS:
        assert action in rendered


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "datasets", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    for action in VALID_ACTIONS:
        assert action in text


def test_records_missing_role_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del template_vault
    result = call_tool(_dispatch_server(), "datasets", {"action": "records", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "role" in text


def test_inventory_action_matches_datasets_inventory_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    with patch(_BASELINE_PROBE_TARGET, return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)

    old = payload_of(call_tool(build_full_server(), "datasets_inventory", {"topic": TOPIC}))
    new = payload_of(
        call_tool(_dispatch_server(), "datasets", {"action": "inventory", "topic": TOPIC})
    )
    assert "error" not in old and "error" not in new
    assert new == old


def test_records_action_matches_datasets_records_tool(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    store = LocalFSStore(template_vault)
    with patch(_BASELINE_PROBE_TARGET, return_value=None):
        populate_query_trainset(store, template_vault, TOPIC)

    old = payload_of(
        call_tool(
            build_full_server(),
            "datasets_records",
            {"topic": TOPIC, "role": "trainset", "limit": 3},
        )
    )
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "datasets",
            {"action": "records", "topic": TOPIC, "role": "trainset", "limit": 3},
        )
    )
    assert "error" not in old and "error" not in new
    assert new == old


def test_bootstrap_action_matches_datasets_bootstrap_tool_on_the_deterministic_error_path(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    old = payload_of(call_tool(build_full_server(), "datasets_bootstrap", {"topic": TOPIC}))
    new = payload_of(
        call_tool(_dispatch_server(), "datasets", {"action": "bootstrap", "topic": TOPIC})
    )
    assert old["error"]["code"] == "NOT_CONFIGURED"
    assert new == old


def test_bootstrap_train_action_matches_datasets_bootstrap_train_tool_on_the_deterministic_error_path(
    vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del vault_config
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    old = payload_of(call_tool(build_full_server(), "datasets_bootstrap_train", {"topic": TOPIC}))
    new = payload_of(
        call_tool(_dispatch_server(), "datasets", {"action": "bootstrap_train", "topic": TOPIC})
    )
    assert old["error"]["code"] == "NOT_CONFIGURED"
    assert new == old


def test_freeze_action_matches_datasets_freeze_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Representative mutating-action proof: apply actually freezes the reviewed
    candidates into `golden.jsonl` + `MANIFEST.json`, identically, on both
    surfaces. Two independent, identically-seeded vaults let the two calls run
    without one observing state the other already changed; `commit_sha` and
    `manifest.sha256` are compared separately since each candidate's frozen
    `QARecord.created` stamp is `datetime.now(UTC)` -- the two calls happen
    microseconds apart, so the frozen bytes (and their content-hash) differ
    even though every other field is identical.
    """
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")

    accepted = [
        {
            "question": f"Freeze concept {i}?",
            "reference_answer": f"Answer {i}",
            "citations": [],
            "pages_used": [],
        }
        for i in range(20)
    ]
    for vault in (vault_a, vault_b):
        store = LocalFSStore(vault)
        with patch(_BASELINE_PROBE_TARGET, return_value=None):
            populate_query_trainset(store, vault, TOPIC, golden_if_missing=False)
        save_golden_review(store, vault, TOPIC, accepted)

    before_a = git_commit_count(vault_a)
    before_b = git_commit_count(vault_b)

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    with patch(_BASELINE_PROBE_TARGET, return_value=None):
        old = payload_of(call_tool(build_full_server(), "datasets_freeze", {"topic": TOPIC}))

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    with patch(_BASELINE_PROBE_TARGET, return_value=None):
        new = payload_of(
            call_tool(_dispatch_server(), "datasets", {"action": "freeze", "topic": TOPIC})
        )

    assert "error" not in old and "error" not in new
    old_manifest = old["manifest"]
    new_manifest = new["manifest"]
    for key in ("version", "source", "split", "size"):
        assert new_manifest[key] == old_manifest[key]
    old_rest = {k: v for k, v in old.items() if k not in {"commit_sha", "manifest"}}
    new_rest = {k: v for k, v in new.items() if k not in {"commit_sha", "manifest"}}
    assert new_rest == old_rest
    assert old["n_frozen"] == 20 and new["n_frozen"] == 20
    assert git_commit_count(vault_a) - before_a == git_commit_count(vault_b) - before_b
