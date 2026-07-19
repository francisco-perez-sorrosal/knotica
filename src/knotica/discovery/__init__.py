"""Gap-fill discovery layer -- pluggable search providers + reputability scoring.

Given a search query, this package returns a deterministically-ranked list of
reputability-scored :class:`~knotica.discovery.records.SourceCandidate` records:
search adapters (Exa, you.com) originate candidates, a provider-agnostic OpenAlex
pass enriches them with scholarly metadata, and a metadata-only scorer tiers and
ranks them. There is **no LLM anywhere** in this package, and it stays **off the
MCP cold-start path** -- heavy HTTP clients are imported lazily at use time, never
at package import (see the import-boundary fitness test).

The public surface re-exported here is the frozen records, the two structural
protocols, the fake used to satisfy ``SearchProvider`` in tests, and the
composed :class:`~knotica.discovery.service.DiscoveryService` facade. Concrete
adapters (``YouComProvider``), the enricher (``OpenAlexEnricher``), the scorer
(``ReputabilityScorer``), and config resolution are imported from their own
submodules by whichever code composes a live ``DiscoveryService`` -- they are
deliberately not re-exported here, keeping this package's import-light surface
small. Keep the heavy-import isolation invariant: nothing imported at package
level may transitively pull ``httpx`` or any provider client (the import-boundary
fitness test asserts this).
"""

from knotica.discovery.provider import Enricher, FakeSearchProvider, SearchProvider
from knotica.discovery.records import (
    ReputabilityScore,
    ReputabilityTier,
    SearchQuery,
    SourceCandidate,
)
from knotica.discovery.service import DiscoveryService

__all__ = [
    "DiscoveryService",
    "Enricher",
    "FakeSearchProvider",
    "ReputabilityScore",
    "ReputabilityTier",
    "SearchProvider",
    "SearchQuery",
    "SourceCandidate",
]
