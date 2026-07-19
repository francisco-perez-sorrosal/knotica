"""Behavioral contract tests for ``DiscoveryService`` -- the composed facade.

The highest-value integration-level tests in this plan: fallback-chain
semantics, dedup, enrichment composition, and the deterministic total order
``(tier_rank, -score, url)``. Four behavior groups are under test:

- **composition order**: providers are tried in the configured order, and the
  first provider to yield at least one candidate wins -- later providers are
  never consulted once an earlier one succeeds;
- **fallback on hard failure**: a provider that raises is skipped, not fatal
  -- the next provider in the chain is tried; when every provider raises or
  returns nothing, ``discover`` returns an empty list, never an exception
  ;
- **dedup**: two candidates resolving to the same normalized DOI (or, absent
  a DOI, the same URL) collapse to one, keeping the richer-metadata record
  ;
- **the pre-mortem-mandated tie-break test**: two candidates sharing an
  identical ``(tier, score)`` but differing only in URL must sort
  URL-ascending, and that ordering must be stable across repeated
  invocations -- guarding against a `sorted()` call (or an earlier dict/set
  dedup step) silently depending on insertion order.

Every seam uses a real fake, never a mock of the unit under test:
``FakeSearchProvider`` (already landed in ``provider.py``) originates
candidates; a small local ``_FakeEnricher``/``_FakeScorer`` (this test's own
test doubles, not the unit under test) stand in for the enrichment and
scoring stages so the ranking tests aren't coupled to the real scorer's
metadata-driven tiering.

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.service`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.

**Interface note (negotiable):** the plan fixes the constructor as
``DiscoveryService(providers, enricher, scorer)`` and ``discover(query) ->
list[SourceCandidate]``. This suite's ``_FakeScorer.score`` accepts an
optional ``reference_date`` keyword (present or absent) so it stays
compatible regardless of whether the service passes one through -- if the
implementer's actual call signature differs further, reconcile the fake
rather than the service.
"""

from knotica.discovery.provider import FakeSearchProvider
from knotica.discovery.records import ReputabilityScore, ReputabilityTier, SearchQuery


def _service_module():
    import knotica.discovery.service as service

    return service


def _errors_module():
    import knotica.core.errors as errors

    return errors


def _candidate(**overrides):
    from knotica.discovery.records import SourceCandidate

    kwargs = {
        "url": "https://example.com/papers/gap-fill",
        "title": "Gap-Fill Discovery",
        "snippet": "A short abstract.",
        "source_provider": "fake",
    }
    kwargs.update(overrides)
    return SourceCandidate(**kwargs)


class _RaisingProvider:
    """A ``SearchProvider`` that always fails -- the missing-key/exhausted-
    retries case the fallback chain must skip past, never treat as fatal."""

    name = "raising"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: SearchQuery) -> list:
        self.calls += 1
        errors = _errors_module()
        raise errors.KnoticaError(errors.ErrorCode.SEARCH_API_ERROR, "provider unavailable")


class _FakeEnricher:
    """A passthrough enricher -- returns candidates unchanged, recording calls."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    def enrich(self, candidates: list) -> list:
        self.calls.append(list(candidates))
        return list(candidates)


class _FakeScorer:
    """Stamps a pre-assigned ``ReputabilityScore`` by URL -- gives tests full
    control over tier/score so the ranking tests aren't coupled to the real
    scorer's metadata-driven tiering. Implements ``score_all`` -- the landed
    ``DiscoveryService.discover`` calls ``self._scorer.score_all(enriched,
    reference_date=...)``, mirroring ``ReputabilityScorer``'s real interface."""

    def __init__(self, scores_by_url: dict[str, ReputabilityScore]) -> None:
        self._scores_by_url = scores_by_url

    def score_all(self, candidates, reference_date=None):
        from dataclasses import replace

        return [
            replace(candidate, reputability=self._scores_by_url[candidate.url])
            for candidate in candidates
        ]


def _score(tier: ReputabilityTier, value: float) -> ReputabilityScore:
    return ReputabilityScore(tier=tier, score=value, signals=())


# ---------------------------------------------------------------------------
# Composition order: providers tried in order, first non-empty wins
# ---------------------------------------------------------------------------


