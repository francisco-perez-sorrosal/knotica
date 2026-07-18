"""Phase 3a compile pipeline — doctor → gate → clone → MIPRO → branch → post-eval."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knotica.core.compiled import (
    CompiledArtifact,
    artifact_write_bodies,
    compiled_artifact_path,
    compiled_manifest_path,
)
from knotica.core.compile_state import (
    CompileStage,
    CompileState,
    empty_compile_state,
    read_compile_state,
    record_compile_finished,
    write_compile_state,
)
from knotica.core.doctor import run_doctor_checks
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.status import COMPILE_READY_MIN_EXAMPLES
from knotica.core.trainset import load_query_train_examples
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import GitError, VaultVcs
from knotica.evals.compiled_runner import CompiledRunner
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.evals.golden import EVAL_MIN_GOLDEN, GoldenSetMissingError, load as load_golden
from knotica.evals.lexical import lexical_pair_score
from knotica.evals.llm import LLMClient
from knotica.evals.runner import BaselineRunner, MessagesApiRunner
from knotica.programs.query import optimize_query
from knotica.store import LocalFSStore, VaultStore

__all__ = [
    "CompileResult",
    "compile_status_payload",
    "run_compile",
]


@dataclass(frozen=True, slots=True)
class CompileResult:
    """Outcome of one compile attempt."""

    topic: str
    branch: str | None
    stage: str
    message: str
    scalar_before: float | None
    scalar_after: float | None
    train_n: int
    golden_n: int

    def render(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "branch": self.branch,
            "stage": self.stage,
            "message": self.message,
            "scalar_before": self.scalar_before,
            "scalar_after": self.scalar_after,
            "train_n": self.train_n,
            "golden_n": self.golden_n,
        }


CompareFn = Callable[
    [VaultStore, str, BaselineRunner, BaselineRunner, list[Any]],
    tuple[float, float],
]


def compile_status_payload(store: VaultStore, topic: str) -> dict[str, Any]:
    """Shape for MCP ``compile_status`` / dashboard polling."""
    cleaned = topic.strip().strip("/")
    state = read_compile_state(store, cleaned) or empty_compile_state(cleaned)
    return state.render()


def run_compile(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    *,
    config_detail: str = "configured",
    llm_client: LLMClient | None = None,
    worker_snapshot: str = WORKER_SNAPSHOT,
    use_mipro: bool = True,
    optimize_fn: Callable[..., CompiledArtifact] | None = None,
    compare_fn: CompareFn | None = None,
) -> CompileResult:
    """Run the Phase 3a compile pipeline; return branch merge instructions on success."""
    root = Path(vault_root)
    cleaned = topic.strip().strip("/")
    if not cleaned or "/" in cleaned:
        raise KnoticaError(
            ErrorCode.TOPIC_NOT_FOUND,
            f"compile failed because topic {topic!r} is invalid",
        )
    if not store.exists(cleaned):
        raise KnoticaError(
            ErrorCode.TOPIC_NOT_FOUND,
            f"compile failed because no topic named '{cleaned}' exists.",
        )

    # 1. Doctor quick + dirty tree guard
    _doctor_gate(store, root, config_detail=config_detail)

    # 2. Train / golden floors
    train = load_query_train_examples(store, cleaned)
    train_n = len(train)
    if train_n < COMPILE_READY_MIN_EXAMPLES:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            (
                f"compile failed because topic '{cleaned}' has {train_n} query-style "
                f"train examples; need at least {COMPILE_READY_MIN_EXAMPLES}."
            ),
            fix=(
                "Grow the trainset through the flywheel: ask questions against the "
                "wiki and save good answers with `curate_example` until the floor "
                "is reached."
            ),
        )
    try:
        golden = load_golden(store, cleaned)
    except GoldenSetMissingError as error:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            f"compile failed because topic '{cleaned}' has no held-out golden set.",
            fix=f"Bootstrap and freeze golden.jsonl for '{cleaned}' first.",
        ) from error
    golden_n = len(golden)
    if golden_n < EVAL_MIN_GOLDEN:
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            (
                f"compile failed because topic '{cleaned}' golden set has {golden_n} "
                f"examples; need at least {EVAL_MIN_GOLDEN}."
            ),
            fix="Freeze more held-out golden pairs before compiling.",
        )

    state = CompileState(
        topic=cleaned,
        stage=CompileStage.running,
        message="clone and optimize",
        trial=0,
        trial_total=1,
    )
    write_compile_state(store, root, state, title="compile start")

    clone_dir: Path | None = None
    try:
        # 3. Clone
        vcs = VaultVcs(root)
        clone_dir = Path(tempfile.mkdtemp(prefix=f"knotica-compile-{cleaned}-"))
        clone_vcs = vcs.clone_to(clone_dir)
        clone_store = LocalFSStore(clone_dir)

        def on_trial(trial: int, total: int) -> None:
            write_compile_state(
                store,
                root,
                CompileState(
                    topic=cleaned,
                    stage=CompileStage.optimizing,
                    message="MIPROv2 / bootstrap optimizing",
                    trial=trial,
                    trial_total=total,
                ),
                title=f"compile trial {trial}/{total}",
            )

        # 4–5. Optimize on the clone
        write_compile_state(
            store,
            root,
            CompileState(
                topic=cleaned,
                stage=CompileStage.optimizing,
                message="optimizing query program",
                trial=0,
                trial_total=1,
            ),
            title="compile optimizing",
        )
        artifact = optimize_query(
            clone_store,
            cleaned,
            train,
            golden=golden,
            use_mipro=use_mipro,
            worker_snapshot=worker_snapshot,
            on_trial=on_trial,
            optimize_fn=optimize_fn,
        )

        # 6. Post-eval: compiled must beat baseline
        write_compile_state(
            store,
            root,
            CompileState(
                topic=cleaned,
                stage=CompileStage.evaluating,
                message="comparing compiled vs baseline on golden",
                trial=1,
                trial_total=1,
            ),
            title="compile evaluating",
        )
        baseline_scalar, compiled_scalar = _compare_runners(
            clone_store,
            cleaned,
            golden,
            artifact,
            llm_client=llm_client,
            worker_snapshot=worker_snapshot,
            compare_fn=compare_fn,
        )
        if compiled_scalar <= baseline_scalar:
            write_compile_state(
                store,
                root,
                CompileState(
                    topic=cleaned,
                    stage=CompileStage.failed,
                    message="compiled scalar did not beat baseline",
                    scalar_before=baseline_scalar,
                    scalar_after=compiled_scalar,
                    error="compiled_not_better",
                ),
                title="compile failed post-eval",
            )
            raise KnoticaError(
                ErrorCode.NOT_CONFIGURED,
                (
                    f"compile failed because compiled scalar {compiled_scalar:.4f} "
                    f"did not beat baseline {baseline_scalar:.4f}."
                ),
                fix="Add more diverse curated examples and re-run compile.",
            )

        artifact = CompiledArtifact(
            optimized_instructions=artifact.optimized_instructions,
            demos=artifact.demos,
            metrics={"baseline": baseline_scalar, "compiled": compiled_scalar},
            created_at=artifact.created_at,
            train_n=train_n,
            golden_n=golden_n,
            harness_version=artifact.harness_version,
        )

        # Write artifact on the clone under lock
        art_body, man_body = artifact_write_bodies(artifact)
        with VaultTransaction(
            clone_store, clone_dir, "compile", cleaned, "compiled query artifact"
        ) as txn:
            txn.write(compiled_artifact_path(cleaned), art_body)
            txn.write(compiled_manifest_path(cleaned), man_body)

        short = clone_vcs.head_sha()[:12]
        branch = f"compile/{cleaned}/{short}"
        # 7. Return branch onto the source vault (never merge to main here)
        if vcs.branch_exists(branch):
            vcs.delete_branch(branch, force=True)
        vcs.fetch_ref_from(clone_dir, "HEAD", branch)
        head_sha = vcs.ref_sha(branch)
        base_sha = vcs.ref_sha(vcs.default_branch())

        message = (
            f"Compiled artifact ready on branch {branch}. "
            "Promote with compile_promote (mode=apply) after review — does not merge automatically."
        )
        completed = CompileState(
            topic=cleaned,
            stage=CompileStage.completed,
            branch=branch,
            message=message,
            trial=1,
            trial_total=1,
            scalar_before=baseline_scalar,
            scalar_after=compiled_scalar,
        )
        write_compile_state(store, root, completed, title="compile completed")
        record_compile_finished(
            store,
            root,
            completed,
            branch=branch,
            head_sha=head_sha,
            base_sha=base_sha,
            scalar_before=baseline_scalar,
            scalar_after=compiled_scalar,
        )
        return CompileResult(
            topic=cleaned,
            branch=branch,
            stage=CompileStage.completed.value,
            message=message,
            scalar_before=baseline_scalar,
            scalar_after=compiled_scalar,
            train_n=train_n,
            golden_n=golden_n,
        )
    except KnoticaError:
        raise
    except GitError as error:
        write_compile_state(
            store,
            root,
            CompileState(
                topic=cleaned,
                stage=CompileStage.failed,
                message=str(error),
                error="git_error",
            ),
            title="compile git failed",
        )
        raise KnoticaError(
            ErrorCode.GIT_ERROR,
            f"compile failed because git reported: {error}",
            fix="Run `knotica doctor` and retry compile on a clean vault.",
        ) from error
    except Exception as error:  # noqa: BLE001
        write_compile_state(
            store,
            root,
            CompileState(
                topic=cleaned,
                stage=CompileStage.failed,
                message=str(error),
                error="compile_error",
            ),
            title="compile failed",
        )
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            f"compile failed because {error}",
            fix="Inspect compile-state.json and retry after fixing the underlying error.",
        ) from error
    finally:
        if clone_dir is not None:
            shutil.rmtree(clone_dir, ignore_errors=True)


def _doctor_gate(store: VaultStore, root: Path, *, config_detail: str) -> None:
    rows = run_doctor_checks(store, root, config_detail=config_detail, quick=True)
    if any(row.status == "FAIL" for row in rows):
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            "compile failed because doctor --quick reported a FAIL.",
            fix="Run `knotica doctor` and repair before compiling.",
        )
    vcs = VaultVcs(root)
    if vcs.is_dirty():
        raise KnoticaError(
            ErrorCode.GIT_ERROR,
            "compile failed because the vault worktree is dirty.",
            fix="Commit or `knotica doctor repair` scoped dirty paths, then retry.",
        )


def _compare_runners(
    store: VaultStore,
    topic: str,
    golden: list[Any],
    artifact: CompiledArtifact,
    *,
    llm_client: LLMClient | None,
    worker_snapshot: str,
    compare_fn: CompareFn | None,
) -> tuple[float, float]:
    """Score baseline vs compiled; injectable for tests."""
    if compare_fn is not None:
        # Tests inject compare_fn and often omit an LLM client — build cheap
        # placeholder runners only when a client is available.
        if llm_client is None:
            return compare_fn(store, topic, None, None, list(golden))  # type: ignore[arg-type]
        baseline = MessagesApiRunner(llm_client, worker_snapshot)
        compiled = CompiledRunner(artifact, llm_client, worker_snapshot=worker_snapshot)
        return compare_fn(store, topic, baseline, compiled, list(golden))

    if llm_client is None:
        # Never fabricate a pass: a compile whose post-eval cannot run must fail
        # loudly, not report invented scores.
        raise KnoticaError(
            ErrorCode.NOT_CONFIGURED,
            "compile post-eval needs LLM credentials; refusing to fabricate scores.",
            fix=(
                "Set CLAUDE_CODE_OAUTH_TOKEN (preferred) or ANTHROPIC_API_KEY in the "
                "server/CLI environment, then rerun compile."
            ),
        )

    baseline = MessagesApiRunner(llm_client, worker_snapshot)
    compiled = CompiledRunner(artifact, llm_client, worker_snapshot=worker_snapshot)
    return lexical_pair_score(store, topic, baseline, compiled, golden)


def _require_llm() -> LLMClient:
    from knotica.evals.llm import AnthropicClient

    return AnthropicClient()
