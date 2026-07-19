"""Frozen record shapes for the discovery layer -- the P3 consumer contract.

These dataclasses are the *frozen* interface P3 (the gap-fill classifier join,
suggestion queue, and approval surface) consumes. P3 reads a
:class:`SourceCandidate` **only** through :meth:`SourceCandidate.to_record` /
:meth:`SourceCandidate.from_record`, so the guarantees below are load-bearing
across the pipeline boundary and must not be broken silently:

1. **Self-contained record.** A candidate carries no gap/question/topic linkage;
   associating a candidate with the golden question(s) that motivated the query
   is P3's job. P2 answers "for THIS query, here are ranked candidates".
2. **``schema_version`` is present and starts at 1** (mirrors the ``core.records``
   additive-only convention). An additive field bumps nothing; a breaking shape
   change bumps ``schema_version`` and P3 branches on it. A v1 field is never
   removed or renamed.
3. **``None`` means "unknown", never "zero".** ``citation_count = None`` is not
   ``0``; ``is_open_access = None`` is not ``False``. Absence is unambiguous.
4. **``reputability`` may be ``None``** when no scorer has stamped the candidate;
   the service always returns scored candidates, but a raw provider read may not.
5. **``doi`` is stored as given at construction; normalized by pipeline exit.**
   This record stores whatever string it is handed, and the enricher normalizes
   to a bare ``10.xxxx/...`` DOI — so by the time ``DiscoveryService.discover()``
   returns, ``doi`` is the stable join key the consumer contract guarantees.
6. **Ordering is decided by the service**, not this module -- these are pure data.
7. **JSONL round-trip is lossless.** ``from_record(to_record(c)) == c`` holds for
   every candidate; ``to_record`` emits only JSON-native types (str/int/float/
   bool/None/list/dict) so a candidate serializes straight into a JSONL line.

Records here are pure data plus (de)serialization -- no network, no I/O, no
mutation. The dataclasses are frozen and slotted, matching the house style in
``core.records`` and ``evals.llm``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "SCHEMA_VERSION",
    "ReputabilityScore",
    "ReputabilityTier",
    "SearchQuery",
    "SourceCandidate",
]

#: Current ``schema_version`` of a :class:`SourceCandidate` record. Additive
#: field growth keeps this at 1; only a breaking shape change bumps it.
SCHEMA_VERSION = 1


class ReputabilityTier(StrEnum):
    """Coarse reputability class, highest to lowest -- assigned from metadata only."""

    PEER_REVIEWED = "peer_reviewed"  # NeurIPS/ICML/ICLR/ACL/EMNLP/... -- highest
    PREPRINT_KNOWN_LAB = "preprint_known_lab"  # arXiv + recognized lab/institution
    ESTABLISHED_ORG = "established_org"  # established org/docs domains
    GENERAL_WEB = "general_web"  # everything else -- lowest


@dataclass(frozen=True, slots=True)
class ReputabilityScore:
    """A deterministic reputability verdict stamped onto a candidate.

    ``score`` is a composite in ``[0, 1]`` derived purely from metadata;
    ``signals`` is a human-readable rationale (``("venue=NeurIPS",
    "citations=142", "year=2025")``) so a reviewer can see why the tier was
    assigned. Immutable and comparable by value.
    """

    tier: ReputabilityTier
    score: float
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A provider-agnostic search request.

    Only ``text`` is required; the rest are optional filters a provider may
    honor or ignore. ``min_citations`` is advisory -- providers may ignore it and
    only the scholarly enrichment pass enforces it. ``category`` is ``"paper"`` or
    ``"general"`` (adapters map ``"paper"`` to their own vocabulary).
    """

    text: str
    include_domains: tuple[str, ...] | None = None
    exclude_domains: tuple[str, ...] | None = None
    date_from: str | None = None  # ISO 8601 date
    date_to: str | None = None
    min_citations: int | None = None
    category: str | None = None
    max_results: int = 10


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """One ranked source produced by a search provider -- the frozen P3 record.

    The first four fields are universal (every provider returns them); the rest
    are optional scholarly metadata that is ``None`` until a provider or the
    enricher supplies it. ``reputability`` is ``None`` until the scorer stamps it.
    See the module docstring for the full P3 consumer contract.
    """

    # universal -- every provider returns these
    url: str
    title: str
    snippet: str
    source_provider: str  # which SearchProvider produced this ("exa" | "youcom")
    # scholarly metadata (None when unknown / un-enriched)
    authors: tuple[str, ...] | None = None
    venue: str | None = None
    published_date: str | None = None  # ISO 8601 date
    doi: str | None = None
    citation_count: int | None = None
    is_open_access: bool | None = None
    fwci: float | None = None  # field-weighted citation impact (OpenAlex)
    provider_score: float | None = None  # provider's own relevance score (not cross-comparable)
    reputability: ReputabilityScore | None = None
    schema_version: int = SCHEMA_VERSION

    def to_record(self) -> dict[str, object]:
        """Serialize to a JSONL-ready dict of JSON-native types only.

        Tuples become lists and the nested :class:`ReputabilityScore` becomes a
        nested object so the result serializes straight into a JSONL line;
        :meth:`from_record` inverts this losslessly.
        """
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "source_provider": self.source_provider,
            "authors": None if self.authors is None else list(self.authors),
            "venue": self.venue,
            "published_date": self.published_date,
            "doi": self.doi,
            "citation_count": self.citation_count,
            "is_open_access": self.is_open_access,
            "fwci": self.fwci,
            "provider_score": self.provider_score,
            "reputability": _reputability_to_record(self.reputability),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "SourceCandidate":
        """Rebuild a candidate from a :meth:`to_record` dict.

        Unknown extra fields are tolerated (forward-compatible with additive
        schema growth). Required universal fields must be present and correctly
        typed -- a boundary mismatch fails fast with a ``ValueError``.
        """
        return cls(
            url=_require_str(record, "url"),
            title=_require_str(record, "title"),
            snippet=_require_str(record, "snippet"),
            source_provider=_require_str(record, "source_provider"),
            authors=_optional_str_tuple(record, "authors"),
            venue=_optional_str(record, "venue"),
            published_date=_optional_str(record, "published_date"),
            doi=_optional_str(record, "doi"),
            citation_count=_optional_int(record, "citation_count"),
            is_open_access=_optional_bool(record, "is_open_access"),
            fwci=_optional_float(record, "fwci"),
            provider_score=_optional_float(record, "provider_score"),
            reputability=_reputability_from_record(record.get("reputability")),
            schema_version=_schema_version_from_record(record),
        )


