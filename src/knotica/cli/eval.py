"""``knotica eval`` -- the headless per-topic eval-harness entry point.

Runs the frozen-corpus evaluator for one topic: it clones the configured source
vault at a pinned SHA, scores the topic's held-out golden set, and appends one
``MetricsRecord`` to the *clone's* ``metrics.jsonl`` -- never the live vault.

With ``--bootstrap`` it instead generates synthetic golden-set candidates from the
topic's entity pages and stages them (uncommitted) for human review -- freezing the
accepted subset into the held-out set is a separate, human-gated act, so the command
never auto-accepts a pair.

This adapter is thin by design. It does exactly one piece of config work --
resolve the source vault (mirroring ``status``'s ``diagnose()`` shape) -- then
delegates the whole evaluation to :func:`knotica.evals.harness.run_eval` and
renders the returned record as a table or ``--json``. Every failure the harness
raises lands in the house error envelope (message + fix, never a raw stack
trace); the CLI performs no vault mutation of its own (the single-writer
invariant, enforced by the import-boundary fitness test -- ``run_eval`` routes
its one commit through ``core.transaction`` on the clone).

The evaluator's ``ANTHROPIC_API_KEY`` is read from the environment only (the
trust boundary lives in ``evals.llm``); this adapter never reads, passes, or
echoes it. An absent key surfaces as the clean "eval is not configured" error.

Exit codes (documented interface -- hooks and scripts branch on these):

* ``0`` success -- the scalar was produced and one record appended (on the clone),
  or (with ``--bootstrap``) synthetic golden candidates were staged for review.
* ``1`` the run failed -- golden-set integrity/contamination, a spend ceiling, an
  instrument failure, the internal live-vault guard, or (with ``--bootstrap``) a
  malformed synthesis response.
* ``2`` misuse -- an invalid config override (e.g. ``--num-threads 2``).
* ``3`` not configured -- no vault, or ``ANTHROPIC_API_KEY`` unset (the key value
  is never echoed) -- on both the eval and the ``--bootstrap`` path.
* ``5`` the topic has no golden set -- run ``knotica eval --bootstrap`` first.
"""

import argparse
from pathlib import Path

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_MISUSE,
    EXIT_NO_GOLDEN_SET,
    EXIT_NOT_CONFIGURED,
    EXIT_SUCCESS,
    Console,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.config import ConfigDiagnosis, ConfigState, ResolvedVault, diagnose
from knotica.core.errors import KnoticaError
from knotica.core.records import MetricsRecord, RecordParseError
from knotica.evals.config import DEFAULT_CONFIG, HarnessConfig
from knotica.evals.golden import (
    GoldenCandidateError,
    GoldenSetError,
    GoldenSetMissingError,
    bootstrap,
    golden_staging_path,
)
from knotica.evals.harness import EvalHarnessError, EvalRunResult, run_eval
from knotica.evals.judge import JudgeParseError
from knotica.evals.llm import AnthropicClient
from knotica.evals.runner import MalformedResponseError
from knotica.store import LocalFSStore

__all__ = ["EVAL_JSON_SCHEMA_VERSION", "configure", "run"]

#: Stable version of the ``--json`` envelope (consumers branch on this).
EVAL_JSON_SCHEMA_VERSION = 1

#: The topic's hidden per-topic state directory and its eval-history file
#: (mirrors ``evals.harness``; kept a local constant since a sibling module's
#: private symbol is not imported, per the codebase convention).
_KNOTICA_DIR = ".knotica"
_METRICS_FILENAME = "metrics.jsonl"

