"""The knotica eval harness -- a headless, frozen-corpus per-topic evaluator.

The harness produces one stable scalar per topic (QA accuracy + citation
validity + lint cleanliness, discounted by a token-cost penalty) written to
``<topic>/.knotica/metrics.jsonl`` via ``knotica eval``. It runs against a git
clone of the vault at a pinned SHA -- never the live vault -- so evaluation has
no side effect on content and is reproducible.

Unlike the MCP server (client-as-brain: the server does no LLM work), the
evaluator is a distinct headless process that legitimately owns an
``ANTHROPIC_API_KEY`` -- a trust boundary local to this package (see
:mod:`knotica.evals.llm`). Importing this package is cheap: the heavy optional
dependencies (``anthropic``, ``dspy``) live in the ``evals`` dependency group
and are imported lazily by the modules that need them, so ``import
knotica.evals`` never forces the eval group onto an unrelated import path such
as the MCP cold start.

The package's public seam is the curated cross-subsystem surface -- the names
other subsystems (the ``knotica eval`` CLI, ``dspy.Evaluate``, the later
optimizer / SIA loops) depend on:

* :class:`~knotica.evals.llm.LLMClient` -- the injectable LLM boundary;
* :func:`~knotica.evals.scorer.build_metric` -- binds the triple-consumer
  ``score`` metric (there is no free-standing ``score``: the collaborators the
  metric needs are bound up front by this factory);
* :class:`~knotica.evals.runner.BaselineRunner` and
  :func:`~knotica.evals.program.BaselineProgram` -- the headless-query and
  DSPy-adapter seams;
* :func:`~knotica.evals.harness.run_eval` -- the orchestrator entry point the
  ``knotica eval`` CLI drives, where every seam above composes.

Concrete implementation and DI types (the Anthropic client, the test fake, the
message/usage dataclasses, the runner's ``Prediction``) are imported from their
defining submodule directly, keeping this surface to the cross-subsystem names
alone. Re-exporting these seams keeps
``import knotica.evals`` cheap: every one is defined in a module that imports
``anthropic``/``dspy`` lazily (or not at all), so the package import never forces
the ``evals`` dependency group onto an unrelated path such as the MCP cold start.
"""

from knotica.evals.harness import EvalRunResult, run_eval
from knotica.evals.llm import LLMClient
from knotica.evals.program import BaselineProgram
from knotica.evals.runner import BaselineRunner
from knotica.evals.scorer import build_metric

__all__ = [
    "BaselineProgram",
    "BaselineRunner",
    "EvalRunResult",
    "LLMClient",
    "build_metric",
    "run_eval",
]
