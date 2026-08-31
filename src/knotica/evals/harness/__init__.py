"""The eval harness -- ``run_eval``, the frozen-corpus evaluator for one topic.

Clones the vault at a pinned SHA, drives the golden devset through
``dspy.Evaluate`` over the baseline runner and the triple-consumer scorer, folds
per-example quality with lint-cleanliness and a token-cost discount into one stable
scalar, and appends a :class:`~knotica.core.records.MetricsRecord` to the clone's
``<topic>/.knotica/metrics.jsonl`` through the single mutation path. The source
vault is left byte-identical -- loops always work on a clone, never the live vault.

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
3. **Score the devset** with ``dspy.Evaluate(devset, metric, num_threads=N)`` (default 4)
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

One module per stage of that flow; this ``__init__`` is the import surface, so
every consumer keeps writing ``from knotica.evals.harness import run_eval`` and
never names a submodule.

* :mod:`~knotica.evals.harness.errors` -- the refusal grammar, declared once
  because the four refusals are raised from four different stages.
* :mod:`~knotica.evals.harness.paths` -- where a run's three outputs land inside
  a topic.
* :mod:`~knotica.evals.harness.accounting` -- the usage-totalling LLM proxy and
  the post-run spend ceilings it feeds (step 4).
* :mod:`~knotica.evals.harness.evaluate` -- driving ``dspy.Evaluate``, the two
  error-capture seams, and the instrument-failure rejection (step 3).
* :mod:`~knotica.evals.harness.scoring` -- per-example breakdown, the frozen
  budget, and the composed scalar (step 5).
* :mod:`~knotica.evals.harness.artifacts` -- the record, the manifest, the
  cross-generation delta, and the one transaction that writes them (step 6).
* :mod:`~knotica.evals.harness.run` -- ``run_eval`` itself and the frozen-corpus
  clone (steps 1-2).

**Import stays cheap.** ``dspy`` is imported lazily inside ``run_eval``, so
``import knotica.evals.harness`` (and therefore ``import knotica.evals``) never
forces the eval dependency group onto an unrelated import path such as the MCP
cold start.
"""

from knotica.evals.harness.artifacts import _compute_held_out_delta as _compute_held_out_delta
from knotica.evals.harness.errors import (
    EvalHarnessError,
    EvalRunError,
    LiveVaultTargetError,
    SpendCeilingExceededError,
)
from knotica.evals.harness.run import EvalRunResult, run_eval
from knotica.evals.harness.scoring import _count_content_pages as _count_content_pages

__all__ = [
    "EvalHarnessError",
    "EvalRunError",
    "EvalRunResult",
    "LiveVaultTargetError",
    "SpendCeilingExceededError",
    "run_eval",
]

# ``_compute_held_out_delta`` and ``_count_content_pages`` are imported above and
# deliberately absent from ``__all__``: they stay package-private. Both are pinned
# by tests that import them from this package -- the cold-start/probe-sentinel
# delta contract, and the schema-overlay exclusion from the scored page set --
# so re-exporting them here is what let the split land with zero test edits. The
# redundant `as` alias is the marker that says the import is a re-export, not dead
# code; it does not make either name public API.
