"""``run_eval`` -- the orchestrator where every seam composes, plus the frozen corpus.

This is the module the whole ``evals/`` package builds toward. It owns the run's
top-level narrative and nothing else: clone the vault at a pinned SHA, load and
verify the golden set, drive it through ``dspy.Evaluate``, reject on instrument
failure, enforce the ceilings, compose the scalar, and persist. Each of those verbs
is one sibling module; the sequence -- and the safety guard that keeps the write
target off the live vault -- is here.

The clone helpers stay with the orchestrator because they exist only to establish
the frozen corpus this one function runs against: loops always work on a clone,
never the live vault, and :func:`_guard_not_live_vault` is the backstop that makes
that structural rather than merely intended.

**Import stays cheap.** ``dspy`` is imported lazily inside :func:`run_eval`, so
``import knotica.evals.harness`` (and therefore ``import knotica.evals``) never
forces the eval dependency group onto an unrelated import path such as the MCP
cold start. Every collaborator is injectable: tests pass a ``FakeLLMClient`` and
a fixture source vault for a fully offline, deterministic run.
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Unpack

from knotica.core.config import resolve
from knotica.core.records import MetricsRecord, body_sha256
from knotica.core.vcs import VaultVcs
from knotica.evals import golden
from knotica.evals.cache import ResponseCache
from knotica.evals.config import DEFAULT_CONFIG, HarnessConfig, HarnessOverrides
from knotica.evals.error_capture import OnOutcome
from knotica.evals.harness.accounting import _UsageAccountingClient, _enforce_spend_ceilings
from knotica.evals.harness.artifacts import (
    _build_manifest,
    _build_record,
    _compute_held_out_delta,
    _next_generation,
    _persist,
)
from knotica.evals.harness.errors import EvalRunError, LiveVaultTargetError
from knotica.evals.harness.evaluate import (
    _question_id_map,
    _reject_on_failures,
    _remap_scorer_outcome_by_question,
    _run_evaluate,
    _with_example_progress,
)
from knotica.evals.harness.scoring import _compose_scalar, _per_example_breakdown
from knotica.evals.llm import AnthropicClient, LLMClient
from knotica.evals.program import BaselineProgram
from knotica.evals.runner import MessagesApiRunner
from knotica.evals.scorer import build_metric
from knotica.store import LocalFSStore

_LOGGER = logging.getLogger(__name__)

#: Directory name (under the OS temp dir) for the default cross-invocation judge
#: cache, namespaced per corpus SHA so distinct frozen corpora never share it.
_CACHE_DIRNAME = "knotica-eval-cache"


@dataclass(frozen=True, slots=True)
class EvalRunResult:
    """One ``run_eval`` outcome: the appended record plus the clone it landed on.

    ``run_eval`` commits its metrics line and per-run manifest to a throwaway
    frozen-corpus *clone*, so the record's ``artifact_ref`` is clone-relative. This
    result surfaces ``clone_root`` alongside the record so a caller can (a) resolve
    the manifest at ``clone_root / record.artifact_ref`` and (b) point a human at
    the clone to review the eval commit -- the frozen corpus and this run's manifest
    live only there. The live source vault is untouched.
    """

    record: MetricsRecord
    clone_root: Path


def run_eval(
    topic: str,
    *,
    source_root: str | PurePath | None = None,
    ref: str | None = None,
    llm_client: LLMClient | None = None,
    config: HarnessConfig = DEFAULT_CONFIG,
    cache: ResponseCache | None = None,
    work_root: str | Path | None = None,
    on_example: Callable[[int, int, str], None] | None = None,
    on_substage: Callable[[str, int, int], None] | None = None,
    on_outcome: OnOutcome | None = None,
    instructions_override: str | None = None,
    **overrides: Unpack[HarnessOverrides],
) -> EvalRunResult:
    """Evaluate ``topic`` against its frozen golden set and append one metrics record.

    Clones the source vault at ``ref`` (default HEAD), scores the topic's golden
    devset through ``dspy.Evaluate`` over the baseline runner and scorer,
    composes one stable scalar, and appends a
    :class:`~knotica.core.records.MetricsRecord` to the clone's
    ``<topic>/.knotica/metrics.jsonl`` via a single
    :class:`~knotica.core.transaction.VaultTransaction`. The source vault is left
    byte-identical.

    Args:
        topic: The topic to evaluate; its golden set and ``query.md`` are read
            from the clone.
        source_root: The source vault root to clone. ``None`` config-resolves the
            default vault via :func:`knotica.core.config.resolve` (an absent
            config raises the clean ``NOT_CONFIGURED`` error); a caller that has
            already resolved the vault may pass the path to skip re-resolution.
        ref: Optional commit-ish to pin the corpus to. ``None`` uses the source's
            current ``HEAD``.
        llm_client: The LLM seam for the runner and judge. ``None`` constructs
            the real :class:`~knotica.evals.llm.AnthropicClient` (which raises a
            clean, network-free error if ``ANTHROPIC_API_KEY`` is unset); tests
            inject a ``FakeLLMClient`` for a zero-network run.
        config: The base run config (packaged defaults). ``**overrides`` are
            threaded onto it via :meth:`~knotica.evals.config.HarnessConfig.with_overrides`.
        cache: The judge response cache. ``None`` uses a per-corpus on-disk cache
            so a warm re-run reproduces the scalar bit-for-bit; a shared instance
            can be injected for the same effect in one process.
        work_root: The clone destination -- the harness clones the source *into*
            it (it must not already exist). ``None`` uses a fresh OS temp
            directory (the clone persists for review; the source is untouched).
        on_outcome: Fired once per example, ``(id, status, error_class, detail)``.
            The runner leg fires it on a caught ``program(question=...)``
            exception (classified by
            :func:`~knotica.evals.error_capture.classify_error`, id resolved via
            the question -> id map -- see
            :func:`~knotica.evals.harness.evaluate._question_id_map`); the scorer
            leg (threaded to :func:`~knotica.evals.scorer.build_metric`) fires it
            on success or a judge parse failure, keyed by the golden record's
            stable id. ``None`` disables capture; no extra model call either way.
        instructions_override: Replaces the clone's ``query.md`` body for this
            run only, leaving retrieval, judge, golden set and scalar formula
            untouched -- so the resulting scalar is directly comparable to one
            produced without it. This is what lets the prompt arena score a
            candidate prompt on the same instrument as the gate baseline
            (:mod:`knotica.core.arena_eval`); ``None`` evaluates the vault's own
            prompt, which is every other caller.
        **overrides: CLI-flag-style config overrides (e.g. ``max_total_tokens=1``)
            re-validated onto ``config``.

    Returns:
        An :class:`EvalRunResult` -- the appended
        :class:`~knotica.core.records.MetricsRecord` and the ``clone_root`` it was
        committed to, so a caller can resolve the record's clone-relative
        ``artifact_ref`` and point a reviewer at the eval commit.

    Raises:
        LiveVaultTargetError: If the clone destination is the source vault root.
        GoldenSetMissingError: If the topic has no golden set (the CLI exit code).
        GoldenSetContaminationError: If the golden set overlaps the trainset.
        EvalRunError: If any example failed with an instrument error, or the
            golden set has no examples.
        SpendCeilingExceededError: If the run crossed a token or USD ceiling.
    """
    import dspy  # lazy: keeps ``import knotica.evals`` free of the eval group

    run_config = config.with_overrides(**overrides) if overrides else config
    source = _resolve_source(source_root)
    clone_dest = _clone_destination(work_root)
    _guard_not_live_vault(clone_dest, source)
    clone_vcs = VaultVcs(source).clone_to(clone_dest, ref)
    corpus_sha = clone_vcs.head_sha()
    clone_store = LocalFSStore(clone_vcs.root)
    _LOGGER.info(
        "eval clone for topic %r at corpus git:%s -> %s", topic, corpus_sha, clone_vcs.root
    )

    records = golden.load(clone_store, topic)
    golden.verify_disjoint_from_trainset(clone_store, topic, records)
    if not records:
        raise EvalRunError(topic, "the golden set loaded zero examples")
    dataset_sha256 = body_sha256(clone_store.read_text(golden.golden_dataset_path(topic)))

    run_cache = cache if cache is not None else _default_cache(corpus_sha)
    client = _UsageAccountingClient(llm_client if llm_client is not None else AnthropicClient())
    program = BaselineProgram(
        clone_store,
        topic,
        MessagesApiRunner(
            client,
            run_config.worker_snapshot,
            cache=run_cache,
            instructions_override=instructions_override,
        ),
    )
    question_id_map = _question_id_map(records)
    metric = build_metric(
        client,
        run_config.judge_snapshot,
        clone_store,
        topic,
        cache=run_cache,
        w_qa=run_config.w_qa,
        w_cite=run_config.w_cite,
        threshold=run_config.threshold,
        n_judge_samples=run_config.n_judge_samples,
        on_substage=on_substage,
        on_outcome=(
            None
            if on_outcome is None
            else _remap_scorer_outcome_by_question(on_outcome, records, question_id_map)
        ),
    )

    scored_program = (
        program
        if on_example is None and on_substage is None and on_outcome is None
        else _with_example_progress(
            dspy,
            program,
            len(records),
            on_example,
            on_substage,
            on_outcome,
            question_id_map,
        )
    )
    results = _run_evaluate(dspy, records, scored_program, metric, run_config)
    _reject_on_failures(topic, results)
    _enforce_spend_ceilings(topic, client, run_config)

    breakdown = _per_example_breakdown(client, clone_store, topic, run_cache, run_config, results)
    scalar_value, components, budget = _compose_scalar(clone_store, topic, breakdown, run_config)
    generation = _next_generation(clone_store, topic)
    record = _build_record(
        topic, generation, corpus_sha, scalar_value, components, len(records), run_config
    )
    held_out_delta = _compute_held_out_delta(
        clone_store, topic, generation, record.scalar, breakdown
    )
    manifest = _build_manifest(
        topic,
        generation,
        corpus_sha,
        dataset_sha256,
        record,
        held_out_delta,
        breakdown,
        budget,
        client,
        run_cache,
        run_config,
    )
    _persist(clone_store, clone_vcs.root, topic, generation, record, manifest, budget, run_config)
    # The verdict, on the operator's channel. The clone line above announced the
    # run; without this its *result* reached only the vault and the UI, so a
    # terminal watching a minutes-long eval never learned what it scored.
    _LOGGER.info("eval done topic=%r gen=%d scalar=%.4f", topic, generation, record.scalar)
    return EvalRunResult(record=record, clone_root=clone_vcs.root)


def _resolve_source(source_root: str | PurePath | None) -> Path:
    """Return the source vault root: the explicit argument, or the config default.

    An absent ``source_root`` config-resolves the default vault via
    :func:`knotica.core.config.resolve`, which raises the clean
    ``NOT_CONFIGURED`` error when no vault is configured -- so config resolution
    is optional for a caller that already has the path (the CLI) but automatic
    otherwise.
    """
    if source_root is not None:
        return Path(source_root)
    return resolve().path


def _clone_destination(work_root: str | Path | None) -> Path:
    """The clone destination -- the given ``work_root``, or a fresh temp path.

    ``git clone`` requires the destination not to pre-exist, so a ``None``
    ``work_root`` resolves to a not-yet-created ``clone`` under a fresh temp dir.
    """
    if work_root is not None:
        return Path(work_root)
    return Path(tempfile.mkdtemp(prefix="knotica-eval-")) / "clone"


def _guard_not_live_vault(clone_dest: Path, source_root: Path) -> None:
    """Refuse -- before any clone -- if the write target is the live source vault.

    Fires up front (never as a raw clone-into-existing-dir git failure) so a
    path-confusion bug that aimed the eval at the source instead of a throwaway
    clone can never mutate the live wiki.
    """
    if clone_dest.resolve() == source_root.resolve():
        raise LiveVaultTargetError(str(source_root))


def _default_cache(corpus_sha: str) -> ResponseCache:
    """A judge cache backed on disk under a per-corpus temp directory.

    Namespacing the backing directory by ``corpus_sha`` keeps two frozen corpora
    from sharing cached judge medians and lets a warm re-run of the same corpus
    reuse them (so the scalar reproduces bit-for-bit) without ever writing the
    source vault or the disposable clone.
    """
    storage_root = Path(tempfile.gettempdir()) / _CACHE_DIRNAME / corpus_sha
    return ResponseCache(storage_root=storage_root)
