"""``knotica loop`` -- the self-improvement watcher (observe → gate → heal).

One command owns the whole autonomous loop for a topic:

* ``--watch`` (default): poll forever. Each tick observes the default branch
  (new content → eval on a clone → metrics merged home; first observation
  auto-freezes the gate baseline), then processes at most one pending
  ``loop/c/*`` candidate. A regression below baseline triggers the arena
  prompt-heal. A heartbeat under ``.knotica/locks/`` lets ``wiki_status`` and
  the dashboard report the runner as alive.
* ``--once``: one watch tick (observation + at most one candidate), then exit.
* ``--set-baseline SCALAR``: freeze the gate baseline explicitly and exit
  (rarely needed now that the first observation freezes itself).

All state lands in loop-state / ``metrics.jsonl`` via ``VaultTransaction`` --
``wiki_status`` / ``metrics_read`` remain the only dashboard data paths.
"""

import argparse
import sys
import threading
import time
from functools import partial
from pathlib import Path

from knotica.cli.common import (
    EXIT_ERROR,
    EXIT_SUCCESS,
    common_parent,
    console_from_args,
    unconfigured,
)
from knotica.core.arena import heuristic_arena_score
from knotica.core.config import diagnose
from knotica.core.gapfill_config import resolve_gapfill_config
from knotica.core.loop import (
    DEFAULT_BRANCH_PREFIX,
    LoopDecision,
    LoopRunner,
    build_loop_runner,
    harness_evaluate,
)
from knotica.core.loop_heartbeat import clear_heartbeat, write_heartbeat
from knotica.core.loop_progress import read_progress

__all__ = ["configure", "run"]

#: Default poll interval; observation evals are cache-cheap on unchanged content,
#: and a beat per tick keeps the liveness readout honest.
_DEFAULT_INTERVAL_SECONDS = 5.0

#: Default HEAD-stability window before a watch-mode observation fires — long
#: enough to coalesce a multi-commit burst, short enough to feel live.
_DEFAULT_OBSERVE_QUIET_SECONDS = 20.0


