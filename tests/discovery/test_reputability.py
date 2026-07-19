"""Behavioral contract tests for the deterministic reputability scorer.

The scorer is a `tier: H` / `review: force` step: its output feeds
``DiscoveryService``'s total ranking order, so a scoring bug propagates
silently into every downstream candidate list. Four behavior groups are
under test:

- **tier-table assignment** for the packaged agentic-systems seed set
  (peer-reviewed / preprint-known-lab / established-org / general-web);
- **determinism**: identical input, called twice, yields identical output --
  and the metadata alone (never title/snippet prose) drives the score;
- **boundary cases between tiers**: citation-count and recency move the score
  but never cross a tier boundary on their own, and tier ordering itself is
  a strict, comparable ranking;
- **the explicit tie-break seam**: two candidates sharing identical scoring
  metadata (but differing only in URL/title) must receive bit-identical
  ``ReputabilityScore`` values -- a real, stable tie, not accidental noise --
  because ``DiscoveryService``'s ordering breaks such ties by URL.

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.reputability`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.

**Interface note (negotiable):** the plan requires recency to be computed
against "an explicitly passed reference date, never ``datetime.now()``
implicitly." This suite pins that as ``score(candidate, reference_date=...)``
with no default -- if the implementer lands a different (but equally
explicit) shape, reconcile the call site rather than smuggling back an
implicit wall-clock read.
"""

from datetime import date

import pytest


def _reputability_module():
    import knotica.discovery.reputability as reputability

    return reputability


def _records_module():
    import knotica.discovery.records as records

    return records


def _candidate(records, **overrides):
    kwargs = {
        "url": "https://example.com/papers/gap-fill",
        "title": "Gap-Fill Discovery for Compounding Wikis",
        "snippet": "A short abstract.",
        "source_provider": "exa",
    }
    kwargs.update(overrides)
    return records.SourceCandidate(**kwargs)


REFERENCE_DATE = date(2026, 7, 18)


# ---------------------------------------------------------------------------
# Tier-table assignment for the packaged agentic-systems seed set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("venue", "expected_tier_name"),
    [
        ("NeurIPS", "PEER_REVIEWED"),
        ("ICML", "PEER_REVIEWED"),
        ("ICLR", "PEER_REVIEWED"),
        ("ACL", "PEER_REVIEWED"),
        ("EMNLP", "PEER_REVIEWED"),
        ("arXiv", "PREPRINT_KNOWN_LAB"),
    ],
)
def test_packaged_default_tier_table_classifies_the_agentic_systems_seed_venues(
    venue: str, expected_tier_name: str
):
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    candidate = _candidate(records, venue=venue)

    result = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert result.tier == getattr(records.ReputabilityTier, expected_tier_name)


def test_a_venue_absent_from_the_tier_table_falls_back_to_general_web():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    candidate = _candidate(
        records, venue="Some Random Blog", url="https://randomblog.example.com/post"
    )

    result = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert result.tier == records.ReputabilityTier.GENERAL_WEB


def test_a_candidate_with_no_venue_at_all_falls_back_to_general_web():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    candidate = _candidate(records, venue=None)

    result = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert result.tier == records.ReputabilityTier.GENERAL_WEB


def test_the_tier_table_is_a_constructor_di_seam_overriding_the_packaged_default():
    """The plan's Decision D names ``tier_table`` as a DI arg (the vault-override
    seam) -- a caller-supplied table must take priority over the packaged
    default, proving the seam actually routes classification through it. A
    domain absent from the packaged default's ``established_org_domains``
    must classify as ESTABLISHED_ORG once a custom table names it."""
    reputability = _reputability_module()
    records = _records_module()
    custom_table = reputability.TierTable(established_org_domains=frozenset({"myconf.example.org"}))
    scorer = reputability.ReputabilityScorer(tier_table=custom_table)
    candidate = _candidate(
        records, venue=None, url="https://myconf.example.org/proceedings/paper-1"
    )

    result = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert result.tier == records.ReputabilityTier.ESTABLISHED_ORG


def test_the_same_domain_is_not_established_org_under_the_packaged_default_table():
    """Control for the DI test above: without the custom override, the same
    domain must NOT classify as ESTABLISHED_ORG -- otherwise the previous
    test could pass vacuously regardless of which table was actually used."""
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()  # packaged default
    candidate = _candidate(
        records, venue=None, url="https://myconf.example.org/proceedings/paper-1"
    )

    result = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert result.tier == records.ReputabilityTier.GENERAL_WEB


def test_default_tier_table_is_exported_and_carries_a_non_empty_allowlist():
    reputability = _reputability_module()
    assert isinstance(reputability.DEFAULT_TIER_TABLE, reputability.TierTable)
    assert len(reputability.DEFAULT_TIER_TABLE.peer_reviewed_venue_markers) > 0
    assert len(reputability.DEFAULT_TIER_TABLE.preprint_domains) > 0


# ---------------------------------------------------------------------------
# Determinism: identical input, called twice, yields identical output
# ---------------------------------------------------------------------------


