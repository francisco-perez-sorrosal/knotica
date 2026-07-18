"""Query program optimize path — MIPROv2 (or bootstrap) over vault ``query.md``.

Lazy ``dspy`` import so default installs without the ``evals`` group still load
the package. The student keeps deterministic retrieve + synthesize (same contract
as :class:`~knotica.evals.runner.MessagesApiRunner`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from knotica.core.compiled import CompiledArtifact, CompiledDemo
from knotica.core.prompts import resolve_prompt
from knotica.core.records import QARecord
from knotica.evals.config import WORKER_SNAPSHOT
from knotica.store import VaultStore

__all__ = ["bootstrap_query_artifact", "optimize_query"]

_QUERY_OPERATION = "query"
_MAX_DEMOS = 8

OptimizeFn = Callable[..., CompiledArtifact]


def bootstrap_query_artifact(
    store: VaultStore,
    topic: str,
    train: Sequence[QARecord],
    *,
    golden_n: int = 0,
    metrics: dict[str, float] | None = None,
    fallback_reason: str = "",
) -> CompiledArtifact:
    """Build a compiled artifact from ``query.md`` + few-shot demos (no MIPRO).

    The instructions are the topic's resolved prompt verbatim — the vault is the
    single source of truth for prompt content; the artifact's added value is the
    demos it earned from the trainset, never injected editorial guidance.
    """
    prompt = resolve_prompt(store, _QUERY_OPERATION, topic)
    demos = _demos_from_train(train)
    return CompiledArtifact(
        optimized_instructions=prompt.body.rstrip() + "\n",
        demos=demos,
        metrics=dict(metrics or {}),
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        train_n=len(train),
        golden_n=golden_n,
        harness_version="compile-v1",
        optimizer="bootstrap",
        fallback_reason=fallback_reason,
    )


def optimize_query(
    store: VaultStore,
    topic: str,
    train: Sequence[QARecord],
    *,
    golden: Sequence[QARecord] | None = None,
    use_mipro: bool = True,
    worker_snapshot: str = WORKER_SNAPSHOT,
    on_trial: Callable[[int, int], None] | None = None,
    optimize_fn: OptimizeFn | None = None,
) -> CompiledArtifact:
    """Optimize the query instruction substrate; return a JSON-serializable artifact.

    ``optimize_fn`` is the test seam. When absent, tries MIPROv2 (``auto="light"``)
    and falls back to :func:`bootstrap_query_artifact` if DSPy/MIPRO is unavailable.
    """
    golden_n = len(golden or ())
    if optimize_fn is not None:
        return optimize_fn(
            store,
            topic,
            train,
            golden=golden,
            worker_snapshot=worker_snapshot,
        )

    if on_trial is not None:
        on_trial(0, 1)

    fallback_reason = ""
    if use_mipro:
        try:
            artifact = _mipro_optimize(
                store,
                topic,
                train,
                golden=golden or (),
                worker_snapshot=worker_snapshot,
                on_trial=on_trial,
            )
            if on_trial is not None:
                on_trial(1, 1)
            return replace(artifact, optimizer="mipro")
        except Exception as exc:  # noqa: BLE001 — bootstrap remains a valid Phase 3a path
            # The fallback is legitimate, but never silent: the artifact records
            # which optimizer actually ran and why MIPRO did not.
            fallback_reason = f"{type(exc).__name__}: {exc}"

    artifact = bootstrap_query_artifact(
        store,
        topic,
        train,
        golden_n=golden_n,
        metrics={"baseline": 0.0, "compiled": 0.0},
        fallback_reason=fallback_reason,
    )
    if on_trial is not None:
        on_trial(1, 1)
    return artifact


def _demos_from_train(train: Sequence[QARecord]) -> tuple[CompiledDemo, ...]:
    """Select few-shot demos, preferring human curation over cold-start seeds.

    Machine-seeded records (``source: seed_train``) exist to bridge an empty
    flywheel; the ratchet is here — once real curated examples accumulate they
    fill the demo slots first, so cold-start scaffolding ages out of compiled
    artifacts without any migration.
    """
    preferred = sorted(train, key=lambda record: record.source == "seed_train")
    demos: list[CompiledDemo] = []
    for record in preferred:
        if record.verdict not in {"good", "corrected"}:
            continue
        answer = record.corrected_answer or record.answer
        demos.append(
            CompiledDemo(
                question=record.query,
                answer=answer,
                citations=tuple(record.citations),
            )
        )
        if len(demos) >= _MAX_DEMOS:
            break
    return tuple(demos)


def _mipro_optimize(
    store: VaultStore,
    topic: str,
    train: Sequence[QARecord],
    *,
    golden: Sequence[QARecord],
    worker_snapshot: str,
    on_trial: Callable[[int, int], None] | None,
) -> CompiledArtifact:
    """Run MIPROv2 over a thin instruction-bearing student; extract instructions+demos."""
    from dspy.teleprompt import MIPROv2

    from knotica.evals.program import BaselineProgram
    from knotica.evals.runner import MessagesApiRunner
    from knotica.evals.llm import AnthropicClient

    if on_trial is not None:
        on_trial(0, 3)

    base = resolve_prompt(store, _QUERY_OPERATION, topic).body
    # Student uses baseline runner; MIPRO proposes instruction variants via the
    # teleprompter's instruction proposal LM. We seed with demos and keep the
    # resulting few-shots + an appendix on the vault prompt body.
    llm = AnthropicClient()
    runner = MessagesApiRunner(llm, worker_snapshot)
    program = BaselineProgram(store, topic, runner)

    trainset = [_to_example(record) for record in train[:20]]
    valset = [_to_example(record) for record in (golden[:10] or train[:5])]

    def metric(gold: Any, pred: Any, _trace: Any = None) -> float:
        gold_answer = getattr(gold, "answer", "") or ""
        pred_answer = getattr(pred, "answer", "") or ""
        if not gold_answer or not pred_answer:
            return 0.0
        gold_l = gold_answer.lower()
        pred_l = pred_answer.lower()
        # Cheap lexical overlap — full judge is the post-compile eval gate.
        hits = sum(1 for token in gold_l.split() if len(token) > 3 and token in pred_l)
        return min(1.0, hits / max(1, len(gold_l.split()) // 4))

    teleprompter = MIPROv2(metric=metric, auto="light", num_threads=1)
    if on_trial is not None:
        on_trial(1, 3)
    optimized = teleprompter.compile(program, trainset=trainset, valset=valset)
    if on_trial is not None:
        on_trial(2, 3)

    demos = _demos_from_train(train)
    # Use the instruction string MIPRO actually proposed; without one, the vault
    # prompt stands unmodified — improvements are earned, never injected.
    optimized_instructions = base.rstrip() + "\n"
    for attr in ("optimized_instructions", "instructions", "rationale"):
        value = getattr(optimized, attr, None)
        if isinstance(value, str) and value.strip():
            optimized_instructions = value.strip() + "\n"
            break

    return CompiledArtifact(
        optimized_instructions=optimized_instructions,
        demos=demos,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        train_n=len(train),
        golden_n=len(golden),
        harness_version="compile-v1",
    )


def _to_example(record: QARecord) -> Any:
    import dspy

    answer = record.corrected_answer or record.answer
    return dspy.Example(question=record.query, answer=answer).with_inputs("question")
