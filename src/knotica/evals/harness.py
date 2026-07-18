"""The eval-harness orchestrator -- ``run_eval``, where every seam composes.

This is the module the whole ``evals/`` package builds toward: it clones the
vault at a pinned SHA, drives the golden devset through ``dspy.Evaluate`` over
the baseline runner and the triple-consumer scorer, folds per-example quality
with lint-cleanliness and a token-cost discount into one stable scalar, and
appends a :class:`~knotica.core.records.MetricsRecord` to the clone's
``<topic>/.knotica/metrics.jsonl`` through the single mutation path. The source
vault is left byte-identical -- loops always work on a clone, never the live
vault.

The data flow, in one pass:

1. **Clone the source** at HEAD (or an explicit ``ref``) into a throwaway tree;
   record ``corpus_ref = "git:<clone-sha>"``. A cheap safety guard refuses to
   run if the clone's real path resolves to the source vault's -- an eval must
   never write the live wiki.
2. **Load the golden set** from the clone; an absent set raises
   :class:`~knotica.evals.golden.GoldenSetMissingError` (the CLI's dedicated
   exit code) and a set that overlaps the flywheel trainset raises
   :class:`~knotica.evals.golden.GoldenSetContaminationError` -- a contaminated
   held-out set is never scored silently.
3. **Score the devset** with ``dspy.Evaluate(devset, metric, num_threads=1)``
   over a :func:`~knotica.evals.program.BaselineProgram` wrapping
   :class:`~knotica.evals.runner.MessagesApiRunner`. Every per-example
   ``(gold, prediction, quality)`` is read back from ``EvaluationResult.results``
   (the topic scalar is recomputed from ``.results``; ``.score`` is ignored).
   An instrument failure (a malformed runner response or an unparseable judge
   score) surfaces in ``.results`` as an empty prediction; the harness detects
   any such failure and aborts loudly rather than diluting the scalar with a
   silent ``0.0``.
4. **Account every billed token; a cache hit bills nothing.** The injected LLM
   client is wrapped in a proxy that accumulates exact per-call usage across the
   runner *and* the judge, so a per-run token or USD ceiling can hard-abort a
   runaway before its record is committed. Both the runner's synthesis cache and
   the judge's score cache sit *above* this proxy: a warm-cache hit never reaches
   ``complete``, so it contributes zero to the billed total (the ceiling and
   ``cost_usd``) while its replayed usage still feeds the scalar's per-item token
   measure ``T``. This is the accounting split that lets a warm re-run reproduce
   ``T`` bit-for-bit yet pass a ceiling a cold run breached. Each cache's hit-rate
   is recorded per consumer, so a silent cache failure (unstable keys -> 100% miss
   -> surprise spend) is visible.
5. **Compose the scalar** from the mean per-example quality, the topic's lint
   violation count, and the per-item median total tokens ``T`` against a
   budget ``T_target`` (``tau * median(T)`` frozen at generation 0 in the
   topic's ``eval.toml``, read back unchanged on later generations).
6. **Persist** the record, a per-run reproducibility manifest (the
   ``artifact_ref`` target), and -- on generation 0 -- the frozen ``eval.toml``,
   all through one :class:`~knotica.core.transaction.VaultTransaction` (one
   commit, one ``log.md`` entry). Nothing secret is ever written; the
   transaction's secret scrub is the belt to the harness's braces.

**Import stays cheap.** ``dspy`` is imported lazily inside :func:`run_eval`, so
``import knotica.evals.harness`` (and therefore ``import knotica.evals``) never
forces the eval dependency group onto an unrelated import path such as the MCP
cold start. Every collaborator is injectable: tests pass a ``FakeLLMClient`` and
a fixture source vault for a fully offline, deterministic run.
"""

import json
import logging
import statistics
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from knotica.core.config import resolve
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.lint import lint_vault
from knotica.core.links import iter_page_paths
from knotica.core.records import (
    MetricsComponents,
    MetricsRecord,
    body_sha256,
)
from knotica.core.transaction import VaultTransaction
from knotica.core.vcs import VaultVcs
from knotica.evals import citations, golden, judge, scalar
from knotica.evals.cache import ResponseCache
from knotica.evals.config import (
    DEFAULT_CONFIG,
    JUDGE_SNAPSHOT,
    WORKER_SNAPSHOT,
    HarnessConfig,
    harness_version,
)
from knotica.evals.llm import AnthropicClient, Completion, LLMClient, Message
from knotica.evals.program import BaselineProgram
from knotica.evals.runner import RUNNER_CACHE_NAMESPACE, MessagesApiRunner
from knotica.evals.scorer import build_metric
from knotica.store import LocalFSStore, VaultStore

