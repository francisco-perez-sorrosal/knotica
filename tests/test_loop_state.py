"""Unit tests for :mod:`knotica.core.loop_state` and gate derivation."""

from pathlib import Path

import pytest

from knotica.core.compile_state import CompileStage, CompileState, write_compile_state
from knotica.core.loop_state import (
    LoopDecision,
    LoopStage,
    LoopState,
    compute_gate,
    empty_loop_state,
    read_loop_state,
    write_loop_state,
)
from knotica.core.status import gather_wiki_status
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def test_loop_state_round_trip(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    state = empty_loop_state(TOPIC).model_copy(
        update={
            "baseline_scalar": 0.5707,
            "baseline_harness_version": "h1",
            "stage": LoopStage.idle,
        }
    )
    written = write_loop_state(store, template_vault, state, title="seed baseline")
    loaded = read_loop_state(store, TOPIC)
    assert loaded is not None
    assert loaded.baseline_scalar == 0.5707
    assert loaded.schema_version == 1
    assert loaded.updated_at >= written.updated_at or loaded.topic == TOPIC


def test_compute_gate_unknown_without_baseline() -> None:
    gate = compute_gate(None, last_scalar=0.6)
    assert gate == {"state": "unknown", "baseline": None, "last_scalar": 0.6}


def test_compute_gate_pass_fail_and_harness_mismatch() -> None:
    state = LoopState(
        topic=TOPIC,
        baseline_scalar=0.57,
        baseline_harness_version="h1",
    )
    assert compute_gate(state, last_scalar=0.58, last_harness_version="h1")["state"] == "pass"
    assert compute_gate(state, last_scalar=0.56, last_harness_version="h1")["state"] == "fail"
    assert compute_gate(state, last_scalar=0.58, last_harness_version="h2")["state"] == "unknown"


def test_wiki_status_surfaces_gate_from_loop_state(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(
            update={
                "baseline_scalar": 0.57,
                "baseline_harness_version": "h1",
                "last_scalar": 0.56,
                "last_harness_version": "h1",
                "last_decision": LoopDecision.fail,
                "stage": LoopStage.failed,
            }
        ),
        title="seed fail gate",
    )
    payload = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert payload["gate"]["state"] == "fail"
    assert payload["gate"]["baseline"] == 0.57
    assert payload["gate"]["last_scalar"] == 0.56
    assert payload["loop"]["stage"] == "failed"
    assert payload["loop"]["last_decision"] == "fail"


def test_wiki_status_lists_pending_loop_candidates(template_vault: Path) -> None:
    from knotica.core.vcs import VaultVcs
    from support.vault import run_git

    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.57}),
        title="seed baseline",
    )
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = "loop/c/wound-demo"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    wound = template_vault / TOPIC / ".knotica" / "prompts" / "query.md"
    wound.parent.mkdir(parents=True, exist_ok=True)
    wound.write_text("# wounded\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: wound branch")
    vcs.checkout_branch(default)

    payload = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert payload["loop"]["baseline_frozen"] is True
    assert payload["loop"]["baseline_scalar"] == 0.57
    pending = payload["loop"]["pending_candidates"]
    assert len(pending) == 1
    assert pending[0]["branch"] == branch
    assert pending[0]["pending"] is True


def test_wiki_status_falls_back_to_compile_scalar_without_baseline(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            scalar_before=0.4,
            scalar_after=0.55,
        ),
        title="compile done",
    )

    payload = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert payload["gate"]["state"] == "unknown"
    assert payload["gate"]["baseline"] is None
    assert payload["gate"]["last_scalar"] == pytest.approx(0.55)
    assert payload["loop"]["baseline_frozen"] is False
    assert payload["compile"]["scalar_after"] == pytest.approx(0.55)
