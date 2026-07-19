"""The search seam -- the ``SearchProvider`` / ``Enricher`` protocols + a fake.

Search adapters (Exa, you.com) originate :class:`~knotica.discovery.records.SourceCandidate`
records; a provider-agnostic enricher stamps scholarly metadata on the merged
set. Both are structural seams (``runtime_checkable`` :class:`typing.Protocol`),
mirroring the ``LLMClient`` / ``FakeLLMClient`` DI convention in ``evals.llm`` and
``store``'s ``VaultStore``: production code depends on the protocol, tests inject a
fake that satisfies it structurally with no inheritance and no network.

:class:`FakeSearchProvider` replays a canned candidate list and records every
:meth:`~FakeSearchProvider.search` call on :attr:`FakeSearchProvider.calls`, so a
test can assert the exact query a provider was driven with and the call count --
the same call-recording discipline :class:`~knotica.evals.llm.FakeLLMClient` uses.

This module is import-light on purpose: it pulls only ``records`` and the stdlib
``typing`` machinery, never ``httpx`` or any provider client, so importing it
never touches the MCP cold-start isolation boundary.
"""

from typing import Protocol, runtime_checkable

from knotica.discovery.records import SearchQuery, SourceCandidate

__all__ = [
    "Enricher",
    "FakeSearchProvider",
    "SearchProvider",
]


@runtime_checkable
class SearchProvider(Protocol):
    """Structural seam over a single ``search`` call -- one origin of candidates.

    An implementation exposes a ``name`` (the ``source_provider`` tag it stamps on
    every candidate it returns, e.g. ``"exa"``) and a :meth:`search` that maps one
    :class:`~knotica.discovery.records.SearchQuery` to a list of
    :class:`~knotica.discovery.records.SourceCandidate`s. Implementations satisfy
    this protocol structurally -- they do not inherit from it.
    """

    name: str

    def search(self, query: SearchQuery) -> list[SourceCandidate]:
        """Return the candidates this provider found for ``query``."""
        ...


@runtime_checkable
class Enricher(Protocol):
    """Structural seam over a provider-agnostic metadata pass over candidates.

    The default implementation (``OpenAlexEnricher``) stamps scholarly metadata
    (citation count, venue, open-access, FWCI, published date) onto every candidate
    with a resolvable DOI and returns the rest unchanged. Tests inject a fake that
    satisfies this protocol structurally.
    """

    def enrich(self, candidates: list[SourceCandidate]) -> list[SourceCandidate]:
        """Return ``candidates`` with scholarly metadata stamped where resolvable."""
        ...


class FakeSearchProvider:
    """A zero-network :class:`SearchProvider` that replays canned candidates.

    Construct with a ``name`` (the ``source_provider`` tag, defaulting to
    ``"fake"``) and the candidate list to replay for every :meth:`search` call.
    Every invocation is recorded on :attr:`calls`, so a test can assert the exact
    :class:`~knotica.discovery.records.SearchQuery` the provider was driven with and
    the call count. Satisfies :class:`SearchProvider` structurally (no inheritance),
    needs no third-party dependency, and never touches the network -- preserving the
    suite's zero-network discipline, exactly as :class:`~knotica.evals.llm.FakeLLMClient`
    does for the LLM seam.
    """

    def __init__(
        self,
        candidates: list[SourceCandidate] | None = None,
        *,
        name: str = "fake",
    ) -> None:
        self.name = name
        self._candidates: list[SourceCandidate] = list(candidates or [])
        self.calls: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[SourceCandidate]:
        """Record the query and return a fresh copy of the canned candidate list."""
        self.calls.append(query)
        return list(self._candidates)

    @property
    def call_count(self) -> int:
        """How many times :meth:`search` has been invoked."""
        return len(self.calls)
