"""Deterministic, metadata-only reputability scoring.

A :class:`ReputabilityScorer` assigns each candidate a
:class:`~knotica.discovery.records.ReputabilityTier` and a composite ``[0, 1]``
score **purely from metadata** -- venue/domain tier, citation-count bucket, and
recency -- and never from ``title`` or ``snippet`` text. That makes the score
ungameable by prose and reproducible: the same candidate scored twice against the
same reference date yields a bit-identical :class:`~knotica.discovery.records.ReputabilityScore`.
There is no wall-clock read anywhere here; recency is measured against a
``reference_date`` the caller passes explicitly, so scoring is a pure function of
its inputs.

Classification is driven by a :class:`TierTable` -- a packaged allowlist for the
seed topic (agentic-systems: the top ML venues, arXiv, and recognized labs). The
table is a constructor argument (defaulting to :data:`DEFAULT_TIER_TABLE`) so a
future per-vault ``.knotica/reputability.toml`` override can supply its own without
touching this module (the root-defaults + earned-overrides seam).

Every weight, bucket threshold, and allowlist entry below is a *designed default*,
not a value dictated by an upstream spec -- the plan names the venues and the three
score inputs and requires determinism and metadata-only scoring; the concrete
numbers are chosen here and are overridable via the ``tier_table`` DI seam.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from urllib.parse import urlsplit

from knotica.discovery.records import ReputabilityScore, ReputabilityTier, SourceCandidate

__all__ = [
    "DEFAULT_TIER_TABLE",
    "ReputabilityScorer",
    "TierTable",
]

# -- score composition weights (sum to 1.0) ---------------------------------

#: Weight of the tier component in the composite score.
_TIER_WEIGHT = 0.6
#: Weight of the citation-count component.
_CITATION_WEIGHT = 0.25
#: Weight of the recency component.
_RECENCY_WEIGHT = 0.15

#: Base score per tier, in ``[0, 1]`` -- the tier component before weighting.
_TIER_BASE: dict[ReputabilityTier, float] = {
    ReputabilityTier.PEER_REVIEWED: 1.0,
    ReputabilityTier.PREPRINT_KNOWN_LAB: 0.7,
    ReputabilityTier.ESTABLISHED_ORG: 0.4,
    ReputabilityTier.GENERAL_WEB: 0.1,
}

#: Number of decimal places the composite score is rounded to -- keeps the float
#: stable and comparable across runs without exposing binary rounding noise.
_SCORE_PRECISION = 6


@dataclass(frozen=True, slots=True)
class TierTable:
    """A packaged reputability allowlist -- the classification seam.

    All entries are lowercase. Venue matching is token/substring based (an OpenAlex
    ``venue`` display name or a provider venue string); domain matching is host-suffix
    based (a host equals an entry or is a subdomain of it). Frozen and value-comparable
    so a scorer built from the same table scores identically.
    """

    #: Venue abbreviation tokens marking a peer-reviewed venue (matched against the
    #: alphanumeric tokens of a candidate's venue -- e.g. ``"neurips"`` matches
    #: ``"NeurIPS 2024"`` and ``"(ACL)"``).
    peer_reviewed_venue_markers: frozenset[str] = field(
        default_factory=lambda: frozenset({"neurips", "icml", "iclr", "acl", "emnlp"})
    )
    #: Long-form venue phrases marking a peer-reviewed venue (substring-matched
    #: against the whole normalized venue, for display names without the abbreviation).
    peer_reviewed_venue_phrases: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "neural information processing systems",
                "international conference on machine learning",
                "international conference on learning representations",
                "association for computational linguistics",
                "empirical methods in natural language processing",
            }
        )
    )
    #: Hosts (and their subdomains) that are preprint servers -> preprint/known-lab tier.
    preprint_domains: frozenset[str] = field(default_factory=lambda: frozenset({"arxiv.org"}))
    #: Venue tokens marking a preprint (e.g. ``"arxiv"`` in a venue string).
    preprint_venue_markers: frozenset[str] = field(default_factory=lambda: frozenset({"arxiv"}))
    #: Recognized lab/institution hosts -> preprint/known-lab tier.
    known_lab_domains: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "openai.com",
                "anthropic.com",
                "deepmind.com",
                "deepmind.google",
                "research.google",
                "ai.meta.com",
                "allenai.org",
                "stanford.edu",
                "berkeley.edu",
                "mit.edu",
                "cmu.edu",
            }
        )
    )
    #: Established organization / documentation hosts -> established-org tier.
    established_org_domains: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"wikipedia.org", "github.com", "readthedocs.io", "huggingface.co"}
        )
    )


#: The packaged default allowlist for the agentic-systems seed topic.
DEFAULT_TIER_TABLE = TierTable()


class ReputabilityScorer:
    """Assigns a deterministic tier + ``[0, 1]`` score from candidate metadata only.

    ``tier_table`` is the classification allowlist (defaulting to the packaged
    :data:`DEFAULT_TIER_TABLE`); pass a custom :class:`TierTable` to override
    classification without subclassing. :meth:`score` is a pure function of the
    candidate's metadata and the explicit ``reference_date`` -- no wall-clock, no
    randomness -- so identical inputs always produce an identical score.
    """

    def __init__(self, tier_table: TierTable = DEFAULT_TIER_TABLE) -> None:
        self._table = tier_table

    def score(self, candidate: SourceCandidate, *, reference_date: date) -> ReputabilityScore:
        """Return the deterministic :class:`ReputabilityScore` for ``candidate``.

        Tier comes from venue/domain classification; the composite score is the
        weighted sum of the tier base, the citation-count bucket, and the recency
        bucket (measured against ``reference_date``). ``title`` and ``snippet`` are
        never read, so mutating them cannot change the result.
        """
        tier, tier_signal = _classify_tier(candidate, self._table)
        citation_component, citation_signal = _citation_component(candidate.citation_count)
        recency_component, recency_signal = _recency_component(
            candidate.published_date, reference_date
        )
        composite = (
            _TIER_BASE[tier] * _TIER_WEIGHT
            + citation_component * _CITATION_WEIGHT
            + recency_component * _RECENCY_WEIGHT
        )
        return ReputabilityScore(
            tier=tier,
            score=round(min(max(composite, 0.0), 1.0), _SCORE_PRECISION),
            signals=(tier_signal, citation_signal, recency_signal),
        )

    def score_all(
        self, candidates: list[SourceCandidate], *, reference_date: date
    ) -> list[SourceCandidate]:
        """Stamp a fresh reputability score onto every candidate, preserving order.

        Returns new candidates (the records are frozen) with :attr:`reputability`
        populated; the input list is left untouched.
        """
        return [
            replace(candidate, reputability=self.score(candidate, reference_date=reference_date))
            for candidate in candidates
        ]


# ---------------------------------------------------------------------------
# Tier classification (metadata only -- venue then domain, highest tier first)
# ---------------------------------------------------------------------------


def _classify_tier(candidate: SourceCandidate, table: TierTable) -> tuple[ReputabilityTier, str]:
    """Return the highest-matching tier and a human-readable signal for it."""
    venue = candidate.venue
    if venue is not None and _is_peer_reviewed_venue(venue, table):
        return ReputabilityTier.PEER_REVIEWED, f"venue={venue}"

    host = _host(candidate.url)
    if venue is not None and _has_marker(venue, table.preprint_venue_markers):
        return ReputabilityTier.PREPRINT_KNOWN_LAB, f"venue={venue}"
    if _domain_matches(host, table.preprint_domains):
        return ReputabilityTier.PREPRINT_KNOWN_LAB, f"domain={host}"
    if _domain_matches(host, table.known_lab_domains):
        return ReputabilityTier.PREPRINT_KNOWN_LAB, f"domain={host}"
    if _domain_matches(host, table.established_org_domains):
        return ReputabilityTier.ESTABLISHED_ORG, f"domain={host}"
    return ReputabilityTier.GENERAL_WEB, f"domain={host}" if host else "domain=unknown"


def _is_peer_reviewed_venue(venue: str, table: TierTable) -> bool:
    """True when ``venue`` matches a peer-reviewed marker token or long-form phrase."""
    if _has_marker(venue, table.peer_reviewed_venue_markers):
        return True
    normalized = venue.lower()
    return any(phrase in normalized for phrase in table.peer_reviewed_venue_phrases)


def _has_marker(venue: str, markers: frozenset[str]) -> bool:
    """True when any marker appears as an alphanumeric token of ``venue``."""
    tokens = {token for token in _tokenize(venue) if token}
    return bool(tokens & markers)


def _tokenize(text: str) -> set[str]:
    """Split ``text`` into lowercase alphanumeric tokens (any other char is a boundary)."""
    tokens: set[str] = set()
    current: list[str] = []
    for char in text.lower():
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.add("".join(current))
            current = []
    if current:
        tokens.add("".join(current))
    return tokens


def _host(url: str) -> str:
    """Return the lowercase host of ``url`` with a leading ``www.`` stripped."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _domain_matches(host: str, domains: frozenset[str]) -> bool:
    """True when ``host`` equals a listed domain or is a subdomain of one."""
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in domains)


