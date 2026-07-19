"""Gap-fill discovery layer -- pluggable search providers + reputability scoring.

Given a search query, this package returns a deterministically-ranked list of
reputability-scored :class:`~knotica.discovery.records.SourceCandidate` records:
search adapters (Exa, you.com) originate candidates, a provider-agnostic OpenAlex
pass enriches them with scholarly metadata, and a metadata-only scorer tiers and
ranks them. There is **no LLM anywhere** in this package, and it stays **off the
MCP cold-start path** -- heavy HTTP clients are imported lazily at use time, never
at package import (see the import-boundary fitness test).

Only the frozen record contract is wired at import today; the provider protocol,
adapters, enricher, scorer, config, and service facade join ``__all__`` as later
steps land. Keep the heavy-import isolation invariant: nothing imported at package
level may transitively pull ``httpx`` or any provider client.
"""

from knotica.discovery.records import (
    ReputabilityScore,
    ReputabilityTier,
    SearchQuery,
    SourceCandidate,
)

__all__ = [
    "ReputabilityScore",
    "ReputabilityTier",
    "SearchQuery",
    "SourceCandidate",
]