def configure(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``loop`` subcommand and its flags."""
    parser = subparsers.add_parser(
        "loop",
        parents=[common_parent()],
        help="run the self-improvement loop (watch, eval, gate, heal)",
        description=(
            "Watch a topic: eval new content on the default branch, gate loop/c/* "
            "candidates against the frozen baseline, and heal prompt regressions "
            "via the arena. First observation auto-freezes the baseline."
        ),
    )
    parser.add_argument("--topic", required=True, metavar="NAME", help="topic whose loop to run")
    parser.add_argument("--vault", metavar="PATH", help="vault root (default: knotica config)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one watch tick, then exit")
    mode.add_argument(
        "--set-baseline",
        type=float,
        metavar="SCALAR",
        help="freeze the gate baseline and exit (no eval)",
    )
    mode.add_argument(
        "--baseline-policy",
        choices=("latest", "best"),
        metavar="{latest,best}",
        help=(
            "set the gate policy and exit: latest = baseline tracks reality; "
            "best = high-water mark (better observations ratchet it up)"
        ),
    )
    mode.add_argument(
        "--rebaseline",
        choices=("best", "latest"),
        metavar="{best,latest}",
        help=(
            "re-freeze the baseline from metrics history and exit (best = "
            "high-water mark of the current instrument; latest = newest record)"
        ),
    )
    mode.add_argument(
        "--mark-observed",
        action="store_true",
        help=(
            "recovery: adopt the current HEAD as observed (cursor advanced, stage "
            "idle) after reconciling an interrupted observation by hand; no eval"
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=_DEFAULT_INTERVAL_SECONDS,
        metavar="SECONDS",
        help=f"watch poll interval (default: {_DEFAULT_INTERVAL_SECONDS:g})",
    )
    parser.add_argument(
        "--eval-threads",
        type=int,
        default=None,
        metavar="N",
        help=(
            "parallel workers for the eval's per-question scoring (1-8; default: "
            "harness default, 4). Results are identical to sequential; N concurrent "
            "API calls while an eval runs"
        ),
    )
    parser.add_argument(
        "--observe-quiet",
        type=float,
        default=_DEFAULT_OBSERVE_QUIET_SECONDS,
        metavar="SECONDS",
        help=(
            "watch mode: observe only after HEAD has been stable this long, so a "
            "burst of commits coalesces into one eval "
            f"(default: {_DEFAULT_OBSERVE_QUIET_SECONDS:g}; ignored by --once)"
        ),
    )
    parser.add_argument(
        "--push", metavar="REMOTE", help="git remote to push after keep/observe (e.g. origin)"
    )
    parser.add_argument(
        "--no-arena",
        action="store_true",
        help="on regression, record the failure only (skip the prompt-heal race)",
    )
    parser.add_argument(
        "--no-observe",
        action="store_true",
        help="gate loop/c/* candidates only; skip default-branch observation",
    )
    parser.add_argument(
        "--branch-prefix",
        default=DEFAULT_BRANCH_PREFIX,
        help=f"candidate branch prefix (default: {DEFAULT_BRANCH_PREFIX})",
    )
    parser.add_argument(
        "--arena-variants",
        metavar="JSON",
        help="JSON file of [{id,label,body}, ...] to race instead of generated variants",
    )
    # Demo/test seam: skip the LLM eval and return a fixed scalar. Hidden from
    # help on purpose -- never part of the advertised surface.
    parser.add_argument("--fake-scalar", type=float, help=argparse.SUPPRESS)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the selected loop mode; returns the process exit code."""
    vault = _resolve_vault(args.vault)
    if vault is None:
        return unconfigured(console_from_args(args))

    runner = _build_runner(args, vault)

    if args.set_baseline is not None:
        state = runner.set_baseline(args.set_baseline)
        print(
            f"baseline frozen at {state.baseline_scalar} for topic={state.topic}",
            file=sys.stderr,
        )
        return EXIT_SUCCESS

    if args.baseline_policy:
        state = runner.set_baseline_policy(args.baseline_policy)
        print(
            f"gate policy set to {state.baseline_policy} for topic={state.topic}", file=sys.stderr
        )
        return EXIT_SUCCESS

    if args.rebaseline:
        try:
            state = runner.rebaseline(args.rebaseline)
        except ValueError as error:
            print(f"knotica loop: {error}", file=sys.stderr)
            return EXIT_ERROR
        print(
            f"baseline re-frozen ({args.rebaseline}) at {state.baseline_scalar:.4f}"
            f" for topic={state.topic}",
            file=sys.stderr,
        )
        return EXIT_SUCCESS

    if args.mark_observed:
        state = runner.mark_observed()
        cursor = next(iter(state.cursors.values()), "")
        print(
            f"marked observed at {cursor[:12]} for topic={state.topic}; stage=idle",
            file=sys.stderr,
        )
        return EXIT_SUCCESS

    if args.once:
        acted_fail = _tick(runner, observe=not args.no_observe)
        return EXIT_ERROR if acted_fail else EXIT_SUCCESS

    return _watch(runner, args, vault)


def _watch(runner: LoopRunner, args: argparse.Namespace, vault: Path) -> int:
    """Poll forever; heartbeat from a background thread; clean removal on exit.

    The beat runs on its own daemon thread because a tick can legitimately take
    minutes (a real eval): beating only between ticks would let the liveness
    readout go stale exactly when the runner is doing its most important work.
    """
    interval = max(0.2, float(args.interval))
    print(
        f"knotica loop watching {vault} topic={args.topic}"
        f" observe={'off' if args.no_observe else 'on'}"
        f" arena={'off' if args.no_arena else 'on'} interval={interval:g}s",
        file=sys.stderr,
    )
    stop_beating = threading.Event()

    def _beat_forever() -> None:
        last_reported: tuple[str, int, str, int] | None = None
        while not stop_beating.is_set():
            write_heartbeat(vault, args.topic, interval_seconds=interval)
            progress = read_progress(vault, args.topic)
            if progress is not None:
                key = (
                    progress["phase"],
                    progress["current"],
                    progress["substage"],
                    progress["sub_current"],
                )
                if key != last_reported:
                    last_reported = key
                    counter = (
                        f" {progress['current']}/{progress['total']}" if progress["total"] else ""
                    )
                    substage = ""
                    if progress["substage"]:
                        sub_counter = (
                            f" {progress['sub_current']}/{progress['sub_total']}"
                            if progress["sub_total"]
                            else ""
                        )
                        substage = f" · {progress['substage']}{sub_counter}"
                    detail = f" — {progress['detail'][:70]}" if progress["detail"] else ""
                    print(f"  {progress['phase']}{counter}{substage}{detail}", file=sys.stderr)
            stop_beating.wait(interval)

    beater = threading.Thread(target=_beat_forever, name="loop-heartbeat", daemon=True)
    write_heartbeat(vault, args.topic, interval_seconds=interval)
    beater.start()
    try:
        while True:
            _tick(runner, observe=not args.no_observe)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("loop stopped", file=sys.stderr)
        return EXIT_SUCCESS
    finally:
        stop_beating.set()
        beater.join(timeout=2.0)
        clear_heartbeat(vault, args.topic)


def _tick(runner: LoopRunner, *, observe: bool) -> bool:
    """One watch tick; returns True when an acted-on step failed its gate."""
    failed = False
    if observe:
        observed = runner.observe_default()
        if observed.acted:
            print(observed.message)
            failed |= observed.decision is LoopDecision.fail
    candidate = runner.poll_once()
    if candidate.acted:
        print(candidate.message)
        failed |= candidate.decision is LoopDecision.fail
    return failed


def _build_runner(args: argparse.Namespace, vault: Path) -> LoopRunner:
    evaluate = harness_evaluate
    if args.eval_threads is not None:
        evaluate = partial(harness_evaluate, num_threads=max(1, args.eval_threads))
    if args.fake_scalar is not None:
        evaluate = _fake_evaluate_factory(args.fake_scalar)
    return build_loop_runner(
        vault,
        args.topic,
        evaluate=evaluate,
        branch_prefix=args.branch_prefix,
        push_remote=args.push,
        arena_enabled=not args.no_arena,
        arena_score=None if args.no_arena else heuristic_arena_score,
        arena_variants=_load_variants(args.arena_variants) if args.arena_variants else None,
        # Opt-in loop-side gap-fill batch, gated by the [gapfill] config table (off
        # by default). The resolved config is always passed; the drain only runs
        # when the config flag is enabled.
        gapfill_config=resolve_gapfill_config(),
        # One-shot invocations observe immediately; only watch mode debounces.
        observe_quiet_seconds=0.0 if args.once else max(0.0, args.observe_quiet),
    )


def _load_variants(path: str) -> list:
    import json

    from knotica.core.arena import VariantSpec

    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("--arena-variants must be a JSON array")
    return [
        VariantSpec(
            id=str(item["id"]),
            label=str(item.get("label") or item["id"]),
            body=str(item["body"]),
        )
        for item in payload
    ]


def _resolve_vault(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    diagnosis = diagnose()
    if diagnosis.vault is None:
        return None
    return Path(diagnosis.vault.path)


def _fake_evaluate_factory(scalar: float):
    """Zero-network evaluate: clone the ref, fabricate a metrics record at ``scalar``."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        import tempfile
        from datetime import UTC, datetime

        from knotica.core.loop import wrap_harness_result
        from knotica.core.records import MetricsComponents, MetricsRecord
        from knotica.core.vcs import VaultVcs
        from knotica.evals.harness import EvalRunResult

        dest = Path(tempfile.mkdtemp(prefix="knotica-loop-fake-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            generation=0,
            harness_version="fake-loop",
            scalar=float(scalar),
            components=MetricsComponents(
                qa_accuracy=float(scalar),
                citation_validity=1.0,
                lint_violations=0.0,
                token_cost=0.0,
            ),
            n_examples=1,
            corpus_ref=f"git:{clone.head_sha()}",
            artifact_ref=None,
        )
        return wrap_harness_result(EvalRunResult(record=record, clone_root=clone.root))

    return _evaluate