#: ``HarnessConfig`` fields exposed as ``--flag`` overrides. Deliberately a
#: safety/run-shaping subset: the spend ceilings (the ``SpendCeilingExceededError``
#: fix text names these two exact flags), the model pins, the judge-sample count,
#: and the thread count. The scalar-formula coefficients (weights, lambda, tau,
#: formula version) and the optimizer-only ``threshold`` are *not* exposed -- an
#: override of those silently changes the instrument (and would need a formula
#: bump), and ``threshold`` has no effect on the float-branch eval scalar.
_OVERRIDE_FIELDS: tuple[str, ...] = (
    "max_total_tokens",
    "max_usd",
    "judge_snapshot",
    "worker_snapshot",
    "n_judge_samples",
    "num_threads",
)


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``eval`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "eval",
        parents=[common_parent()],
        help="run the headless eval harness for a topic",
        description=(
            "Evaluate a topic against its frozen held-out golden set on a vault "
            "clone; append one metrics record. The live vault is never touched."
        ),
    )
    parser.add_argument("--topic", metavar="NAME", required=True, help="the topic to evaluate")
    parser.add_argument(
        "--ref",
        metavar="COMMIT",
        help="pin the frozen corpus to a commit-ish (default: the source vault's HEAD)",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="generate and stage golden-set candidates for human review (does not freeze)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    _add_override_flags(parser)
    return parser


def _add_override_flags(parser: argparse.ArgumentParser) -> None:
    """Add the packaged-default config-override flags (all default to unset)."""
    parser.add_argument(
        "--max-total-tokens",
        type=int,
        metavar="N",
        help="per-run total-token hard-abort ceiling (default: the packaged ceiling)",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        metavar="USD",
        help="per-run USD hard-abort ceiling (default: the packaged ceiling)",
    )
    parser.add_argument(
        "--judge-snapshot",
        metavar="MODEL",
        help="override the pinned judge model snapshot",
    )
    parser.add_argument(
        "--worker-snapshot",
        metavar="MODEL",
        help="override the pinned worker/baseline model snapshot",
    )
    parser.add_argument(
        "--n-judge-samples",
        type=int,
        metavar="N",
        help="number of temperature-0 judge samples to median over (must be odd)",
    )
    parser.add_argument(
        "--num-threads",
        type=int,
        metavar="N",
        help="dspy.Evaluate thread count (must be 1 in v1)",
    )


def run(args: argparse.Namespace) -> int:
    """Resolve the source vault, then evaluate the topic (or bootstrap its set)."""
    console = console_from_args(args)
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return _report_unconfigured(console, diagnosis)
    if args.bootstrap:
        return _run_bootstrap(console, diagnosis.vault, args)
    return _run_eval(console, diagnosis.vault, args)


def _report_unconfigured(console: Console, diagnosis: ConfigDiagnosis) -> int:
    """Non-READY config states all exit 3; render the state-specific remediation."""
    if diagnosis.state == ConfigState.UNCONFIGURED:
        return unconfigured(console)
    console.error(diagnosis.detail)
    if diagnosis.remediation:
        console.error(f"To fix: {diagnosis.remediation}")
    return EXIT_NOT_CONFIGURED


def _run_eval(console: Console, vault: ResolvedVault, args: argparse.Namespace) -> int:
    """Build the config, run the harness, and render the record (or a clean error)."""
    try:
        run_config = _resolve_config(DEFAULT_CONFIG, args)
    except ValueError as bad_override:
        console.error(str(bad_override))
        return EXIT_MISUSE

    previous_version = _previous_harness_version(vault.path, args.topic)
    try:
        result = run_eval(args.topic, source_root=vault.path, ref=args.ref, config=run_config)
    except GoldenSetMissingError as missing:
        _emit_error(console, missing)
        return EXIT_NO_GOLDEN_SET
    except (GoldenSetError, EvalHarnessError) as failure:
        # Golden-set integrity/contamination, the live-vault guard, a spend
        # ceiling, or an instrument failure: an operational failure, rendered
        # cleanly (message + fix, never a stack trace).
        _emit_error(console, failure)
        return EXIT_ERROR
    except KnoticaError as not_configured:
        # The eval-not-configured case (absent ANTHROPIC_API_KEY / missing eval
        # group): a clean what+fix, exit 3; the key value is never in the message.
        _emit_error(console, not_configured)
        return EXIT_NOT_CONFIGURED
    except (MalformedResponseError, JudgeParseError) as instrument:
        # A defensive belt: the harness wraps instrument failures into an
        # ``EvalRunError`` above, but if a raw parse error ever escapes it still
        # lands in the envelope shape rather than as a bare stack trace.
        _emit_instrument_error(console, instrument)
        return EXIT_ERROR

    _warn_instrument_drift(console, previous_version, result.record)
    _emit_record(console, args, result)
    return EXIT_SUCCESS


def _run_bootstrap(console: Console, vault: ResolvedVault, args: argparse.Namespace) -> int:
    """Generate synthetic golden-set candidates to the review staging file.

    The generate half of the human-gated bootstrap: it reads the topic's entity
    pages, asks the worker model to synthesize one candidate QA pair per page, and
    stages them (uncommitted) on the source vault for the user to review and edit.
    It never writes ``golden.jsonl`` and never commits -- freezing the accepted
    subset into the held-out set is a separate, human-gated act (nothing is
    auto-accepted). An absent ``ANTHROPIC_API_KEY`` surfaces as the same clean
    eval-not-configured envelope as the metrics path (exit 3), never a stack trace;
    the key value is never echoed.
    """
    try:
        run_config = _resolve_config(DEFAULT_CONFIG, args)
    except ValueError as bad_override:
        console.error(str(bad_override))
        return EXIT_MISUSE

    try:
        # The API key is resolved (env-only) inside AnthropicClient construction,
        # which raises the clean not-configured error *before* any network call.
        client = AnthropicClient()
        candidates = bootstrap(
            LocalFSStore(vault.path), args.topic, client, run_config.worker_snapshot
        )
    except KnoticaError as not_configured:
        # Absent ANTHROPIC_API_KEY / missing eval group: a clean what+fix, exit 3;
        # the key value never appears in the message.
        _emit_error(console, not_configured)
        return EXIT_NOT_CONFIGURED
    except GoldenCandidateError as malformed:
        # A page's synthesis response did not parse into a candidate triple.
        _emit_bootstrap_parse_error(console, malformed)
        return EXIT_ERROR

    _emit_bootstrap(console, args, vault, candidates)
    return EXIT_SUCCESS


def _resolve_config(base: HarnessConfig, args: argparse.Namespace) -> HarnessConfig:
    """Thread the config-override flags the user set onto the packaged defaults.

    Only flags actually passed override a default. ``HarnessConfig`` re-validates
    the result, so an unsafe value (a multithreaded run, a non-positive ceiling,
    an even judge-sample count) raises ``ValueError`` -- surfaced by the caller as
    a misuse exit rather than a mid-run crash.
    """
    overrides = {
        field: getattr(args, field)
        for field in _OVERRIDE_FIELDS
        if getattr(args, field, None) is not None
    }
    return base.with_overrides(**overrides) if overrides else base


def _previous_harness_version(vault_path: Path, topic: str) -> str | None:
    """The ``harness_version`` of the topic's most recent recorded eval, or ``None``.

    Read from the *source* vault's ``metrics.jsonl`` -- the history the user
    actually compares scalars across. A run writes generation N+1 to a throwaway
    clone, so the source's last line is the previous instrument to compare
    against. ``None`` when the topic has no eval history yet (a first-ever record
    warns about nothing) or the last line does not parse.
    """
    store = LocalFSStore(vault_path)
    metrics_path = f"{topic}/{_KNOTICA_DIR}/{_METRICS_FILENAME}"
    if not store.exists(metrics_path):
        return None
    lines = [line for line in store.read_text(metrics_path).splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return MetricsRecord.from_json_line(lines[-1]).harness_version
    except RecordParseError:
        return None


def _warn_instrument_drift(
    console: Console, previous_version: str | None, record: MetricsRecord
) -> None:
    """Warn when this run's instrument differs from the topic's previous record.

    A different ``harness_version`` (a rotated snapshot, an edited judge prompt, a
    bumped formula, a ``dspy`` upgrade) means the new scalar is not directly
    comparable to the prior one -- comparing them across the boundary is comparing
    measurements from two different instruments. A first-ever record has nothing to
    compare against and warns about nothing.
    """
    if previous_version is not None and previous_version != record.harness_version:
        console.warn(
            "Instrument changed: this run's harness_version differs from the "
            "topic's previous metrics.jsonl record. Scalars across this boundary "
            "are not directly comparable (they were measured by different "
            "instruments)."
        )


def _emit_error(console: Console, error: KnoticaError) -> None:
    """Render a house error to stderr as its message + fix (never a stack trace)."""
    console.error(error.message)
    if error.fix:
        console.error(f"To fix: {error.fix}")


def _emit_instrument_error(console: Console, error: Exception) -> None:
    """Render an escaped instrument (parse) failure cleanly -- message + a fix."""
    console.error(str(error))
    console.error(
        "To fix: re-run; if it persists, the worker/judge model snapshot or the "
        "topic's query.md prompt is producing malformed output."
    )


def _emit_bootstrap_parse_error(console: Console, error: Exception) -> None:
    """Render a malformed-synthesis failure cleanly -- the detail plus a next action."""
    console.error(str(error))
    console.error(
        "To fix: re-run; if it persists, the worker model snapshot is producing "
        "malformed synthesis output for this topic's entity pages."
    )


def _emit_bootstrap(
    console: Console,
    args: argparse.Namespace,
    vault: ResolvedVault,
    candidates: list[dict[str, object]],
) -> None:
    """Render the staged-candidates handoff as a table or a stable ``--json`` envelope."""
    staging_ref = golden_staging_path(args.topic)
    if args.json:
        console.data(_bootstrap_json_payload(args.topic, staging_ref, len(candidates)))
    else:
        _render_bootstrap(console, args.topic, vault, staging_ref, len(candidates))


def _bootstrap_json_payload(topic: str, staging_ref: str, n_candidates: int) -> str:
    """Build the stable ``--json`` envelope for a bootstrap run (finds the staging path)."""
    import json

    payload = {
        "schema_version": EVAL_JSON_SCHEMA_VERSION,
        "topic": topic,
        "staging_ref": staging_ref,
        "n_candidates": n_candidates,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_bootstrap(
    console: Console,
    topic: str,
    vault: ResolvedVault,
    staging_ref: str,
    n_candidates: int,
) -> None:
    """Print the human-readable staged-candidates handoff for review-and-freeze."""
    console.data(f"knotica eval --bootstrap    topic: {topic}")
    console.data("")
    console.data(f"  staged candidates  {n_candidates}")
    console.data(f"  vault              {vault.path}")
    console.data(f"  staging file       {staging_ref}")
    console.data("")
    console.data("  Next: review and edit the staged pairs, then freeze the accepted subset")
    console.data("  into the held-out golden set -- a human-gated step; nothing is auto-accepted.")


def _emit_record(console: Console, args: argparse.Namespace, result: EvalRunResult) -> None:
    """Render the appended record as a table or a stable ``--json`` envelope."""
    if args.json:
        console.data(_json_payload(result.record, result.clone_root))
    else:
        _render_table(console, result.record, result.clone_root)


def _resolved_manifest_path(clone_root: Path, record: MetricsRecord) -> str | None:
    """The run's manifest as a resolvable path (clone root + clone-relative ref).

    The record's ``artifact_ref`` is relative to the throwaway clone, so on its own
    it is not something a human can open. Anchoring it to ``clone_root`` yields the
    absolute path the reproducibility manifest actually lives at. ``None`` when the
    record carries no manifest ref.
    """
    if not record.artifact_ref:
        return None
    return str(clone_root / record.artifact_ref)


def _json_payload(record: MetricsRecord, clone_root: Path) -> str:
    """Build the stable ``--json`` envelope for one eval record.

    Carries the record's own fields (scalar, the four components, ``corpus_ref``,
    ``harness_version``, generation, examples), the clone-relative ``artifact_ref``,
    plus the ``clone_root`` the run committed to and the resolved ``manifest_path``
    (``clone_root`` + ``artifact_ref``) -- the absolute file where the full
    reproducibility detail (exact token/USD totals, the judge cache hit-rate, the
    per-example breakdown) is written, and the clone a human reviews the eval commit
    from.
    """
    import json

    components = record.components
    payload = {
        "schema_version": EVAL_JSON_SCHEMA_VERSION,
        "topic": record.topic,
        "timestamp": record.timestamp,
        "generation": record.generation,
        "harness_version": record.harness_version,
        "scalar": record.scalar,
        "components": {
            "qa_accuracy": components.qa_accuracy,
            "citation_validity": components.citation_validity,
            "lint_violations": components.lint_violations,
            "token_cost": components.token_cost,
        },
        "n_examples": record.n_examples,
        "corpus_ref": record.corpus_ref,
        "artifact_ref": record.artifact_ref,
        "clone_root": str(clone_root),
        "manifest_path": _resolved_manifest_path(clone_root, record),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_table(console: Console, record: MetricsRecord, clone_root: Path) -> None:
    """Print the human-readable scalar + components table for one eval record."""
    components = record.components
    console.data(f"knotica eval    topic: {record.topic}")
    console.data("")
    console.data(f"  scalar             {record.scalar:.4f}")
    console.data(f"  qa accuracy        {components.qa_accuracy:.4f}")
    console.data(f"  citation validity  {components.citation_validity:.4f}")
    console.data(f"  lint violations    {components.lint_violations:.0f}")
    console.data(f"  token cost factor  {components.token_cost:.4f}")
    console.data("")
    console.data(f"  generation         {record.generation}")
    console.data(f"  examples           {record.n_examples}")
    console.data(f"  corpus             {record.corpus_ref}")
    console.data(f"  harness version    {record.harness_version}")
    console.data(f"  clone              {clone_root}")
    manifest_path = _resolved_manifest_path(clone_root, record)
    if manifest_path:
        # The manifest is where the exact token/USD totals and judge cache
        # hit-rate live; the record itself only carries the composed scalar.
        console.data(f"  manifest           {manifest_path}")
    console.data("")
    console.data("  Review: this run evaluated a throwaway clone at the path above; its one")
    console.data("  knotica(eval) commit holds the frozen corpus and this manifest. Inspect or")
    console.data("  diff that clone to review the run -- the live vault was not touched.")
