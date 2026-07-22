"""Equivalence suite for the `loop` dispatcher vs. the four thin `loop_*` tools.

The dispatcher lives in `mcp_server/tools_dispatch_loop.py`
(`register_dispatch_loop_tools`) — every test below imports it lazily so
collection stays green even before the paired implementer step lands (the
concurrent BDD/TDD RED handshake: `ImportError` first, then GREEN).

None of the four wrapped thin tools (`loop_run_once`, `loop_set_baseline`,
`loop_baseline_policy`, `loop_rebaseline`) has a `mode=dry-run|apply` gate
today — each commits directly. So every action here already *is* the
"representative mutating action" proof; there is no dry-run leg to test for
this family (unlike `branches`/`compile`, which do gate mutations behind
`mode`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knotica.core.records import MetricsComponents, MetricsRecord
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
from support.vault import git_commit_count, run_git

VALID_ACTIONS = {"run_once", "set_baseline", "baseline_policy", "rebaseline"}


def _dispatch_server() -> object:
    from knotica.mcp_server.tools_dispatch_loop import register_dispatch_loop_tools

    return build_dispatch_server(register_dispatch_loop_tools)


def _seed_metrics_history(vault: Path, scalars: list[float], harness: str = "fake-m2") -> None:
    """Write a metrics.jsonl history directly (generation = list order)."""
    path = vault / TOPIC / ".knotica" / "metrics.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for generation, scalar in enumerate(scalars, start=1):
        record = MetricsRecord(
            topic=TOPIC,
            timestamp=f"2026-07-18T00:0{generation}:00Z",
            generation=generation,
            harness_version=harness,
            scalar=scalar,
            components=MetricsComponents(
                qa_accuracy=scalar, citation_validity=1.0, lint_violations=0.0, token_cost=0.0
            ),
            n_examples=1,
            corpus_ref="git:seeded",
            artifact_ref=None,
        )
        lines.append(record.to_json_line())
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: seed metrics history")


def test_loop_dispatcher_registers_a_single_tool_documenting_the_four_actions() -> None:
    server = _dispatch_server()
    tools = {tool.name: tool for tool in list_tools(server)}
    assert "loop" in tools
    rendered = f"{tools['loop'].description or ''} {tools['loop'].inputSchema}"
    missing = sorted(a for a in VALID_ACTIONS if a not in rendered)
    assert not missing, f"actions absent from tool docs/schema: {missing}"


def test_unknown_action_is_rejected_naming_every_valid_action(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "loop", {"action": "explode", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    missing = sorted(a for a in VALID_ACTIONS if a not in text)
    assert not missing, f"error text does not name actions: {missing}"


def test_set_baseline_missing_scalar_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    del vault_config
    result = call_tool(_dispatch_server(), "loop", {"action": "set_baseline", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "scalar" in text


def test_baseline_policy_missing_policy_is_rejected_naming_it(
    vault_config: Path, template_vault: Path
) -> None:
    """Mirrors ``set_baseline``'s ``_require_scalar`` guard: an absent
    ``policy`` must be caught before falling through to
    ``LoopRunner.set_baseline_policy`` (which would otherwise report
    ``NOT_CONFIGURED`` for an empty string, an argument problem misfiled as a
    configuration one). See LEARNINGS.md for the discrepancy this test caught
    mid-session, since resolved."""
    del vault_config
    result = call_tool(_dispatch_server(), "loop", {"action": "baseline_policy", "topic": TOPIC})
    assert result.isError
    text = rendered_error_text(result)
    assert "INVALID_ARGUMENT" in text
    assert "policy" in text


def test_run_once_action_matches_loop_run_once_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")
    assert run_git(vault_a, "rev-parse", "HEAD") == run_git(vault_b, "rev-parse", "HEAD"), (
        "both vaults must start byte-identical for the comparison to be meaningful"
    )

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    old = payload_of(call_tool(build_full_server(), "loop_run_once", {"topic": TOPIC}))

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(call_tool(_dispatch_server(), "loop", {"action": "run_once", "topic": TOPIC}))

    assert "error" not in old and "error" not in new
    assert new == old


def test_set_baseline_action_matches_loop_set_baseline_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")
    before = git_commit_count(vault_a)
    assert before == git_commit_count(vault_b)

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    old = payload_of(
        call_tool(build_full_server(), "loop_set_baseline", {"topic": TOPIC, "scalar": 0.5707})
    )

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "loop",
            {"action": "set_baseline", "topic": TOPIC, "scalar": 0.5707},
        )
    )

    assert "error" not in old and "error" not in new
    assert new == old
    # apply-mode proof: the mutation actually landed, identically, on both vaults.
    assert git_commit_count(vault_a) == before + 1
    assert git_commit_count(vault_b) == before + 1


def test_baseline_policy_action_matches_loop_baseline_policy_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    old = payload_of(
        call_tool(build_full_server(), "loop_baseline_policy", {"topic": TOPIC, "policy": "latest"})
    )

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(
        call_tool(
            _dispatch_server(),
            "loop",
            {"action": "baseline_policy", "topic": TOPIC, "policy": "latest"},
        )
    )

    assert "error" not in old and "error" not in new
    assert new == old
    assert new["baseline_policy"] == "latest"


def test_rebaseline_action_matches_loop_rebaseline_tool(
    vault_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_a = fresh_vault(vault_seed, tmp_path, "a")
    vault_b = fresh_vault(vault_seed, tmp_path, "b")
    _seed_metrics_history(vault_a, [0.60, 0.90, 0.70])
    _seed_metrics_history(vault_b, [0.60, 0.90, 0.70])

    configure_default_vault(monkeypatch, tmp_path, "a", vault_a)
    old = payload_of(
        call_tool(build_full_server(), "loop_rebaseline", {"topic": TOPIC, "mode": "best"})
    )

    configure_default_vault(monkeypatch, tmp_path, "b", vault_b)
    new = payload_of(
        call_tool(
            _dispatch_server(), "loop", {"action": "rebaseline", "topic": TOPIC, "mode": "best"}
        )
    )

    assert "error" not in old and "error" not in new
    assert new == old
    assert new["baseline_scalar"] == 0.90
