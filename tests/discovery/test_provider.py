"""Behavioral contract tests for the ``SearchProvider`` protocol and its fake.

Pins two guarantees: a ``FakeSearchProvider`` satisfies ``SearchProvider``
structurally (no inheritance needed -- mirrors ``FakeLLMClient``'s convention),
and every candidate a provider returns is tagged with the provider's own name
so downstream consumers know which search engine originated it.

Production imports are deferred into a helper so collection succeeds while
``knotica.discovery.provider`` is still in flight (concurrent implementer).
This file was written without reading the implementer's code.
"""


def _provider_module():
    import knotica.discovery.provider as provider

    return provider


def _records_module():
    import knotica.discovery.records as records

    return records


def _minimal_query(records):
    return records.SearchQuery(text="agentic gap-fill discovery")


def _minimal_candidate(records, **overrides):
    kwargs = {
        "url": "https://example.com/papers/gap-fill",
        "title": "Gap-Fill Discovery for Compounding Wikis",
        "snippet": "A short abstract.",
        "source_provider": "fake",
    }
    kwargs.update(overrides)
    return records.SourceCandidate(**kwargs)


# ---------------------------------------------------------------------------
# Structural protocol conformance
# ---------------------------------------------------------------------------


def test_search_provider_is_runtime_checkable():
    """A caller must be able to ``isinstance()``-check a provider at runtime --
    that is the whole point of the protocol seam (DI without inheritance)."""
    provider = _provider_module()
    records = _records_module()
    candidates = [_minimal_candidate(records)]
    fake = provider.FakeSearchProvider(candidates=candidates)

    assert isinstance(fake, provider.SearchProvider)


def test_fake_search_provider_satisfies_the_protocol_without_inheriting_it():
    """Structural typing, not nominal: FakeSearchProvider must not subclass
    SearchProvider to satisfy it -- any object with the right shape qualifies."""
    provider = _provider_module()
    fake_bases = {base.__name__ for base in provider.FakeSearchProvider.__mro__}
    assert "SearchProvider" not in fake_bases


def test_fake_search_provider_exposes_a_name_attribute():
    provider = _provider_module()
    records = _records_module()
    fake = provider.FakeSearchProvider(candidates=[_minimal_candidate(records)], name="fake-exa")
    assert fake.name == "fake-exa"


# ---------------------------------------------------------------------------
# search() behavior: returns canned candidates, records the call
# ---------------------------------------------------------------------------


def test_search_returns_the_canned_candidates_unchanged():
    provider = _provider_module()
    records = _records_module()
    canned = [_minimal_candidate(records, url="https://a.example.com")]
    fake = provider.FakeSearchProvider(candidates=canned)

    result = fake.search(_minimal_query(records))

    assert result == canned


def test_search_records_the_query_it_was_called_with():
    """Call-recording is the fake's whole purpose in downstream fallback-chain
    and dedup tests -- a caller must be able to assert exactly what was asked."""
    provider = _provider_module()
    records = _records_module()
    fake = provider.FakeSearchProvider(candidates=[])
    query = _minimal_query(records)

    fake.search(query)

    assert fake.calls == [query]


def test_search_appends_to_calls_across_multiple_invocations():
    provider = _provider_module()
    records = _records_module()
    fake = provider.FakeSearchProvider(candidates=[])
    first_query = records.SearchQuery(text="first query")
    second_query = records.SearchQuery(text="second query")

    fake.search(first_query)
    fake.search(second_query)

    assert fake.calls == [first_query, second_query]


def test_search_never_mutates_the_canned_candidate_list_between_calls():
    """Two separate ``search()`` calls must each observe the same canned
    candidates -- the fake must not consume or drain its own list."""
    provider = _provider_module()
    records = _records_module()
    canned = [_minimal_candidate(records)]
    fake = provider.FakeSearchProvider(candidates=canned)

    first_result = fake.search(_minimal_query(records))
    second_result = fake.search(_minimal_query(records))

    assert first_result == second_result == canned


# ---------------------------------------------------------------------------
# REQ: candidates carry source_provider tagging the origin
# ---------------------------------------------------------------------------


def test_candidates_returned_by_a_provider_are_tagged_with_its_own_name():
    """A real adapter's parse function is expected to stamp source_provider with
    its own name; the fake demonstrates the contract every adapter must honor --
    a caller reading a candidate can always tell which provider produced it."""
    provider = _provider_module()
    records = _records_module()
    canned = [_minimal_candidate(records, source_provider="exa")]
    fake = provider.FakeSearchProvider(candidates=canned, name="exa")

    result = fake.search(_minimal_query(records))

    assert all(candidate.source_provider == "exa" for candidate in result)


# ---------------------------------------------------------------------------
# Enricher protocol stub (also introduced in this step per the plan)
# ---------------------------------------------------------------------------


def test_enricher_protocol_is_runtime_checkable():
    provider = _provider_module()

    class _StubEnricher:
        def enrich(self, candidates):
            return candidates

    assert isinstance(_StubEnricher(), provider.Enricher)


def test_object_missing_enrich_method_does_not_satisfy_enricher_protocol():
    provider = _provider_module()

    class _NotAnEnricher:
        pass

    assert not isinstance(_NotAnEnricher(), provider.Enricher)


def test_object_missing_search_method_does_not_satisfy_search_provider_protocol():
    provider = _provider_module()

    class _NotAProvider:
        name = "not-a-provider"

    assert not isinstance(_NotAProvider(), provider.SearchProvider)