# ---------------------------------------------------------------------------
# Citation + recency buckets (deterministic step functions)
# ---------------------------------------------------------------------------

#: Ascending ``(inclusive_upper_bound, component)`` citation buckets; a count above
#: the last bound scores :data:`_CITATION_TOP`.
_CITATION_BUCKETS: tuple[tuple[int, float], ...] = (
    (0, 0.0),
    (9, 0.25),
    (49, 0.5),
    (199, 0.75),
)
_CITATION_TOP = 1.0

#: Ascending ``(inclusive_upper_bound_in_years, component)`` recency buckets; an age
#: beyond the last bound scores ``0.0``.
_RECENCY_BUCKETS: tuple[tuple[float, float], ...] = (
    (1.0, 1.0),
    (2.0, 0.8),
    (4.0, 0.5),
    (7.0, 0.25),
)
_DAYS_PER_YEAR = 365.25


def _citation_component(citation_count: int | None) -> tuple[float, str]:
    """Map a citation count to its bucket component and a signal.

    ``None`` (unknown) contributes nothing and is reported honestly as unknown --
    never conflated with zero citations.
    """
    if citation_count is None:
        return 0.0, "citations=unknown"
    for upper, component in _CITATION_BUCKETS:
        if citation_count <= upper:
            return component, f"citations={citation_count}"
    return _CITATION_TOP, f"citations={citation_count}"


def _recency_component(published_date: str | None, reference_date: date) -> tuple[float, str]:
    """Map an age (``reference_date`` minus ``published_date``) to its bucket component.

    An unknown or unparseable date contributes nothing; a future date is treated as
    most recent. The component is a step function of whole-year age, so it never
    reads a wall clock.
    """
    published = _parse_date(published_date)
    if published is None:
        return 0.0, "year=unknown"
    age_years = (reference_date - published).days / _DAYS_PER_YEAR
    signal = f"year={published.year}"
    if age_years <= 0.0:
        return 1.0, signal
    for upper, component in _RECENCY_BUCKETS:
        if age_years <= upper:
            return component, signal
    return 0.0, signal


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO-8601 date (``YYYY-MM-DD``, tolerating a trailing time), else None."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
