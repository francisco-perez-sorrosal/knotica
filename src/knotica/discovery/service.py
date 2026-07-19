"""``DiscoveryService`` -- the search -> dedup -> enrich -> score -> rank facade.

Composes every stage of the pipeline in the ``SYSTEMS_PLAN.md`` Interfaces
diagram behind one call, :meth:`DiscoveryService.discover`:

1. **search** -- try each configured :class:`~knotica.discovery.provider.SearchProvider`
   in order (Decision C -- first-non-empty-wins, skip-on-hard-failure): a
   provider that raises (missing key, exhausted retries, a hard 5xx) is
   skipped, not fatal; the first provider yielding at least one candidate
   wins; if every provider raises or returns nothing, ``discover`` returns
   ``[]`` -- never an error.
2. **dedup** -- by normalized DOI, falling back to normalized URL when no DOI
   is present, preferring the candidate with richer metadata when two
   providers surface the same source (REQ-09).
3. **enrich** -- via the configured :class:`~knotica.discovery.provider.Enricher`
   (optional; ``None`` skips this stage).
4. **score** -- via :class:`~knotica.discovery.reputability.ReputabilityScorer`.
5. **rank** -- a total, explicit sort key ``(tier_rank, -score, url)`` (REQ-10),
   so ordering never depends on dict/insertion order and repeated runs over
   the same input produce byte-identical ordering, including the tie-break
   for two candidates sharing an identical ``(tier, score)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from knotica.core.errors import KnoticaError
from knotica.discovery.provider import Enricher, SearchProvider
from knotica.discovery.records import ReputabilityTier, SearchQuery, SourceCandidate
from knotica.discovery.reputability import ReputabilityScorer

__all__ = ["DiscoveryService"]

#: Ascending rank per tier -- lower sorts first, so the highest tier (peer
#: reviewed) ranks ahead of everything else. A candidate with no reputability
#: yet (should not happen post-scoring, but guarded defensively) sorts last.
_TIER_RANK: dict[ReputabilityTier, int] = {
    ReputabilityTier.PEER_REVIEWED: 0,
    ReputabilityTier.PREPRINT_KNOWN_LAB: 1,
    ReputabilityTier.ESTABLISHED_ORG: 2,
    ReputabilityTier.GENERAL_WEB: 3,
}
_UNSCORED_TIER_RANK = len(_TIER_RANK)

#: The un-normalized DOI URL prefix a provider or enricher may still carry.
_DOI_URL_PREFIX = "https://doi.org/"


class DiscoveryService:
    """Runs the fallback chain and composes dedup/enrich/score/rank.

    ``reference_date`` is a zero-arg callable returning the date the scorer
    measures recency against (default: ``date.today``) -- injected so tests
    can pin a fixed date instead of the service reading a real wall clock.
    """

    def __init__(
        self,
        providers: Sequence[SearchProvider],
        enricher: Enricher | None,
        scorer: ReputabilityScorer,
        *,
        reference_date: Callable[[], date] = date.today,
    ) -> None:
        self._providers = list(providers)
        self._enricher = enricher
        self._scorer = scorer
        self._reference_date = reference_date

    def discover(self, query: SearchQuery) -> list[SourceCandidate]:
        """Run the full pipeline for ``query``; ``[]`` when nothing is found."""
        candidates = self._search(query)
        if not candidates:
            return []
        deduped = _dedup(candidates)
        enriched = deduped if self._enricher is None else self._enricher.enrich(deduped)
        scored = self._scorer.score_all(enriched, reference_date=self._reference_date())
        return _rank(scored)

    def _search(self, query: SearchQuery) -> list[SourceCandidate]:
        """First provider yielding >=1 candidate wins; a raiser is skipped."""
        for provider in self._providers:
            try:
                candidates = provider.search(query)
            except KnoticaError:
                continue
            if candidates:
                return candidates
        return []


# ---------------------------------------------------------------------------
# Dedup -- normalized DOI, falling back to normalized URL; richer record wins
# ---------------------------------------------------------------------------


def _dedup(candidates: Sequence[SourceCandidate]) -> list[SourceCandidate]:
    """Collapse same-source duplicates, keeping the richer-metadata record.

    Dedup key is the normalized DOI when present, else the normalized URL.
    First-seen order is preserved for the surviving keys; a later duplicate
    with more populated optional fields replaces the earlier one at its slot.
    """
    best_by_key: dict[str, SourceCandidate] = {}
    order: list[str] = []
    for candidate in candidates:
        key = _dedup_key(candidate)
        current = best_by_key.get(key)
        if current is None:
            order.append(key)
            best_by_key[key] = candidate
        elif _richness(candidate) > _richness(current):
            best_by_key[key] = candidate
    return [best_by_key[key] for key in order]


def _dedup_key(candidate: SourceCandidate) -> str:
    doi = _normalize_doi(candidate.doi)
    if doi is not None:
        return f"doi:{doi}"
    return f"url:{_normalize_url(candidate.url)}"


def _richness(candidate: SourceCandidate) -> int:
    """Count populated optional scholarly fields -- higher wins a dedup tie."""
    optional_fields = (
        candidate.authors,
        candidate.venue,
        candidate.published_date,
        candidate.doi,
        candidate.citation_count,
        candidate.is_open_access,
        candidate.fwci,
        candidate.provider_score,
    )
    return sum(field is not None for field in optional_fields)


def _normalize_doi(doi: str | None) -> str | None:
    """Bare, lowercase DOI for the dedup join key -- ``None``/empty stays ``None``."""
    if not doi:
        return None
    stripped = doi[len(_DOI_URL_PREFIX) :] if doi.lower().startswith(_DOI_URL_PREFIX) else doi
    return stripped.lower()


def _normalize_url(url: str) -> str:
    """Lowercase scheme/host, strip a trailing slash, drop any fragment."""
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


# ---------------------------------------------------------------------------
# Rank -- total deterministic order, explicit tie-break
# ---------------------------------------------------------------------------


def _rank(candidates: Sequence[SourceCandidate]) -> list[SourceCandidate]:
    return sorted(candidates, key=_sort_key)


def _sort_key(candidate: SourceCandidate) -> tuple[int, float, str]:
    """``(tier_rank, -score, url)`` -- an explicit key, never bare dict/set order."""
    reputability = candidate.reputability
    if reputability is None:
        return (_UNSCORED_TIER_RANK, 0.0, candidate.url)
    return (_TIER_RANK[reputability.tier], -reputability.score, candidate.url)
