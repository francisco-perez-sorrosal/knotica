"""Phase 3a — seed-train, compiled artifacts, QueryEngine selection, compile gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knotica.core.compiled import (
    CompiledArtifact,
    CompiledDemo,
    artifact_write_bodies,
    compiled_artifact_path,
    compiled_manifest_path,
    is_compiled_healthy,
    load_compiled,
)
from knotica.core.metrics import read_last_metrics
from knotica.core.compile_promote import compile_branch_prefix, compile_promote
from knotica.core.compile_run import compile_status_payload, run_compile
from knotica.core.compile_state import CompileStage, CompileState, write_compile_state
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.query_engine import answer_question, select_runner
from support.trainset import populate_query_trainset
from knotica.core.status import COMPILE_READY_MIN_EXAMPLES, gather_wiki_status
from knotica.core.trainset import count_query_train_examples
from knotica.evals.compiled_runner import CompiledRunner
from knotica.evals.golden import load as load_golden, verify_disjoint_from_trainset
from knotica.evals.llm import Completion, FakeLLMClient, TokenUsage
from knotica.evals.runner import MessagesApiRunner
from knotica.programs.query import bootstrap_query_artifact
from knotica.store import LocalFSStore

TOPIC = "agentic-systems"


def _completion(answer: str) -> Completion:
    return Completion(
        text=json.dumps({"answer": answer, "citations": ["wang2024awm"]}),
        usage=TokenUsage(input_tokens=5, output_tokens=10),
    )


def test_compile_ready_floor_is_thirty() -> None:
    assert COMPILE_READY_MIN_EXAMPLES == 30


def test_fixture_trainset_reaches_compile_floor_and_stays_disjoint(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    result = populate_query_trainset(store, template_vault, TOPIC)
    assert result["train_appended"] == 30
    assert result["golden_seeded"] == 20
    assert count_query_train_examples(store, TOPIC) >= 30
    golden = load_golden(store, TOPIC)
    assert len(golden) >= 20
    verify_disjoint_from_trainset(store, TOPIC, golden)


def test_golden_overlap_with_trainset_is_detected(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    populate_query_trainset(store, template_vault, TOPIC)
    # Force contamination: append a golden question into qa.jsonl via raw rewrite.
    from knotica.core.operations.create_topic import qa_dataset_path
    from knotica.core.records import QARecord, parse_qa_jsonl
    from knotica.core.transaction import VaultTransaction

    golden = load_golden(store, TOPIC)[0]
    path = qa_dataset_path(TOPIC)
    existing = parse_qa_jsonl(store.read_text(path))
    contaminant = QARecord(
        id="bad-overlap",
        topic=TOPIC,
        created="2026-07-17T00:00:00Z",
        query=golden.query,
        pages_used=("agent-workflow-memory",),
        answer="overlap",
        citations=("wang2024awm",),
        verdict="good",
        corrected_answer=None,
        source="curate_example",
        model="test",
    )
    body = "".join(r.to_json_line() + "\n" for r in existing + [contaminant])
    with VaultTransaction(store, template_vault, "curate_example", TOPIC, "force overlap") as txn:
        txn.write(path, body)

    with pytest.raises(Exception) as raised:
        verify_disjoint_from_trainset(store, TOPIC, load_golden(store, TOPIC))
    assert "disjoint" in str(raised.value).lower() or "contaminat" in str(raised.value).lower()


def test_compiled_artifact_roundtrip(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    artifact = CompiledArtifact(
        optimized_instructions="Prefer exact Mind2Web 24.6% and WebArena 51.1% figures.",
        demos=(CompiledDemo("Q?", "A.", ("wang2024awm",)),),
        metrics={"baseline": 0.4, "compiled": 0.6},
        train_n=30,
        golden_n=20,
    )
    art_body, man_body = artifact_write_bodies(artifact)
    store.write_text_atomic(compiled_artifact_path(TOPIC), art_body)
    store.write_text_atomic(compiled_manifest_path(TOPIC), man_body)
    loaded = load_compiled(store, TOPIC)
    assert is_compiled_healthy(loaded)
    assert loaded is not None
    assert "24.6%" in loaded.optimized_instructions
    assert loaded.demos[0].citations == ("wang2024awm",)


def test_query_engine_selects_compiled_when_present(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    artifact = CompiledArtifact(
        optimized_instructions="Compiled instructions with demos about AWM gains.",
        demos=(CompiledDemo("q", "a", ("wang2024awm",)),),
    )
    art_body, man_body = artifact_write_bodies(artifact)
    store.write_text_atomic(compiled_artifact_path(TOPIC), art_body)
    store.write_text_atomic(compiled_manifest_path(TOPIC), man_body)

    fake = FakeLLMClient([_completion("Compiled path answered.")])
    runner = select_runner(store, TOPIC, llm_client=fake)
    assert isinstance(runner, CompiledRunner)

    result = answer_question(
        store,
        TOPIC,
        "What relative gains does AWM report?",
        runner=runner,
    )
    assert "Compiled path" in result.answer
    assert "engine" not in result.render()


def test_query_engine_falls_back_to_baseline(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    fake = FakeLLMClient([_completion("Baseline path.")])
    runner = select_runner(store, TOPIC, llm_client=fake)
    assert isinstance(runner, MessagesApiRunner)
    assert not isinstance(runner, CompiledRunner)


def test_compile_gate_requires_thirty_examples(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    with pytest.raises(KnoticaError) as raised:
        run_compile(
            store,
            template_vault,
            TOPIC,
            use_mipro=False,
            optimize_fn=lambda *a, **k: bootstrap_query_artifact(store, TOPIC, []),
            compare_fn=lambda *a: (0.4, 0.6),
        )
    assert raised.value.code is ErrorCode.NOT_CONFIGURED
    assert "30" in raised.value.message or "train examples" in raised.value.message


def test_compile_status_idle_and_running_shapes(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    idle = compile_status_payload(store, TOPIC)
    assert idle["stage"] == "idle"
    assert idle["topic"] == TOPIC
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.optimizing,
            message="working",
            trial=1,
            trial_total=3,
        ),
        title="compile progress",
    )
    running = compile_status_payload(store, TOPIC)
    assert running["stage"] == "optimizing"
    assert running["trial"] == 1
    assert running["trial_total"] == 3


def test_compile_run_returns_branch_when_seeded(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    populate_query_trainset(store, template_vault, TOPIC)
    status = gather_wiki_status(store, template_vault, topic=TOPIC)
    assert status["topics"][0]["compile_ready"] is True

    def fake_optimize(store_arg, topic, train, **kwargs):
        return bootstrap_query_artifact(store_arg, topic, train, golden_n=20)

    result = run_compile(
        store,
        template_vault,
        TOPIC,
        use_mipro=False,
        optimize_fn=fake_optimize,
        compare_fn=lambda *a: (0.41, 0.72),
    )
    assert result.stage == "completed"
    assert result.branch is not None
    assert result.branch.startswith(f"compile/{TOPIC}/")
    assert result.scalar_after is not None
    assert result.scalar_after > float(result.scalar_before or 0)

    # Artifact lives on the branch tip, not yet on default checkout.
    assert load_compiled(store, TOPIC) is None


def test_compile_branch_prefix_validation() -> None:
    assert compile_branch_prefix(TOPIC) == f"compile/{TOPIC}/"
    with pytest.raises(KnoticaError) as raised:
        compile_branch_prefix("bad/topic")
    assert raised.value.code is ErrorCode.TOPIC_NOT_FOUND


def test_compile_promote_rejects_wrong_branch_prefix(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    payload = compile_promote(
        store,
        template_vault,
        TOPIC,
        "loop/agentic-systems/deadbeef",
        apply=False,
    )
    assert "error" in payload
    assert payload["error"]["code"] == ErrorCode.INVALID_CURSOR.value


def test_compile_promote_dry_run_after_compile(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    populate_query_trainset(store, template_vault, TOPIC)

    def fake_optimize(store_arg, topic, train, **kwargs):
        return bootstrap_query_artifact(store_arg, topic, train, golden_n=20)

    result = run_compile(
        store,
        template_vault,
        TOPIC,
        use_mipro=False,
        optimize_fn=fake_optimize,
        compare_fn=lambda *a: (0.41, 0.72),
    )
    assert result.branch is not None

    dry = compile_promote(store, template_vault, TOPIC, result.branch, apply=False)
    assert "error" not in dry
    assert dry["mode"] == "dry-run"
    assert dry["merged"] is False
    assert dry["into"] in {"main", "master"}
    assert load_compiled(store, TOPIC) is None


def test_compile_promote_apply_merges_branch(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    populate_query_trainset(store, template_vault, TOPIC)

    def fake_optimize(store_arg, topic, train, **kwargs):
        return bootstrap_query_artifact(store_arg, topic, train, golden_n=20)

    result = run_compile(
        store,
        template_vault,
        TOPIC,
        use_mipro=False,
        optimize_fn=fake_optimize,
        compare_fn=lambda *a: (0.41, 0.72),
    )
    assert result.branch is not None

    applied = compile_promote(store, template_vault, TOPIC, result.branch, apply=True)
    assert "error" not in applied
    assert applied["mode"] == "apply"
    assert applied["merged"] is True
    assert applied["commit_sha"]
    assert load_compiled(store, TOPIC) is not None
    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.scalar == 0.72
    assert last.harness_version == "compile-post-eval" or last.harness_version


def test_compile_promote_refuses_dirty_tree(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    populate_query_trainset(store, template_vault, TOPIC)

    def fake_optimize(store_arg, topic, train, **kwargs):
        return bootstrap_query_artifact(store_arg, topic, train, golden_n=20)

    result = run_compile(
        store,
        template_vault,
        TOPIC,
        use_mipro=False,
        optimize_fn=fake_optimize,
        compare_fn=lambda *a: (0.41, 0.72),
    )
    assert result.branch is not None
    (template_vault / "dirty-note.md").write_text("uncommitted", encoding="utf-8")

    payload = compile_promote(store, template_vault, TOPIC, result.branch, apply=False)
    assert "error" in payload
    assert payload["error"]["code"] == ErrorCode.GIT_ERROR.value
    assert "dirty" in payload["error"]["message"].lower()
