"""Source identity — when are two candidates the same source?

A source is identified by its DOI when it has one and by its URL otherwise.
That single rule decides three different questions in three different places,
which is why it lives here rather than beside any one of them:

* ``DiscoveryService`` collapses same-source duplicates returned by different
  providers (its dedup key).
* ``OpenAlexEnricher`` joins a candidate to the record it enriches from (its
  join key).
* ``core.gapfill`` dedups the suggestion queue, where the candidate arrives as
  an **opaque dict** — the shape that keeps ``core/records.py`` free of any
  import edge into ``discovery/``.

Those three agreed by hand before this module existed: ``_normalize_doi`` was
implemented twice, byte-identically, in ``service`` and ``openalex``, and
``core.gapfill`` reached across a package boundary for the two private
normalizers so its dedup "cannot drift from the service's own dedup semantics".
That comment was right about the requirement and wrong about the only way to
meet it — a shared leaf makes the agreement structural instead of a convention
three call sites have to keep remembering.

Deliberately a leaf: it imports nothing from ``knotica``, so ``discovery``'s
single inward edge (to ``core.errors``) is unaffected and ``core.gapfill`` can
import it under the same lazy-import rule that governs the rest of
``discovery``.

The two normalizations are narrow on purpose, because over-normalizing merges
sources that are genuinely different:

* **DOI** — prefix and case only. An absent or empty DOI stays absent rather
  than becoming ``""``, which would key every DOI-less candidate into one
  bucket.
* **URL** — scheme and host case, *every* trailing slash, and the fragment. The
  query survives (two URLs differing in query can be two sources); the fragment
  does not (it addresses a position *within* one source).
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

__all__ = ["normalize_doi", "normalize_url", "source_key"]

#: The un-normalized DOI URL prefix a provider or enricher may still carry.
_DOI_URL_PREFIX = "https://doi.org/"


def normalize_doi(doi: str | None) -> str | None:
    """Bare, lowercase DOI for the identity key -- ``None``/empty stays ``None``."""
    if not doi:
        return None
    stripped = doi[len(_DOI_URL_PREFIX) :] if doi.lower().startswith(_DOI_URL_PREFIX) else doi
    return stripped.lower()


def normalize_url(url: str) -> str:
    """Lowercase scheme/host, strip every trailing slash, drop any fragment.

    ``rstrip`` collapses a run, not one character: ``/paper///`` and ``/paper``
    are the same source, so they must reach the same key.
    """
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def source_key(doi: str | None, url: str) -> str:
    """The identity of one source: its normalized DOI, else its normalized URL.

    The ``doi:``/``url:`` prefixes are load-bearing, not decoration: without
    them a DOI-keyed and a URL-keyed source that happen to share a string would
    collide and silently dedup into one.

    Takes the two fields rather than a record, so the frozen ``SourceCandidate``
    and the suggestion queue's opaque dict reach the same rule without this
    module knowing either shape.
    """
    normalized_doi = normalize_doi(doi)
    if normalized_doi is not None:
        return f"doi:{normalized_doi}"
    return f"url:{normalize_url(url)}"