def test_the_first_provider_to_yield_candidates_wins_and_later_providers_are_not_consulted():
    service_module = _service_module()
    winning_candidate = _candidate(url="https://example.com/from-first")
    first = FakeSearchProvider([winning_candidate], name="first")
    second = FakeSearchProvider([_candidate(url="https://example.com/from-second")], name="second")
    scorer = _FakeScorer({winning_candidate.url: _score(ReputabilityTier.GENERAL_WEB, 0.5)})

    service = service_module.DiscoveryService(
        providers=[first, second], enricher=_FakeEnricher(), scorer=scorer
    )
    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert [c.url for c in results] == [winning_candidate.url]
    assert second.call_count == 0, "a later provider must not be consulted once an earlier one wins"


def test_an_empty_first_provider_falls_through_to_the_second():
    service_module = _service_module()
    winning_candidate = _candidate(url="https://example.com/from-second")
    first = FakeSearchProvider([], name="first")
    second = FakeSearchProvider([winning_candidate], name="second")
    scorer = _FakeScorer({winning_candidate.url: _score(ReputabilityTier.GENERAL_WEB, 0.5)})

    service = service_module.DiscoveryService(
        providers=[first, second], enricher=_FakeEnricher(), scorer=scorer
    )
    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert [c.url for c in results] == [winning_candidate.url]
    assert first.call_count == 1
    assert second.call_count == 1


# ---------------------------------------------------------------------------
# Fallback on hard failure: a raiser is skipped, never fatal
# ---------------------------------------------------------------------------


def test_a_raising_primary_provider_is_skipped_and_the_secondary_wins():
    service_module = _service_module()
    winning_candidate = _candidate(url="https://example.com/from-secondary")
    primary = _RaisingProvider()
    secondary = FakeSearchProvider([winning_candidate], name="secondary")
    scorer = _FakeScorer({winning_candidate.url: _score(ReputabilityTier.GENERAL_WEB, 0.5)})

    service = service_module.DiscoveryService(
        providers=[primary, secondary], enricher=_FakeEnricher(), scorer=scorer
    )
    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert [c.url for c in results] == [winning_candidate.url]
    assert primary.calls == 1, (
        "the raising provider must actually have been tried, not skipped a priori"
    )


def test_when_every_provider_fails_or_yields_nothing_discover_returns_an_empty_list_not_an_error():
    service_module = _service_module()
    first_raiser = _RaisingProvider()
    second_raiser = _RaisingProvider()
    empty_provider = FakeSearchProvider([], name="empty")
    scorer = _FakeScorer({})

    service = service_module.DiscoveryService(
        providers=[first_raiser, second_raiser, empty_provider],
        enricher=_FakeEnricher(),
        scorer=scorer,
    )

    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert results == []


# ---------------------------------------------------------------------------
# Dedup: same normalized DOI or same URL collapses, keeping richer metadata
#
# ---------------------------------------------------------------------------


def test_two_candidates_with_the_same_doi_collapse_keeping_the_richer_metadata_record():
    service_module = _service_module()
    thin = _candidate(
        url="https://example.com/thin-copy",
        doi="10.1234/abcde",
        source_provider="first",
    )
    rich = _candidate(
        url="https://example.com/rich-copy",
        doi="10.1234/abcde",
        source_provider="second",
        venue="NeurIPS",
        citation_count=142,
    )
    provider = FakeSearchProvider([thin, rich], name="single")
    scorer = _FakeScorer(
        {
            thin.url: _score(ReputabilityTier.GENERAL_WEB, 0.5),
            rich.url: _score(ReputabilityTier.GENERAL_WEB, 0.5),
        }
    )

    service = service_module.DiscoveryService(
        providers=[provider], enricher=_FakeEnricher(), scorer=scorer
    )
    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert len(results) == 1, "same-DOI candidates must collapse to one"
    assert results[0].url == rich.url, "the richer-metadata record must be kept, not the thin one"