# ---------------------------------------------------------------------------
# Nested reputability (de)serialization
# ---------------------------------------------------------------------------


def _schema_version_from_record(record: Mapping[str, object]) -> int:
    """Recover ``schema_version``, defaulting to the current version when absent."""
    if "schema_version" not in record:
        return SCHEMA_VERSION
    version = _optional_int(record, "schema_version")
    return SCHEMA_VERSION if version is None else version


def _reputability_to_record(score: ReputabilityScore | None) -> dict[str, object] | None:
    if score is None:
        return None
    return {
        "tier": score.tier.value,
        "score": score.score,
        "signals": list(score.signals),
    }


def _reputability_from_record(value: object) -> ReputabilityScore | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"reputability must be an object or null, got {value!r}")
    return ReputabilityScore(
        tier=ReputabilityTier(_require_str(value, "tier")),
        score=_require_float(value, "score"),
        signals=_optional_str_tuple(value, "signals") or (),
    )


# ---------------------------------------------------------------------------
# Boundary coercion helpers -- fail fast on a type mismatch, pass None through
# ---------------------------------------------------------------------------


def _require_str(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError(f"record field {key!r} must be a string, got {value!r}")
    return value


def _require_float(record: Mapping[str, object], key: str) -> float:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"record field {key!r} must be a number, got {value!r}")
    return float(value)


def _optional_str(record: Mapping[str, object], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"record field {key!r} must be a string or null, got {value!r}")
    return value


def _optional_int(record: Mapping[str, object], key: str) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"record field {key!r} must be an integer or null, got {value!r}")
    return value


def _optional_float(record: Mapping[str, object], key: str) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"record field {key!r} must be a number or null, got {value!r}")
    return float(value)


def _optional_bool(record: Mapping[str, object], key: str) -> bool | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"record field {key!r} must be a boolean or null, got {value!r}")
    return value


def _optional_str_tuple(record: Mapping[str, object], key: str) -> tuple[str, ...] | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"record field {key!r} must be an array of strings or null, got {value!r}")
    return tuple(value)
