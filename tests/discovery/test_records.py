"""Behavioral contract tests for the frozen ``knotica.discovery.records`` schema.

This is P2's half of the frozen P3 consumer contract (``SYSTEMS_PLAN.md`` §
Interfaces / § P3 Consumer Contract, ADR ``dec-draft-f4584c2f``). Once P3 starts
consuming ``SourceCandidate`` via ``to_record()``/``from_record()``, changing
this shape is expensive — these tests pin the schema so a shape regression
fails loudly here, not silently downstream in P3.

Frozen guarantees under test:

- exact field inventory for ``SourceCandidate``, ``SearchQuery``, and
  ``ReputabilityScore`` (not "has fields" — the *exact* set, so an accidental
  add/drop/rename fails);
- ``schema_version`` is present and starts at ``1``;
- a missing optional field is ``None``, never a sentinel, `0`, or `False`
  (absence must stay distinguishable from a known zero/false value);
- the JSONL round-trip is lossless: ``from_record(to_record(c)) == c``;
- ``SourceCandidate`` and ``ReputabilityScore`` are frozen and slotted;
- ``doi`` is stored verbatim by the record — normalization is the OpenAlex
  enricher's job (a later step), never this module's.

Production imports are deferred into helpers so collection succeeds while
``knotica.discovery.records`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.
"""

import dataclasses
import json

import pytest

# ---------------------------------------------------------------------------
# The frozen contract constants (SYSTEMS_PLAN.md § Interfaces), mirrored here
# as the source of truth for the schema tests — never derived from the
# implementation.
# ---------------------------------------------------------------------------

SOURCE_CANDIDATE_FIELDS = frozenset(
    {
        # universal (every provider returns these)
        "url",
        "title",
        "snippet",
        "source_provider",
        # scholarly metadata (None when unknown / un-enriched)
        "authors",
        "venue",
        "published_date",
        "doi",
        "citation_count",
        "is_open_access",
        "fwci",
        "provider_score",
        "reputability",
        "schema_version",
    }
)

SEARCH_QUERY_FIELDS = frozenset(
    {
        "text",
        "include_domains",
        "exclude_domains",
        "date_from",
        "date_to",
        "min_citations",
        "category",
        "max_results",
    }
)

REPUTABILITY_SCORE_FIELDS = frozenset({"tier", "score", "signals"})

REPUTABILITY_TIER_VALUES = {
    "PEER_REVIEWED": "peer_reviewed",
    "PREPRINT_KNOWN_LAB": "preprint_known_lab",
    "ESTABLISHED_ORG": "established_org",
    "GENERAL_WEB": "general_web",
}

OPTIONAL_SOURCE_CANDIDATE_FIELDS = (
    "authors",
    "venue",
    "published_date",
    "doi",
    "citation_count",
    "is_open_access",
    "fwci",
    "provider_score",
    "reputability",
)


# ---------------------------------------------------------------------------
# Helpers (deferred import + minimal builders — no raw multi-arg constructors
# inline in test bodies)
# ---------------------------------------------------------------------------


def _records_module():
    import knotica.discovery.records as records

    return records


def _minimal_candidate(records, **overrides):
    """Universal-fields-only candidate; every optional field left at default."""
    kwargs = {
        "url": "https://example.com/papers/gap-fill",
        "title": "Gap-Fill Discovery for Compounding Wikis",
        "snippet": "A short abstract of the paper.",
        "source_provider": "exa",
    }
    kwargs.update(overrides)
    return records.SourceCandidate(**kwargs)


def _fully_populated_candidate(records):
    score = records.ReputabilityScore(
        tier=records.ReputabilityTier.PEER_REVIEWED,
        score=0.87,
        signals=("venue=NeurIPS", "citations=142", "year=2025"),
    )
    return records.SourceCandidate(
        url="https://example.com/papers/gap-fill",
        title="Gap-Fill Discovery for Compounding Wikis",
        snippet="A short abstract of the paper.",
        source_provider="exa",
        authors=("Ada Lovelace", "Alan Turing"),
        venue="NeurIPS",
        published_date="2025-12-01",
        doi="10.1234/abcd.5678",
        citation_count=142,
        is_open_access=True,
        fwci=3.2,
        provider_score=0.91,
        reputability=score,
    )


# ---------------------------------------------------------------------------
# Frozen / slotted structure
# ---------------------------------------------------------------------------


def test_source_candidate_is_frozen():
    records = _records_module()
    candidate = _minimal_candidate(records)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.url = "https://other.example.com"


def test_source_candidate_is_slotted():
    """Slotted instances carry no ``__dict__`` — the structural proof that
    ``slots=True`` was actually applied, not just declared in intent."""
    records = _records_module()
    candidate = _minimal_candidate(records)
    assert not hasattr(candidate, "__dict__")