__all__ = [
    "EvalHarnessError",
    "EvalRunError",
    "EvalRunResult",
    "LiveVaultTargetError",
    "SpendCeilingExceededError",
    "run_eval",
]

_LOGGER = logging.getLogger(__name__)

#: The topic's hidden per-topic state directory (mirrors ``core.operations.create_topic``).
_KNOTICA_DIR = ".knotica"
#: The topic's eval-history file -- one appended line per generation.
_METRICS_FILENAME = "metrics.jsonl"
#: The frozen per-topic token budget target, written once at generation 0.
_EVAL_TOML_FILENAME = "eval.toml"
#: The per-run reproducibility manifests directory (``<topic>/.knotica/eval-runs/gen-<N>/``).
_EVAL_RUNS_DIRNAME = "eval-runs"
#: The per-run manifest filename inside a generation directory.
_MANIFEST_FILENAME = "manifest.json"
#: The self-versioning stamp on the per-run manifest (a v2 reader can probe for
#: ``per_example[].id``/``.pages`` and the ``held_out_delta`` object shape). The
#: manifest versions independently of the dec-006-frozen ``metrics.jsonl`` record;
#: today's unversioned shape is treated as an implicit v1.
_MANIFEST_SCHEMA_VERSION = 2
#: The schema-overlay filename excluded when counting a topic's content pages
#: (mirrors ``core.lint``'s content-page rule; kept a private constant per the
#: codebase convention of not importing a sibling module's private symbol).
_SCHEMA_OVERLAY_FILENAME = "SCHEMA.md"
#: Directory name (under the OS temp dir) for the default cross-invocation judge
#: cache, namespaced per corpus SHA so distinct frozen corpora never share it.
_CACHE_DIRNAME = "knotica-eval-cache"

#: Packaged per-model prices in USD per million tokens as ``(input, output)``,
#: keyed on the pinned snapshot ids. Used only to enforce the USD spend ceiling
#: and record ``cost_usd`` in the manifest. Pricing arguably belongs beside the
#: ceilings in ``evals.config``; it lives here because ``config`` ships no price
#: table and is not this step's to edit. An overridden snapshot absent from this
#: map contributes ``0`` USD -- the exact token ceiling remains the hard guard.
_MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    JUDGE_SNAPSHOT: (5.0, 25.0),
    WORKER_SNAPSHOT: (3.0, 15.0),
}


class EvalHarnessError(KnoticaError):
    """The eval harness refused to run or could not produce a trustworthy scalar.

    Carries the house error envelope (``NOT_CONFIGURED`` -- the eval cannot
    proceed for this topic in this state), so an adapter renders a clean,
    actionable message rather than a stack trace. The concrete subclass names
    the specific refusal; the code is shared, discriminated by type -- the same
    convention the golden-set errors follow.
    """


class LiveVaultTargetError(EvalHarnessError):
    """The eval's write target resolved to the live source vault, not a clone.

    A safety backstop: the evaluator must only ever write a throwaway clone, so
    if the clone root and the source root resolve to the same real path the run
    is refused before any write.
    """

    def __init__(self, source_root: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            (
                "The eval write target resolved to the live source vault "
                f"({source_root}) instead of a throwaway clone, so the run was "
                "refused to protect the live wiki."
            ),
            fix=(
                "This is an internal safety guard that should never trip; if it "
                "does, the clone step failed -- re-run, and report it if it persists."
            ),
        )


class SpendCeilingExceededError(EvalHarnessError):
    """A per-run token or USD spend ceiling was crossed; the record is not committed."""

    def __init__(self, topic: str, reason: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            f"The eval run for topic '{topic}' exceeded its per-run spend ceiling: {reason}.",
            fix=(
                "Raise the ceiling (`--max-total-tokens` / `--max-usd`) if the run is "
                "legitimately large, or investigate a cache-keying regression (a "
                "warm-cache re-run should show a high judge cache hit-rate)."
            ),
        )
        self.topic = topic


class EvalRunError(EvalHarnessError):
    """The run completed but its scalar cannot be trusted (failures or no examples).

    Instrument failures -- a malformed runner response or an unparseable judge
    score -- surface as failure-scored examples; a scalar averaged over silently
    zeroed instrument failures is not trustworthy, so the harness aborts rather
    than emit a misleading record. Also raised when the golden set loaded zero
    examples.
    """

    def __init__(self, topic: str, reason: str) -> None:
        super().__init__(
            ErrorCode.NOT_CONFIGURED,
            f"The eval run for topic '{topic}' cannot produce a trustworthy scalar: {reason}.",
            fix=(
                "Re-run; if instrument failures persist, inspect the worker/judge "
                "model snapshot or the topic's `query.md` prompt -- a persistent "
                "malformed response or unparseable judge score is a broken instrument."
            ),
        )
        self.topic = topic


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


