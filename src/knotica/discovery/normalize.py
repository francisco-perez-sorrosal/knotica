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

**Host canonicalization** sits one narrow notch above those: a small table of
per-host rewrites for sites that publish the *same* source under many URLs.
The one rule today is the Stanford Encyclopedia of Philosophy, whose archive
editions (``/archives/<edition>/entries/<slug>``) are snapshots of one living
entry (``/entries/<slug>``) — a field report staged nine editions of one entry
as nine independent sources, ranked against each other. The archive segment is
matched case-insensitively because providers have emitted it with broken case
(``archIves``). Host rules feed both the identity (:func:`normalize_url`
canonicalizes first, so every consumer of :func:`source_key` collapses
editions — including previously staged records re-keyed at dedup time) and the
stored form (:func:`canonicalize_url` is what the discovery service rewrites a
candidate's own URL with, so what lands in the queue is the canonical,
reachable URL rather than a possibly-malformed edition permalink).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

__all__ = ["canonicalize_url", "is_http_url", "normalize_doi", "normalize_url", "source_key"]

#: The un-normalized DOI URL prefix a provider or enricher may still carry.
_DOI_URL_PREFIX = "https://doi.org/"

#: Hosts serving the Stanford Encyclopedia of Philosophy.
_SEP_HOSTS = frozenset({"plato.stanford.edu", "www.plato.stanford.edu"})
#: An SEP archive-edition path; the leading segment is matched case-insensitively
#: because provider payloads have carried it with broken case (``archIves``).
_SEP_ARCHIVE_RE = re.compile(r"^/archives/[^/]+(?P<entry>/entries/.+)$", re.IGNORECASE)


def normalize_doi(doi: str | None) -> str | None:
    """Bare, lowercase DOI for the identity key -- ``None``/empty stays ``None``."""
    if not doi:
        return None
    stripped = doi[len(_DOI_URL_PREFIX) :] if doi.lower().startswith(_DOI_URL_PREFIX) else doi
    return stripped.lower()


def canonicalize_url(url: str) -> str:
    """Rewrite ``url`` to its host's canonical form; unknown hosts pass through.

    Unlike :func:`normalize_url` this returns a *usable* URL, not an identity
    key: it is what the discovery service stores on the candidate itself, so a
    rewrite must produce something a reader can click. SEP archive editions
    become the living entry at the bare host over https (the canonical form SEP
    itself links); everything else — including SEP URLs that are not archive
    editions — is returned unchanged.
    """
    parsed = urlsplit(url)
    # ``hostname``, not ``netloc``: it is already lowercased and port-stripped,
    # so an explicit ``:443`` cannot smuggle an archive edition past the rule
    # (``reputability`` matches hosts the same way -- one host rule, not two).
    if (parsed.hostname or "") not in _SEP_HOSTS:
        return url
    match = _SEP_ARCHIVE_RE.match(parsed.path)
    if match is None:
        return url
    entry: str = match.group("entry")
    return urlunsplit(("https", "plato.stanford.edu", entry, parsed.query, ""))


def is_http_url(url: str) -> bool:
    """Whether ``url`` is a syntactically plausible web source (http/https + host).

    The floor a candidate must clear to be staged at all: a provider hit whose
    URL has no scheme, a non-web scheme, or an empty host is not a source
    anyone can ingest. Deliberately syntactic only — no reachability probe, so
    discovery stays deterministic and spends nothing beyond the search call.
    """
    parsed = urlsplit(url)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def normalize_url(url: str) -> str:
    """Canonicalize, then lowercase scheme/host, strip trailing slashes and fragment.

    ``rstrip`` collapses a run, not one character: ``/paper///`` and ``/paper``
    are the same source, so they must reach the same key. Canonicalization runs
    first so two archive editions of one SEP entry — or an edition and the
    living entry — reach the same identity key everywhere this rule is asked.
    """
    parsed = urlsplit(canonicalize_url(url))
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
