"""MCP prompts -- the four operation prompts, static names + lazy vault bodies.

The four operation names (``ingest``/``query``/``lint``/``curate``) register
**statically** at server startup so ``prompts/list`` is answerable with zero
vault access -- the surface is discoverable even on an unconfigured host. Each
body resolves **lazily on every** ``prompts/get`` through the one shared
resolver :func:`knotica.core.prompts.get_prompt`, so editing a vault prompt file
takes effect on the next invocation (no restart, no cache) and both this surface
and the ``knotica prompt`` CLI serve byte-identical bodies.

Argument typing: prompt argument values arrive as strings over the protocol, so
every argument is string-typed with an empty-string default (keeping the
listing static and pre-config). An unconfigured invocation does not fail -- the
resolver returns setup-guidance text instead, so ``prompts/get`` always yields
actionable content. A malformed vault (READY but missing prompt defaults) raises
from the resolver and is deliberately *not* softened -- that is a real fault the
client must see.

Registration touches no vault: the decorators only record metadata, and the
body resolves when the client fetches the prompt.
"""

from mcp.server.fastmcp import FastMCP

from knotica.core.prompts import get_prompt

__all__ = ["register_prompts"]

# --- prompt descriptions (mirror the interface design's prompt surface) ---

_INGEST_DESCRIPTION = (
    "Ingest a source into the wiki: infer the topic, fetch and convert the source, store its "
    "provenance, and write the touched pages (updating the catalog atomically), then offer to "
    "save the ingest as a curated example."
)

_QUERY_DESCRIPTION = (
    "Answer a question from the wiki: resolve the topic, search and read the relevant pages, "
    "synthesize an answer with citations to the source pages, then offer to save it as a "
    "curated example."
)

_LINT_DESCRIPTION = (
    "Lint a topic (or the whole vault): run the deterministic mechanical checks, then review "
    "the schemas for semantic issues (contradictions, staleness) and report findings by severity."
)

_CURATE_DESCRIPTION = (
    "Save the last interaction as a curated example for a topic, recording the question, the "
    "pages used, the answer, and the verdict."
)


def register_prompts(mcp: FastMCP) -> None:
    """Register the four operation prompts on ``mcp``.

    Called once at server construction. Names + argument schemas register
    statically (no vault access); every body resolves lazily per ``prompts/get``
    via the shared :func:`knotica.core.prompts.get_prompt` resolver.
    """

    @mcp.prompt(name="ingest", description=_INGEST_DESCRIPTION)
    def ingest(source: str = "", topic: str = "") -> str:
        return get_prompt("ingest", topic).body

    @mcp.prompt(name="query", description=_QUERY_DESCRIPTION)
    def query(question: str = "", topic: str = "") -> str:
        return get_prompt("query", topic).body

    @mcp.prompt(name="lint", description=_LINT_DESCRIPTION)
    def lint(topic: str = "") -> str:
        return get_prompt("lint", topic).body

    @mcp.prompt(name="curate", description=_CURATE_DESCRIPTION)
    def curate(topic: str = "", verdict: str = "") -> str:
        return get_prompt("curate", topic).body