class _UsageAccountingClient:
    """An :class:`~knotica.evals.llm.LLMClient` proxy that totals exact token usage.

    Wraps the injected client and delegates :meth:`complete` unchanged, while
    accumulating each response's exact input/output tokens per model snapshot.
    The harness passes one proxy instance to *both* the baseline runner and the
    judge, so every billed call -- worker synthesis and judge sampling -- is
    accounted through a single accumulator. Both consumers cache *above* this
    proxy, so a warm runner- or judge-cache hit makes no ``complete`` call and is
    correctly not counted -- the replayed usage still feeds the scalar's ``T``, but
    the billed total (and thus the ceiling and ``cost_usd``) sees only fresh calls.
    That total drives the per-run spend ceilings and the manifest's ``cost_usd``;
    the proxy never sees or stores the API key (it holds no key of its own).
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        # snapshot -> [input_tokens, output_tokens], accumulated across calls.
        # Lock-guarded: a multi-threaded dspy.Evaluate accounts through one proxy.
        self._by_snapshot: dict[str, list[int]] = {}
        self._usage_lock = threading.Lock()

    @property
    def auth_mode(self) -> str | None:
        """The wrapped client's resolved auth mode (``"oauth"`` / ``"api_key"``), or ``None``.

        Delegates to the real :class:`~knotica.evals.llm.AnthropicClient`, which
        records the mode (never the credential) for the run manifest. An injected
        fake exposes no auth mode, so the mode is ``None`` on a zero-network test
        run -- honest: no real credential was resolved.
        """
        return getattr(self._inner, "auth_mode", None)

    def complete(
        self,
        *,
        snapshot: str,
        system: str,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int,
        json_schema: dict[str, object] | None = None,
    ) -> Completion:
        """Delegate the call and accumulate its exact per-snapshot token usage.

        ``json_schema`` is forwarded verbatim so the proxy stays transparent to the
        structured-outputs contract: the baseline runner passes its answer/citations
        schema, the judge passes none, and either way the wrapped client's request
        shape is unchanged by the proxy.
        """
        completion = self._inner.complete(
            snapshot=snapshot,
            system=system,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=json_schema,
        )
        with self._usage_lock:
            totals = self._by_snapshot.setdefault(snapshot, [0, 0])
            totals[0] += completion.usage.input_tokens
            totals[1] += completion.usage.output_tokens
        return completion

    @property
    def total_tokens(self) -> int:
        """Total input+output tokens across every accounted call."""
        return sum(inp + out for inp, out in self._by_snapshot.values())

    def cost_usd(self, pricing: Mapping[str, tuple[float, float]]) -> float:
        """Total spend in USD from the accumulated per-snapshot usage and ``pricing``.

        An unpriced snapshot (an override absent from ``pricing``) contributes
        ``0`` -- the exact token total remains the hard ceiling regardless.
        """
        total = 0.0
        for snapshot, (inp, out) in self._by_snapshot.items():
            rates = pricing.get(snapshot)
            if rates is None:
                continue
            input_rate, output_rate = rates
            total += (inp / 1_000_000) * input_rate + (out / 1_000_000) * output_rate
        return total

    def usage_summary(self) -> dict[str, dict[str, int]]:
        """Per-snapshot ``{input_tokens, output_tokens}`` for the manifest."""
        return {
            snapshot: {"input_tokens": inp, "output_tokens": out}
            for snapshot, (inp, out) in self._by_snapshot.items()
        }


@dataclass(frozen=True, slots=True)
class _ExampleBreakdown:
    """One golden example's scored components, re-derived for the record + manifest.

    ``id`` is the golden ``QARecord.id`` -- the edit-stable join key a later
    generation keys its per-question comparison on (rather than fragile question
    text). ``pages`` is the runner's ordered retrieval trace (rank = index), in
    ``QARecord.pages_used`` form, so a consumer can attribute a regression to a
    retrieval change.
    """

    id: str
    pages: tuple[str, ...]
    question: str
    qa_accuracy: float
    citation_validity: float
    quality: float
    total_tokens: int


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
    **overrides: object,
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
        clone_store, topic, MessagesApiRunner(client, run_config.worker_snapshot, cache=run_cache)
    )
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
    )

    scored_program = (
        program
        if on_example is None and on_substage is None
        else _with_example_progress(dspy, program, len(records), on_example, on_substage)
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
    return EvalRunResult(record=record, clone_root=clone_vcs.root)


# --------------------------------------------------------------------------- #
# Frozen corpus
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# dspy.Evaluate over the devset
# --------------------------------------------------------------------------- #


def _with_example_progress(
    dspy: object,
    program: object,
    total: int,
    on_example: Callable[[int, int, str], None] | None,
    on_substage: Callable[[str, int, int], None] | None = None,
) -> object:
    """Wrap ``program`` so each forward reports ``(i, total, question)`` first.

    Counting is safe because the harness pins ``num_threads=1`` (determinism);
    the callbacks fire *before* the example runs so a watcher shows the
    question currently in flight, not the one just finished. ``on_substage``
    additionally marks the "answering" leg (the metric marks "judging").
    """

    class _ProgressProgram(dspy.Module):  # type: ignore[attr-defined,misc]
        def __init__(self) -> None:
            super().__init__()
            self._count = 0
            # dspy.Evaluate shares this one instance across worker threads;
            # under num_threads > 1 the count reports examples *started*.
            self._count_lock = threading.Lock()

        def forward(self, question: str) -> object:
            with self._count_lock:
                self._count += 1
                started = self._count
            try:
                if on_example is not None:
                    on_example(started, total, question)
                if on_substage is not None:
                    on_substage("answering", 0, 0)
            except Exception:  # noqa: BLE001 — progress must never break the run
                _LOGGER.debug("progress callback failed", exc_info=True)
            return program(question=question)

    return _ProgressProgram()


def _run_evaluate(
    dspy: object,
    records: Sequence[object],
    program: object,
    metric: object,
    config: HarnessConfig,
) -> list[tuple[object, object, float]]:
    """Score the golden devset with ``dspy.Evaluate`` and return its ``.results``.

    Builds the devset (lazy ``dspy.Example`` conversion), runs the program over
    it with the bound metric, and returns the per-example
    ``(gold, prediction, quality)`` triples. ``max_errors`` is set past the
    devset size so a per-example failure never aborts the pass early -- the
    harness collects every result and decides on failures itself. The topic
    scalar is recomputed from these triples; ``EvaluationResult.score`` is ignored.

    ``failure_score`` is the configured Evaluate failure policy -- the same value
    the fingerprint (``runner_config_hash``) and the manifest record -- so the
    instrument the record describes is the one actually applied. It is inert on a
    clean pass (:func:`_reject_on_failures` aborts on any failure), but a caller
    that raised it must see it reach ``dspy.Evaluate``, not dspy's own default.
    """
    devset = [golden.to_example(record) for record in records]
    evaluator = dspy.Evaluate(  # type: ignore[attr-defined]
        devset=devset,
        metric=metric,
        num_threads=config.num_threads,
        display_progress=False,
        max_errors=len(devset) + 1,
        failure_score=config.failure_score,
    )
    return list(evaluator(program).results)


def _reject_on_failures(topic: str, results: Sequence[tuple[object, object, float]]) -> None:
    """Abort loudly if any example failed with an instrument error.

    ``dspy.Evaluate`` catches a per-example exception (a malformed runner
    response or an unparseable judge score) and records a failure-scored triple
    whose prediction is empty. Such a failure is an instrument failure, not a
    legitimate ``0.0`` quality, so a scalar averaged over silently zeroed
    examples is not trustworthy -- the run is refused rather than emit one.
    """
    failed = [gold for gold, prediction, _quality in results if _is_failed_prediction(prediction)]
    if not failed:
        return
    raise EvalRunError(
        topic,
        (
            f"{len(failed)} of {len(results)} golden examples produced no scored "
            "prediction (a malformed baseline response or an unparseable judge "
            "score, surfaced by dspy as a failure score)"
        ),
    )


def _is_failed_prediction(prediction: object) -> bool:
    """Whether a ``.results`` prediction is the empty failure sentinel.

    A successful :func:`~knotica.evals.program.BaselineProgram` prediction always
    carries a ``usage``; ``dspy.Evaluate``'s failure sentinel is an empty
    ``dspy.Prediction`` with no fields, so an absent ``usage`` marks the failure.
    """
    return getattr(prediction, "usage", None) is None


# --------------------------------------------------------------------------- #
# Spend ceilings (post-run hard-abort before any record is committed)
# --------------------------------------------------------------------------- #


def _enforce_spend_ceilings(
    topic: str, client: _UsageAccountingClient, config: HarnessConfig
) -> None:
    """Hard-abort if the run's total token or USD spend crossed its ceiling.

    ``dspy.Evaluate`` runs the whole devset in one batch, so this is a post-run
    check: it cannot un-spend, but it refuses to commit a record for an
    over-budget run and surfaces the overage instead of a silent surprise bill.
    """
    total_tokens = client.total_tokens
    if total_tokens > config.max_total_tokens:
        raise SpendCeilingExceededError(
            topic,
            f"{total_tokens} tokens exceeds the {config.max_total_tokens}-token ceiling",
        )
    cost = client.cost_usd(_MODEL_PRICING_USD_PER_MTOK)
    if cost > config.max_usd:
        raise SpendCeilingExceededError(
            topic, f"${cost:.2f} exceeds the ${config.max_usd:.2f} ceiling"
        )


# --------------------------------------------------------------------------- #
# Per-example breakdown + scalar composition
# --------------------------------------------------------------------------- #


def _per_example_breakdown(
    client: _UsageAccountingClient,
    store: VaultStore,
    topic: str,
    run_cache: ResponseCache,
    config: HarnessConfig,
    results: Sequence[tuple[object, object, float]],
) -> list[_ExampleBreakdown]:
    """Re-derive each example's QA and citation components for the record + manifest.

    ``EvaluationResult.results`` carries only the composed quality per example,
    so the two legs are recovered here from the same ``(gold, prediction)``: the
    judge grade from the warm cache (a hit -- zero LLM calls, zero tokens) and
    deterministic citation validity. The recovered legs are faithful to what the
    scalar used by construction (identical deterministic functions + identical
    cached judge medians).
    """
    breakdown: list[_ExampleBreakdown] = []
    for gold, prediction, quality in results:
        qa_accuracy = judge.grade(
            client,
            config.judge_snapshot,
            gold.question,
            prediction.answer,
            gold.reference_answer,
            n=config.n_judge_samples,
            cache=run_cache,
        )
        breakdown.append(
            _ExampleBreakdown(
                id=gold.id,
                pages=tuple(prediction.pages),
                question=gold.question,
                qa_accuracy=qa_accuracy,
                citation_validity=_citation_validity(store, topic, gold, prediction),
                quality=float(quality),
                total_tokens=prediction.usage.total_tokens,
            )
        )
    return breakdown


def _citation_validity(store: VaultStore, topic: str, gold: object, prediction: object) -> float:
    """Deterministic citation validity with the scorer's reference-aware guard.

    Mirrors the guard the scorer applies (kept here rather than importing the
    scorer's private helper, per the codebase's no-cross-module-private-import
    convention): when the golden reference itself carries citations and the
    candidate cites nothing, the leg is ``0.0`` rather than the vacuous ``1.0``
    -- closing the citation-dropping reward-hacking vector. Otherwise it
    delegates to :func:`knotica.evals.citations.integrity`. This feeds only the
    record's ``citation_validity`` component; the scalar itself uses the quality
    already composed by the scorer in ``.results``.
    """
    if gold.citations and not prediction.citations:
        return 0.0
    return citations.integrity(store, topic, prediction)


def _compose_scalar(
    store: VaultStore, topic: str, breakdown: Sequence[_ExampleBreakdown], config: HarnessConfig
) -> tuple[float, MetricsComponents, "_Budget"]:
    """Compose the topic scalar and its four record components.

    Returns the scalar, the :class:`~knotica.core.records.MetricsComponents`
    breakdown, and the resolved :class:`_Budget` (``T`` / ``T_target`` / whether
    it was freshly frozen) so the caller can persist the frozen budget on
    generation 0.
    """
    quality_answers = statistics.mean(item.quality for item in breakdown)
    lint_violations = len(lint_vault(store, topic))
    n_content_pages = _count_content_pages(store, topic)
    per_item_tokens = statistics.median(item.total_tokens for item in breakdown)
    budget = _resolve_budget(store, topic, per_item_tokens, config)
    scalar_value = scalar.compose(
        quality_answers,
        lint_violations,
        budget.T,
        budget.T_target,
        n_content_pages=n_content_pages,
        w_lint=config.w_lint,
        lam=config.lam,
    )
    components = MetricsComponents(
        qa_accuracy=statistics.mean(item.qa_accuracy for item in breakdown),
        citation_validity=statistics.mean(item.citation_validity for item in breakdown),
        lint_violations=float(lint_violations),
        token_cost=_cost_factor(budget.T, budget.T_target, config.lam),
    )
    return scalar_value, components, budget


def _count_content_pages(store: VaultStore, topic: str) -> int:
    """Count a topic's content pages (every ``.md`` page except its schema overlay).

    Mirrors ``core.lint``'s content-page rule so the scalar's lint-cleanliness
    reference ``L_ref = max(1, n_content_pages)`` normalizes violations by the
    same page set the linter counted.
    """
    overlay = f"{topic}/{_SCHEMA_OVERLAY_FILENAME}"
    return sum(1 for path in iter_page_paths(store, topic) if path != overlay)


def _cost_factor(per_item_tokens: float, target: float, lam: float) -> float:
    """The applied hinged token-cost discount multiplier in ``[0, 1]``.

    Mirrors the discount inside :func:`knotica.evals.scalar.compose` (kept local
    rather than importing that module's private helper) so the record's
    ``token_cost`` component reflects the exact multiplier the scalar applied:
    ``1.0`` at or under budget, shrinking linearly with the over-budget hinge.
    """
    if target <= 0:
        return 1.0
    overage = max(0.0, (per_item_tokens - target) / target)
    return max(0.0, min(1.0, 1.0 - lam * overage))


# --------------------------------------------------------------------------- #
# Budget (T / T_target), frozen at generation 0 in the topic's eval.toml
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Budget:
    """The token-cost budget for one run: measured ``T`` vs frozen ``T_target``."""

    T: float
    T_target: float
    newly_frozen: bool


def _resolve_budget(
    store: VaultStore, topic: str, per_item_tokens: float, config: HarnessConfig
) -> _Budget:
    """Read the topic's frozen ``T_target``, or compute and mark it for freezing.

    On generation 0 the topic has no ``eval.toml``: ``T_target = tau * T`` is
    computed for this run and flagged ``newly_frozen`` so the caller persists it.
    On later generations the frozen value is read back unchanged, so the budget
    stays fixed across the topic's history.
    """
    eval_toml_path = _eval_toml_path(topic)
    if store.exists(eval_toml_path):
        return _Budget(
            T=per_item_tokens,
            T_target=_read_t_target(store.read_text(eval_toml_path)),
            newly_frozen=False,
        )
    return _Budget(T=per_item_tokens, T_target=config.tau * per_item_tokens, newly_frozen=True)


def _read_t_target(text: str) -> float:
    """Parse the frozen ``t_target`` from a topic's ``eval.toml``."""
    import tomllib

    return float(tomllib.loads(text)["t_target"])


def _format_eval_toml(budget: _Budget, config: HarnessConfig) -> str:
    """Render the topic's ``eval.toml`` recording the frozen budget and its provenance."""
    return (
        "# knotica eval budget target for this topic.\n"
        "# Frozen when the topic is first evaluated and read back unchanged on every\n"
        "# later generation, so the token-cost discount uses a fixed reference.\n"
        f"t_target = {budget.T_target}\n"
        f"tau = {config.tau}\n"
        f"scalar_formula_version = {config.scalar_formula_version}\n"
    )


# --------------------------------------------------------------------------- #
# Record + manifest assembly
# --------------------------------------------------------------------------- #


def _next_generation(store: VaultStore, topic: str) -> int:
    """The 1-based generation number for this run (one past the highest recorded).

    A topic with no ``metrics.jsonl`` history yields generation ``1``; each later
    run is one past the highest recorded generation, so the history reads
    ``1, 2, 3, ...``.
    """
    metrics_path = _metrics_path(topic)
    if not store.exists(metrics_path):
        return 1
    existing = _existing_generations(store.read_text(metrics_path))
    return max(existing) + 1 if existing else 1


def _existing_generations(text: str) -> list[int]:
    """The generation numbers of every record already in a ``metrics.jsonl`` body."""
    return [
        MetricsRecord.from_json_line(line).generation for line in text.splitlines() if line.strip()
    ]


def _build_record(
    topic: str,
    generation: int,
    corpus_sha: str,
    scalar_value: float,
    components: MetricsComponents,
    n_examples: int,
    config: HarnessConfig,
) -> MetricsRecord:
    """Assemble the frozen ``MetricsRecord`` line for this run."""
    return MetricsRecord(
        topic=topic,
        timestamp=datetime.now(UTC).isoformat(),
        generation=generation,
        harness_version=harness_version(judge.JUDGE_PROMPT_HASH, config),
        scalar=scalar_value,
        components=components,
        n_examples=n_examples,
        corpus_ref=f"git:{corpus_sha}",
        artifact_ref=_manifest_path(topic, generation),
    )


def _build_manifest(
    topic: str,
    generation: int,
    corpus_sha: str,
    dataset_sha256: str,
    record: MetricsRecord,
    held_out_delta: dict[str, object] | None,
    breakdown: Sequence[_ExampleBreakdown],
    budget: _Budget,
    client: _UsageAccountingClient,
    run_cache: ResponseCache,
    config: HarnessConfig,
) -> str:
    """Render the per-run reproducibility manifest (the ``artifact_ref`` target).

    Captures the reproducibility columns the frozen record cannot hold -- the
    dataset digest, weights/lambda/tau, ``T``/``T_target``, exact token usage,
    ``cost_usd``, the resolved ``auth_mode`` (``"oauth"``/``"api_key"``, so a
    reader knows whether ``cost_usd`` is a real bill or notional), the runner and
    judge cache hit-rates (recorded per consumer off the one shared cache), per-example
    scores, the ``dspy`` version + Evaluate config -- with no secret material (and
    the transaction's scrub is the safety net; the auth mode is not secret, the
    credential never enters the manifest).
    """
    judge_cache = run_cache.stats_for(judge.JUDGE_CACHE_NAMESPACE)
    runner_cache = run_cache.stats_for(RUNNER_CACHE_NAMESPACE)
    payload: dict[str, object] = {
        "manifest_schema_version": _MANIFEST_SCHEMA_VERSION,
        "topic": topic,
        "generation": generation,
        "corpus_ref": f"git:{corpus_sha}",
        "harness_version": record.harness_version,
        "scalar": record.scalar,
        "scalar_formula_version": config.scalar_formula_version,
        "deterministic": True,
        "dataset_sha256": dataset_sha256,
        "n_examples": record.n_examples,
        "weights": {"w_qa": config.w_qa, "w_cite": config.w_cite, "w_lint": config.w_lint},
        "lambda": config.lam,
        "tau": config.tau,
        "T": budget.T,
        "T_target": budget.T_target,
        "cost_factor": record.components.token_cost,
        "auth_mode": client.auth_mode,
        "token_usage": {"total": client.total_tokens, "by_snapshot": client.usage_summary()},
        # ``cost_usd`` is pricing-table-derived. In OAuth (subscription) mode
        # (``auth_mode == "oauth"``) there is no per-call USD bill, so this figure
        # is *notional* -- the token ceiling remains the hard, mode-independent guard.
        "cost_usd": client.cost_usd(_MODEL_PRICING_USD_PER_MTOK),
        "judge": {
            "snapshot": config.judge_snapshot,
            "n_samples": config.n_judge_samples,
            "prompt_hash": judge.JUDGE_PROMPT_HASH,
            "cache_hits": judge_cache.hits,
            "cache_misses": judge_cache.misses,
            "cache_hit_rate": judge_cache.hit_rate,
        },
        "worker": {
            "snapshot": config.worker_snapshot,
            "cache_hits": runner_cache.hits,
            "cache_misses": runner_cache.misses,
            "cache_hit_rate": runner_cache.hit_rate,
        },
        "evaluate": {"num_threads": config.num_threads, "failure_score": config.failure_score},
        "held_out_delta": held_out_delta,
        "ceilings": {"max_total_tokens": config.max_total_tokens, "max_usd": config.max_usd},
        "per_example": [
            {
                "id": item.id,
                "pages": list(item.pages),
                "question": item.question,
                "qa_accuracy": item.qa_accuracy,
                "citation_validity": item.citation_validity,
                "quality": item.quality,
                "total_tokens": item.total_tokens,
            }
            for item in breakdown
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------- #
# Cross-generation held-out delta (null only at cold start)
# --------------------------------------------------------------------------- #


def _compute_held_out_delta(
    store: VaultStore,
    topic: str,
    generation: int,
    current_scalar: float,
    breakdown: Sequence[_ExampleBreakdown],
) -> dict[str, object] | None:
    """Diff this generation against the prior one, keyed on the stable golden id.

    Returns ``None`` -- never a fabricated ``0`` -- at cold start
    (``generation == 1``: no prior generation exists). This is the *only* null
    branch: at any later generation the prior manifest is one this harness itself
    wrote (or reconciled to the same v2 shape), so an unreadable or malformed prior
    manifest is a genuine corruption and is allowed to raise a typed error rather
    than be masked as "no baseline" -- consistent with the codebase's
    typed-errors-over-silent-fallback convention.

    Otherwise it follows the prior generation's
    :attr:`~knotica.core.records.MetricsRecord.artifact_ref` to read the prior
    manifest off the same clone ``store`` (clone-not-live-vault) and computes:
    ``scalar_delta`` (the topic-level move), ``ids_added``/``ids_removed`` (the
    golden set's symmetric difference), and a ``per_id`` vector -- built fresh from
    the stable id, never a question-keyed map -- of score deltas and
    retrieval-trace set-diffs for every id present in both generations.
    """
    if generation == 1:
        return None
    prior_record = _prior_metrics_record(store, topic, generation)
    prior_ref = prior_record.artifact_ref
    if prior_ref is None:
        raise EvalRunError(
            topic,
            f"the prior generation ({prior_record.generation}) recorded no manifest "
            "artifact_ref to diff the held-out delta against",
        )
    prior_manifest = json.loads(store.read_text(prior_ref))
    prior_by_id = {entry["id"]: entry for entry in prior_manifest["per_example"]}
    current_by_id = {item.id: item for item in breakdown}
    prior_ids = set(prior_by_id)
    current_ids = set(current_by_id)
    return {
        "prior_generation": prior_record.generation,
        "prior_artifact_ref": prior_ref,
        "scalar_delta": current_scalar - prior_manifest["scalar"],
        "ids_added": sorted(current_ids - prior_ids),
        "ids_removed": sorted(prior_ids - current_ids),
        "per_id": {
            example_id: _per_id_delta(current_by_id[example_id], prior_by_id[example_id])
            for example_id in sorted(current_ids & prior_ids)
        },
    }


def _prior_metrics_record(store: VaultStore, topic: str, generation: int) -> MetricsRecord:
    """The highest-generation ``MetricsRecord`` recorded below ``generation``.

    Reads the same ``metrics.jsonl`` history :func:`_next_generation` parses. Called
    only at ``generation > 1``, where at least one prior record exists (the current
    record is not appended until :func:`_persist`), so "prior" is unambiguously the
    newest record below this generation -- robust even if generations are ever
    non-contiguous (Decision D4).
    """
    text = store.read_text(_metrics_path(topic))
    records = [MetricsRecord.from_json_line(line) for line in text.splitlines() if line.strip()]
    below = [record for record in records if record.generation < generation]
    return max(below, key=lambda record: record.generation)


def _per_id_delta(current: _ExampleBreakdown, prior: Any) -> dict[str, object]:
    """Score deltas and retrieval-trace set-diffs for one id present in both generations.

    ``pages_added`` are pages in the current trace but not the prior (a candidate
    diluter); ``pages_removed`` are pages in the prior trace but not the current (a
    candidate displacement). Both are sorted for a deterministic manifest. ``prior``
    is a JSON-parsed v2 ``per_example`` entry (assumed v2 shape -- no defensive
    version parsing, per the dropped-backward-compat narrowing).
    """
    current_pages = set(current.pages)
    prior_pages = set(prior["pages"])
    return {
        "quality_delta": current.quality - float(prior["quality"]),
        "qa_accuracy_delta": current.qa_accuracy - float(prior["qa_accuracy"]),
        "citation_validity_delta": current.citation_validity - float(prior["citation_validity"]),
        "pages_added": sorted(current_pages - prior_pages),
        "pages_removed": sorted(prior_pages - current_pages),
    }


# --------------------------------------------------------------------------- #
# Persistence -- one VaultTransaction, one commit, one log entry
# --------------------------------------------------------------------------- #


def _persist(
    store: VaultStore,
    clone_root: Path,
    topic: str,
    generation: int,
    record: MetricsRecord,
    manifest: str,
    budget: _Budget,
    config: HarnessConfig,
) -> None:
    """Append the record + manifest (+ frozen budget on gen 0) in one transaction.

    Every write flows through :class:`~knotica.core.transaction.VaultTransaction`
    -- the single mutation path -- so the whole run is exactly one
    ``knotica(eval): <topic> — generation N`` commit with one ``log.md`` entry on
    the clone, regardless of how many files it touches.
    """
    metrics_path = _metrics_path(topic)
    existing = store.read_text(metrics_path) if store.exists(metrics_path) else ""
    new_metrics = _append_jsonl_line(existing, record.to_json_line())
    with VaultTransaction(store, clone_root, "eval", topic, f"generation {generation}") as txn:
        txn.write(metrics_path, new_metrics)
        txn.write(_manifest_path(topic, generation), manifest)
        if budget.newly_frozen:
            txn.write(_eval_toml_path(topic), _format_eval_toml(budget, config))


def _append_jsonl_line(existing_text: str, line: str) -> str:
    """Append one JSONL line, preserving prior records and a single trailing newline."""
    if not existing_text.strip():
        return line + "\n"
    return existing_text.rstrip("\n") + "\n" + line + "\n"


def _metrics_path(topic: str) -> str:
    """Vault-relative path of the topic's eval-history file."""
    return f"{topic}/{_KNOTICA_DIR}/{_METRICS_FILENAME}"


def _eval_toml_path(topic: str) -> str:
    """Vault-relative path of the topic's frozen budget file."""
    return f"{topic}/{_KNOTICA_DIR}/{_EVAL_TOML_FILENAME}"


def _manifest_path(topic: str, generation: int) -> str:
    """Vault-relative path of this run's reproducibility manifest."""
    return f"{topic}/{_KNOTICA_DIR}/{_EVAL_RUNS_DIRNAME}/gen-{generation}/{_MANIFEST_FILENAME}"