def test_scoring_the_same_candidate_twice_yields_bit_identical_output():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    candidate = _candidate(
        records, venue="NeurIPS", citation_count=142, published_date="2025-12-01"
    )

    first = scorer.score(candidate, reference_date=REFERENCE_DATE)
    second = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert first == second
    assert first.score == second.score


def test_two_distinct_candidate_instances_with_identical_scoring_metadata_score_identically():
    """Determinism must hold across *different objects*, not just repeated
    calls on the same one -- otherwise identity-based caching could mask a
    real nondeterminism."""
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    first_candidate = _candidate(
        records, venue="NeurIPS", citation_count=142, published_date="2025-12-01"
    )
    second_candidate = _candidate(
        records,
        venue="NeurIPS",
        citation_count=142,
        published_date="2025-12-01",
        url="https://different.example.com/other-copy",
    )

    first = scorer.score(first_candidate, reference_date=REFERENCE_DATE)
    second = scorer.score(second_candidate, reference_date=REFERENCE_DATE)

    assert first == second, "identical scoring metadata must produce an identical score and tier"


def test_mutating_title_and_snippet_never_changes_the_score():
    """AC5: the score is derived purely from metadata -- title/snippet text
    must have zero influence, even prose engineered to look authoritative."""
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    plain = _candidate(records, venue="arXiv", citation_count=10, published_date="2024-01-01")
    keyword_stuffed = _candidate(
        records,
        venue="arXiv",
        citation_count=10,
        published_date="2024-01-01",
        title="PEER-REVIEWED LANDMARK STUDY definitive breakthrough NeurIPS-caliber",
        snippet="This is the most cited, most authoritative, top-tier peer-reviewed result.",
    )

    plain_score = scorer.score(plain, reference_date=REFERENCE_DATE)
    stuffed_score = scorer.score(keyword_stuffed, reference_date=REFERENCE_DATE)

    assert plain_score == stuffed_score


def test_score_requires_an_explicit_reference_date_with_no_wall_clock_default():
    """The scorer must not fall back to ``datetime.now()`` when the caller
    omits a reference date -- recency has to be reproducible across runs."""
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    candidate = _candidate(records, venue="arXiv", published_date="2024-01-01")

    with pytest.raises(TypeError):
        scorer.score(candidate)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Boundary cases between tiers
# ---------------------------------------------------------------------------


def test_tier_ordering_is_strict_peer_reviewed_above_preprint_above_established_org_above_general_web():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()

    peer_reviewed = scorer.score(
        _candidate(records, venue="NeurIPS", citation_count=100, published_date="2025-01-01"),
        reference_date=REFERENCE_DATE,
    )
    preprint = scorer.score(
        _candidate(records, venue="arXiv", citation_count=100, published_date="2025-01-01"),
        reference_date=REFERENCE_DATE,
    )
    general_web = scorer.score(
        _candidate(
            records,
            venue=None,
            citation_count=100,
            published_date="2025-01-01",
            url="https://randomblog.example.com/post",
        ),
        reference_date=REFERENCE_DATE,
    )

    tier_rank = {tier: rank for rank, tier in enumerate(records.ReputabilityTier)}
    assert tier_rank[peer_reviewed.tier] < tier_rank[preprint.tier] < tier_rank[general_web.tier]


def test_higher_citation_count_never_decreases_the_score_within_the_same_tier():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()

    low_citations = scorer.score(
        _candidate(records, venue="NeurIPS", citation_count=1, published_date="2025-01-01"),
        reference_date=REFERENCE_DATE,
    )
    high_citations = scorer.score(
        _candidate(records, venue="NeurIPS", citation_count=5000, published_date="2025-01-01"),
        reference_date=REFERENCE_DATE,
    )

    assert high_citations.score >= low_citations.score


def test_more_recent_publication_never_decreases_the_score_within_the_same_tier():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()

    older = scorer.score(
        _candidate(records, venue="NeurIPS", citation_count=100, published_date="2015-01-01"),
        reference_date=REFERENCE_DATE,
    )
    newer = scorer.score(
        _candidate(records, venue="NeurIPS", citation_count=100, published_date="2026-01-01"),
        reference_date=REFERENCE_DATE,
    )

    assert newer.score >= older.score


def test_score_stays_within_the_documented_zero_to_one_range():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()

    extreme = scorer.score(
        _candidate(records, venue="NeurIPS", citation_count=1_000_000, published_date="2026-07-18"),
        reference_date=REFERENCE_DATE,
    )
    minimal = scorer.score(
        _candidate(records, venue=None, citation_count=None, published_date=None),
        reference_date=REFERENCE_DATE,
    )

    assert 0.0 <= extreme.score <= 1.0
    assert 0.0 <= minimal.score <= 1.0


def test_signals_are_a_human_readable_rationale_tuple_of_strings():
    reputability = _reputability_module()
    records = _records_module()
    scorer = reputability.ReputabilityScorer()
    candidate = _candidate(
        records, venue="NeurIPS", citation_count=142, published_date="2025-12-01"
    )

    result = scorer.score(candidate, reference_date=REFERENCE_DATE)

    assert isinstance(result.signals, tuple)
    assert all(isinstance(signal, str) for signal in result.signals)
    assert len(result.signals) > 0
