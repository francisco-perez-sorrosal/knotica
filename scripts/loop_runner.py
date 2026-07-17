#!/usr/bin/env python3
"""``loop_runner`` — watch ``loop/c/*`` branches, eval, gate, merge or revert.

The Sentinel spine (M2): a candidate branch under ``loop/c/`` is evaluated on a
clone against a frozen baseline; green merges onto the default branch (bringing
the eval metrics commit with it), red deletes the candidate. State is exposed
only through ``wiki_status`` / ``metrics_read`` via ``<topic>/.knotica/loop-state.json``.

Examples::

    # Freeze the live baseline, then process one pending candidate:
    uv run python scripts/loop_runner.py --topic agentic-systems --set-baseline 0.5707
    uv run python scripts/loop_runner.py --topic agentic-systems --once

    # Poll until interrupted:
    uv run python scripts/loop_runner.py --topic agentic-systems --interval 2
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from knotica.core.config import resolve
from knotica.core.loop import (
    DEFAULT_BRANCH_PREFIX,
    LoopDecision,
    LoopRunner,
    harness_evaluate,
)


def _resolve_vault(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(resolve().path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topic", required=True, help="topic whose loop to run")
    parser.add_argument("--vault", help="vault root (default: knotica config)")
    parser.add_argument(
        "--set-baseline",
        type=float,
        metavar="SCALAR",
        help="freeze the gate baseline and exit (does not eval)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one pending candidate and exit",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="poll interval seconds when not --once (default: 2)",
    )
    parser.add_argument(
        "--branch-prefix",
        default=DEFAULT_BRANCH_PREFIX,
        help=f"candidate branch prefix (default: {DEFAULT_BRANCH_PREFIX})",
    )
    parser.add_argument(
        "--push",
        metavar="REMOTE",
        help="optional git remote to push after keep (e.g. origin)",
    )
    parser.add_argument(
        "--fake-scalar",
        type=float,
        help="TEST ONLY: skip LLM eval and return this scalar (requires a clone tip)",
    )
    args = parser.parse_args(argv)

    vault = _resolve_vault(args.vault)
    evaluate = harness_evaluate
    if args.fake_scalar is not None:
        evaluate = _fake_evaluate_factory(args.fake_scalar)

    runner = LoopRunner(
        vault,
        args.topic,
        evaluate=evaluate,
        branch_prefix=args.branch_prefix,
        push_remote=args.push,
    )

    if args.set_baseline is not None:
        state = runner.set_baseline(args.set_baseline)
        print(
            f"baseline frozen at {state.baseline_scalar} for topic={state.topic}",
            file=sys.stderr,
        )
        return 0

    if args.once:
        result = runner.poll_once()
        print(result.message)
        if result.decision is LoopDecision.fail and result.acted:
            return 1
        return 0

    print(
        f"loop_runner watching {vault} topic={args.topic} prefix={args.branch_prefix}",
        file=sys.stderr,
    )
    while True:
        result = runner.poll_once()
        if result.acted:
            print(result.message)
        time.sleep(max(0.2, args.interval))


def _fake_evaluate_factory(scalar: float):
    """Build a zero-network evaluate callable that clones the ref then returns ``scalar``."""

    def _evaluate(topic: str, source_root: Path, ref: str | None):
        import tempfile

        from knotica.core.loop import wrap_harness_result
        from knotica.core.records import MetricsComponents, MetricsRecord
        from knotica.core.vcs import VaultVcs
        from knotica.evals.harness import EvalRunResult

        dest = Path(tempfile.mkdtemp(prefix="knotica-loop-fake-"))
        clone = VaultVcs(source_root).clone_to(dest, ref)
        record = MetricsRecord(
            topic=topic,
            timestamp="2026-07-17T00:00:00Z",
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


if __name__ == "__main__":
    raise SystemExit(main())
