"""What a run leaves behind: the frozen record, the manifest, and the one commit.

The record is deliberately thin -- the shape ``metrics.jsonl`` froze -- so every
reproducibility column that does not fit it lands in the sibling per-run manifest
the record's ``artifact_ref`` names. Building the two together keeps that division
visible in one place, and the cross-generation delta belongs with them because it is
computed *from* a prior manifest and written *into* the current one.

:func:`_persist` closes the module because the three outputs share one
:class:`~knotica.core.transaction.VaultTransaction` -- one commit, one ``log.md``
entry on the clone, however many files the run touched.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from knotica.core.metrics import BASELINE_PROBE_ARTIFACT_PREFIX
from knotica.core.records import MetricsComponents, MetricsRecord
from knotica.core.transaction import VaultTransaction
from knotica.evals import judge
from knotica.evals.cache import ResponseCache
from knotica.evals.config import HarnessConfig, harness_version
from knotica.evals.harness.accounting import _MODEL_PRICING_USD_PER_MTOK, _UsageAccountingClient
from knotica.evals.harness.paths import _eval_toml_path, _manifest_path, _metrics_path
from knotica.evals.harness.scoring import _Budget, _ExampleBreakdown, _format_eval_toml
from knotica.evals.runner import RUNNER_CACHE_NAMESPACE
from knotica.store import VaultStore

#: The self-versioning stamp on the per-run manifest (a v2 reader can probe for
#: ``per_example[].id``/``.pages`` and the ``held_out_delta`` object shape). The
#: manifest versions independently of the dec-006-frozen ``metrics.jsonl`` record;
#: today's unversioned shape is treated as an implicit v1.
_MANIFEST_SCHEMA_VERSION = 2


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


def _compute_held_out_delta(
    store: VaultStore,
    topic: str,
    generation: int,
    current_scalar: float,
    breakdown: Sequence[_ExampleBreakdown],
) -> dict[str, object] | None:
    """Diff this generation against the prior one, keyed on the stable golden id.

    Returns ``None`` -- never a fabricated ``0`` -- at cold start
    (``generation == 1``), and whenever the prior record carries no manifest to
    diff against: a zero-anchor probe records a ``baseline-probe:<mode>`` sentinel
    and a promoted compile records no ref at all. Neither is corruption and
    neither is the same instrument, so the delta is genuinely absent, not broken.
    A prior record that *does* name a manifest is one this harness wrote, so an
    unreadable or malformed one still raises rather than being masked.

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
    # Both non-harness writers land here. The probe's ref is a sentinel, not a
    # path, so resolving it raised a bare ENOENT that failed the whole eval; a
    # promoted compile records none at all. Guarding only ``is None`` meant any
    # topic baselined by the probe -- the automatic path -- failed its next eval.
    if prior_ref is None or prior_ref.startswith(BASELINE_PROBE_ARTIFACT_PREFIX):
        return None
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