def test_two_candidates_with_the_same_url_and_no_doi_collapse_to_one():
    service_module = _service_module()
    same_url = "https://example.com/duplicate-page"
    first = _candidate(url=same_url, source_provider="first")
    second = _candidate(url=same_url, source_provider="second", venue="arXiv")
    provider = FakeSearchProvider([first, second], name="single")
    scorer = _FakeScorer({same_url: _score(ReputabilityTier.GENERAL_WEB, 0.5)})

    service = service_module.DiscoveryService(
        providers=[provider], enricher=_FakeEnricher(), scorer=scorer
    )
    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert len(results) == 1, "the same URL from two providers must collapse to one candidate"


# ---------------------------------------------------------------------------
# The pre-mortem-mandated tie-break test: identical (tier, score), different
# URL -> URL-ascending order, stable across repeated invocations
# ---------------------------------------------------------------------------


def test_candidates_sharing_an_identical_tier_and_score_sort_url_ascending_and_stay_stable():
    """Pre-mortem guard 2: a `sorted()` call without an explicit key (or an
    earlier dict/set dedup step) can silently depend on insertion/hash order.
    This test constructs two candidates with a genuinely identical (tier,
    score) -- proven by using the exact same ``ReputabilityScore`` instance
    for both -- and different URLs, then asserts URL-ascending order holds
    identically across five repeated ``discover()`` calls."""
    service_module = _service_module()
    tied_score = _score(ReputabilityTier.PEER_REVIEWED, 0.9)
    candidate_z = _candidate(url="https://z-example.com/paper", title="Z paper")
    candidate_a = _candidate(url="https://a-example.com/paper", title="A paper")
    # Insertion order deliberately puts the higher-URL candidate first, so an
    # insertion-order-dependent sort would produce the wrong (non-ascending)
    # order and this test would catch it.
    provider = FakeSearchProvider([candidate_z, candidate_a], name="tied")
    scorer = _FakeScorer({candidate_z.url: tied_score, candidate_a.url: tied_score})

    service = service_module.DiscoveryService(
        providers=[provider], enricher=_FakeEnricher(), scorer=scorer
    )

    orderings = [
        [c.url for c in service.discover(SearchQuery(text="agentic gap-fill"))] for _ in range(5)
    ]

    expected = [candidate_a.url, candidate_z.url]
    assert all(ordering == expected for ordering in orderings), (
        f"expected stable URL-ascending order {expected} on every repeated call, got {orderings}"
    )


def test_total_order_ranks_higher_tier_above_higher_score_within_a_lower_tier():
    """Sanity check on the sort key precedence itself: tier dominates score --
    a GENERAL_WEB candidate with a higher raw score must still rank below a
    PEER_REVIEWED candidate with a lower score."""
    service_module = _service_module()
    peer_reviewed = _candidate(url="https://example.com/peer-reviewed")
    general_web = _candidate(url="https://example.com/general-web")
    provider = FakeSearchProvider([general_web, peer_reviewed], name="mixed")
    scorer = _FakeScorer(
        {
            peer_reviewed.url: _score(ReputabilityTier.PEER_REVIEWED, 0.1),
            general_web.url: _score(ReputabilityTier.GENERAL_WEB, 0.99),
        }
    )

    service = service_module.DiscoveryService(
        providers=[provider], enricher=_FakeEnricher(), scorer=scorer
    )
    results = service.discover(SearchQuery(text="agentic gap-fill"))

    assert [c.url for c in results] == [peer_reviewed.url, general_web.url]


# ---------------------------------------------------------------------------
# Enricher composition: the service actually invokes the configured enricher
# ---------------------------------------------------------------------------


def test_discover_invokes_the_configured_enricher_on_the_deduped_candidate_set():
    service_module = _service_module()
    candidate = _candidate()
    provider = FakeSearchProvider([candidate], name="single")
    enricher = _FakeEnricher()
    scorer = _FakeScorer({candidate.url: _score(ReputabilityTier.GENERAL_WEB, 0.5)})

    service = service_module.DiscoveryService(
        providers=[provider], enricher=enricher, scorer=scorer
    )
    service.discover(SearchQuery(text="agentic gap-fill"))

    assert len(enricher.calls) == 1, "the configured enricher must be invoked exactly once"
    assert [c.url for c in enricher.calls[0]] == [candidate.url]
