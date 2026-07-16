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

The package's public seam is the injectable :class:`~knotica.evals.llm.LLMClient`
protocol. As later modules land, the curated cross-subsystem surface grows to
include ``score`` (the triple-consumer metric), ``run_eval`` (the orchestrator),
and the ``BaselineRunner`` / ``BaselineProgram`` seams. Concrete implementation
types (the Anthropic client, the test fake, the message/usage dataclasses) are
imported from :mod:`knotica.evals.llm` directly.
"""

from knotica.evals.llm import LLMClient

__all__ = [
    "LLMClient",
]