def test_reputability_score_is_frozen():
    records = _records_module()
    score = records.ReputabilityScore(
        tier=records.ReputabilityTier.GENERAL_WEB, score=0.1, signals=()
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        score.score = 0.9


# ---------------------------------------------------------------------------
# Exact field inventory (not "has fields" — the frozen shape, verbatim)
# ---------------------------------------------------------------------------


def test_source_candidate_field_inventory_matches_the_frozen_contract():
    records = _records_module()
    field_names = {f.name for f in dataclasses.fields(records.SourceCandidate)}
    assert field_names == SOURCE_CANDIDATE_FIELDS, (
        "SourceCandidate's field set must match the P3 consumer contract "
        f"exactly — missing: {SOURCE_CANDIDATE_FIELDS - field_names}, "
        f"unexpected: {field_names - SOURCE_CANDIDATE_FIELDS}"
    )


def test_search_query_field_inventory_matches_the_frozen_contract():
    records = _records_module()
    field_names = {f.name for f in dataclasses.fields(records.SearchQuery)}
    assert field_names == SEARCH_QUERY_FIELDS, (
        "SearchQuery's field set must match the frozen contract exactly — "
        f"missing: {SEARCH_QUERY_FIELDS - field_names}, "
        f"unexpected: {field_names - SEARCH_QUERY_FIELDS}"
    )


def test_reputability_score_field_inventory_matches_the_frozen_contract():
    records = _records_module()
    field_names = {f.name for f in dataclasses.fields(records.ReputabilityScore)}
    assert field_names == REPUTABILITY_SCORE_FIELDS


def test_reputability_tier_values_match_the_four_member_vocabulary():
    records = _records_module()
    values = {member.name: member.value for member in records.ReputabilityTier}
    assert values == REPUTABILITY_TIER_VALUES


# ---------------------------------------------------------------------------
# schema_version (dec-006 precedent)
# ---------------------------------------------------------------------------


def test_schema_version_defaults_to_one():
    records = _records_module()
    candidate = _minimal_candidate(records)
    assert candidate.schema_version == 1


# ---------------------------------------------------------------------------
# Required universal fields vs. optional scholarly fields
# ---------------------------------------------------------------------------


def test_source_candidate_requires_the_four_universal_fields():
    records = _records_module()
    with pytest.raises(TypeError):
        records.SourceCandidate(url="https://example.com")  # missing title/snippet/source_provider


@pytest.mark.parametrize("field_name", OPTIONAL_SOURCE_CANDIDATE_FIELDS)
def test_omitted_optional_field_defaults_to_none(field_name: str):
    """A missing optional field is None — never a sentinel, 0, or empty string:
    absence must stay unambiguous for downstream consumers."""
    records = _records_module()
    candidate = _minimal_candidate(records)
    assert getattr(candidate, field_name) is None


def test_search_query_defaults_max_results_to_ten_and_the_rest_to_none():
    records = _records_module()
    query = records.SearchQuery(text="agentic gap-fill discovery")
    assert query.max_results == 10
    for field_name in (
        "include_domains",
        "exclude_domains",
        "date_from",
        "date_to",
        "min_citations",
        "category",
    ):
        assert getattr(query, field_name) is None


# ---------------------------------------------------------------------------
# None-means-unknown, never zero/false (P3 Consumer Contract guarantee #3)
# ---------------------------------------------------------------------------


def test_none_citation_count_stays_distinct_from_a_known_zero():
    records = _records_module()
    unknown = _minimal_candidate(records)
    known_zero = _minimal_candidate(records, citation_count=0)

    assert unknown.citation_count is None
    assert known_zero.citation_count == 0
    assert unknown.to_record()["citation_count"] is None
    assert known_zero.to_record()["citation_count"] == 0


def test_none_is_open_access_stays_distinct_from_a_known_false():
    records = _records_module()
    unknown = _minimal_candidate(records)
    known_closed = _minimal_candidate(records, is_open_access=False)

    assert unknown.is_open_access is None
    assert known_closed.is_open_access is False
    assert unknown.to_record()["is_open_access"] is None
    assert known_closed.to_record()["is_open_access"] is False


# ---------------------------------------------------------------------------
# doi is stored verbatim — normalization is NOT this module's job
# ---------------------------------------------------------------------------


def test_doi_field_stores_whatever_string_it_is_given_unnormalized():
    """Normalizing to a bare lowercase DOI is the OpenAlex enricher's job (a
    later step in this plan); the record itself is a dumb container."""
    records = _records_module()
    candidate = _minimal_candidate(records, doi="https://doi.org/10.1234/ABCD")
    assert candidate.doi == "https://doi.org/10.1234/ABCD"


# ---------------------------------------------------------------------------
# JSONL round-trip losslessness (P3 Consumer Contract guarantee #7)
# ---------------------------------------------------------------------------


def test_round_trip_is_lossless_for_a_fully_populated_candidate():
    records = _records_module()
    candidate = _fully_populated_candidate(records)
    round_tripped = records.SourceCandidate.from_record(candidate.to_record())
    assert round_tripped == candidate


def test_round_trip_is_lossless_for_a_minimal_candidate_with_every_optional_none():
    records = _records_module()
    candidate = _minimal_candidate(records)
    round_tripped = records.SourceCandidate.from_record(candidate.to_record())
    assert round_tripped == candidate


def test_to_record_output_is_json_serializable():
    """The 'JSONL-ready' guarantee: to_record()'s output must survive an
    actual json.dumps/json.loads round trip, not just be dict-shaped."""
    records = _records_module()
    candidate = _fully_populated_candidate(records)
    serialized = json.dumps(candidate.to_record())
    reloaded = json.loads(serialized)
    assert records.SourceCandidate.from_record(reloaded) == candidate
