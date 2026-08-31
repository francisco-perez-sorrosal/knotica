"""Golden devset for the eval harness -- load, verify, convert; bootstrap and freeze.

A topic's *golden set* is the frozen, human-reviewed, held-out set of QA pairs the
eval scalar is measured against. It lives at
``<topic>/.knotica/datasets/golden.jsonl`` with a sibling ``MANIFEST.json`` that
content-addresses it (a sha256 of the file's exact bytes) and marks it
``split: held_out``. The golden set is kept deliberately *disjoint* from the
flywheel ``qa.jsonl`` (the future DSPy trainset), so the eval scalar can never be
measured on the very examples an optimizer trained against.

The package has two sides, and they differ in how they touch the vault.

**The deterministic read side** -- pure reads, used by the harness at the top of
every run:

* :func:`load` reads and verifies a topic's golden set, distinguishing its failure
  modes with typed, actionable errors -- the set is absent
  (:class:`GoldenSetMissingError`, the "run the bootstrap" outcome) or present but
  untrustworthy (:class:`GoldenSetIntegrityError` -- a missing, malformed,
  wrong-split, or mismatched ``MANIFEST.json``).
* :func:`to_example` converts a :class:`~knotica.core.records.QARecord` into the
  ``dspy.Example`` the DSPy metric runner consumes.
* :func:`verify_disjoint_from_trainset` is the held-out-split guard: a question
  shared between ``golden.jsonl`` and ``qa.jsonl`` is a contamination signal and
  raises :class:`GoldenSetContaminationError`.

**The interactive write side** that *produces* a golden set, in two human-gated
stages:

* :func:`bootstrap` reads a topic's entity pages and asks the injected LLM to
  synthesize candidate ``(question, reference_answer, citations)`` triples --
  each carrying the verbatim support quotes it was grounded in, located back to
  deterministic 1-based line ranges -- writing them to an *uncommitted* review
  staging file for a human to edit and accept; it never writes ``golden.jsonl``
  and never commits, which is why its signature carries no ``vault_root``.
* :func:`freeze` turns the human-accepted candidates into ``QARecord``s and
  writes the frozen ``golden.jsonl`` + sibling ``MANIFEST.json`` through one
  :class:`~knotica.core.transaction.VaultTransaction` (one commit), after
  verifying the set is disjoint from the flywheel trainset.

One module per concern; this ``__init__`` is the import surface, so every consumer
keeps writing ``from knotica.evals import golden`` and never names a submodule.

* :mod:`~knotica.evals.golden.contract` -- where a golden set lives, the floor it
  declares, and the refusal grammar both sides raise. The leaf.
* :mod:`~knotica.evals.golden.manifest` -- the content-addressing
  ``MANIFEST.json``: parsed and verified on read, rendered on freeze.
* :mod:`~knotica.evals.golden.read` -- ``load`` / ``to_example`` /
  ``verify_disjoint_from_trainset``.
* :mod:`~knotica.evals.golden.candidates` -- the candidate-dict key names and the
  boundary parsers both write-side stages read it through.
* :mod:`~knotica.evals.golden.support` -- locating a model-supplied support quote
  back to real 1-based line numbers (never trusting model-supplied ones).
* :mod:`~knotica.evals.golden.synthesize` -- the generate stage: ``bootstrap``,
  ``entity_pages``, and the uncommitted staging write.
* :mod:`~knotica.evals.golden.freeze` -- the freeze stage: the one commit.

``dspy`` is imported **lazily**, inside :func:`to_example` only, so ``import
knotica.evals.golden`` never pulls the eval dependency group onto an unrelated
import path (for example the MCP cold start); ``anthropic`` never enters this
package at all (the LLM seam is the injected :class:`~knotica.evals.llm.LLMClient`,
whose real implementation defers its own heavy import).
"""

from knotica.evals.golden.contract import (
    EVAL_MIN_GOLDEN,
    GoldenCandidateError,
    GoldenSetContaminationError,
    GoldenSetError,
    GoldenSetFloorWarning,
    GoldenSetIntegrityError,
    GoldenSetMissingError,
    golden_dataset_path,
    golden_manifest_path,
    golden_staging_path,
)
from knotica.evals.golden.freeze import FreezeResult, freeze
from knotica.evals.golden.manifest import GOLDEN_SPLIT, GoldenManifest
from knotica.evals.golden.read import load, to_example, verify_disjoint_from_trainset
from knotica.evals.golden.support import _locate_span as _locate_span
from knotica.evals.golden.synthesize import bootstrap, entity_pages

__all__ = [
    "EVAL_MIN_GOLDEN",
    "GOLDEN_SPLIT",
    "FreezeResult",
    "GoldenCandidateError",
    "GoldenManifest",
    "GoldenSetContaminationError",
    "GoldenSetError",
    "GoldenSetFloorWarning",
    "GoldenSetIntegrityError",
    "GoldenSetMissingError",
    "bootstrap",
    "entity_pages",
    "freeze",
    "golden_dataset_path",
    "golden_manifest_path",
    "golden_staging_path",
    "load",
    "to_example",
    "verify_disjoint_from_trainset",
]

# ``_locate_span`` is imported above and deliberately absent from ``__all__``: it
# stays package-private. The quote-location ladder is pinned directly by tests that
# import it from this package, so re-exporting here is what let the split land with
# zero test edits. The redundant `as` alias marks the import as a re-export rather
# than dead code; it does not make the name public API.
