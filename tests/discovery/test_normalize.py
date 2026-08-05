"""Behavioral spec for source-identity normalization.

A source's identity is its DOI when it has one and its URL otherwise, and that
rule was implemented three times: `discovery/service.py` deduped candidates with
it, `discovery/openalex.py` re-derived it as its enrichment join key, and
`core/gapfill.py` reached across a package boundary for the two private
normalizers so its suggestion-queue dedup "cannot drift from the service's own
dedup semantics" -- a comment that names the coupling exactly, and existed
because there was nowhere shared to put the rule.

These cases pin the rule itself, independent of who asks:

- **DOI normalization is prefix-and-case only.** The `https://doi.org/` prefix
  is stripped case-insensitively and the remainder lowercased. Absent and empty
  stay absent, so a candidate with no DOI falls through to its URL rather than
  keying on an empty string.
- **URL normalization is scheme/host case, trailing slashes, and fragment.** The
  query survives, because two URLs differing only in query are different
  sources; the fragment does not, because a fragment addresses a position
  within one source.
- **One key function, two call shapes.** The service holds a frozen
  `SourceCandidate`, the queue holds the opaque dict that keeps `core/records.py`
  free of any edge into `discovery/`. Both must produce the same key for the
  same source, which is the invariant the private-import coupling was
  protecting and which this module now makes structural.
"""

from __future__ import annotations

import pytest

from knotica.discovery.normalize import normalize_doi, normalize_url, source_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1234/ABC", "10.1234/abc"),
        ("HTTPS://DOI.ORG/10.1234/ABC", "10.1234/abc"),
        ("10.1234/ABC", "10.1234/abc"),
        ("10.1234/abc", "10.1234/abc"),
    ],
)
def test_normalize_doi_strips_the_prefix_case_insensitively_and_lowercases(
    raw: str, expected: str
) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("empty", [None, ""])
def test_normalize_doi_keeps_an_absent_doi_absent(empty: str | None) -> None:
    # Not "", which would key every DOI-less candidate to the same bucket.
    assert normalize_doi(empty) is None


def test_normalize_url_lowercases_scheme_and_host_but_not_the_path() -> None:
    # Host is case-insensitive per RFC 3986; the path is not, and two paths
    # differing in case can be two different pages.
    assert normalize_url("HTTPS://Example.COM/Path/To/Doc") == "https://example.com/Path/To/Doc"


def test_normalize_url_strips_every_trailing_slash_not_just_one() -> None:
    # `rstrip("/")` collapses a run. A single-slash case cannot tell the two
    # readings apart, so it is the run that pins the behavior.
    assert normalize_url("https://example.com/paper/") == "https://example.com/paper"
    assert normalize_url("https://example.com/paper///") == "https://example.com/paper"


def test_normalize_url_drops_the_fragment_but_keeps_the_query() -> None:
    # A fragment addresses a position inside one source; a query can select a
    # different one.
    assert normalize_url("https://example.com/s?id=7#results") == "https://example.com/s?id=7"


def test_source_key_prefers_the_doi_when_one_is_present() -> None:
    assert source_key("https://doi.org/10.1/A", "https://example.com/x") == "doi:10.1/a"


def test_source_key_falls_back_to_the_url_when_no_doi_is_present() -> None:
    assert source_key(None, "https://Example.com/x/") == "url:https://example.com/x"


def test_source_key_namespaces_the_two_kinds_so_they_cannot_collide() -> None:
    # Without the prefixes a DOI-keyed and a URL-keyed source could collide on
    # an identical string and silently dedup into one.
    assert source_key("10.1/a", "").startswith("doi:")
    assert source_key(None, "10.1/a").startswith("url:")


def test_a_candidate_with_neither_doi_nor_url_keys_to_the_bare_url_bucket() -> None:
    # Pinned as-is, not endorsed. The DOI branch guards this case -- an empty DOI
    # stays `None` rather than keying every DOI-less candidate together -- and the
    # URL branch has no equivalent guard, so malformed records sharing neither
    # field dedup into one bucket. Behavior is unchanged from before the rule was
    # extracted; this test exists so changing it has to be deliberate.
    assert source_key(None, "") == "url:"


@pytest.mark.parametrize(
    ("doi", "url"),
    [
        ("HTTPS://DOI.ORG/10.5555/Xyz", "https://example.com/paper/"),
        (None, "https://Example.com/x/"),
        ("", "https://example.com/only-url"),
    ],
)
def test_the_queues_opaque_dict_keys_a_source_exactly_as_the_record_does(
    doi: str | None, url: str
) -> None:
    """The invariant the cross-package private import used to protect by hand.

    `DiscoveryService` keys a frozen `SourceCandidate`; the suggestion queue keys
    the opaque dict `to_record()` produces. Extracting the *rule* into one place
    does not by itself pin the *field mapping* between those two shapes -- if
    `to_record()`'s key names ever drift from `_source_key`'s lookups, the rule
    stays single-source and the two callers still disagree. This crosses the
    boundary for real: a genuine record on one side, `gapfill`'s reader on the
    other.
    """
    from knotica.core.gapfill import _source_key
    from knotica.discovery.records import SourceCandidate

    candidate = SourceCandidate(url=url, title="t", snippet="s", source_provider="youcom", doi=doi)

    assert _source_key(candidate.to_record()) == source_key(candidate.doi, candidate.url)
