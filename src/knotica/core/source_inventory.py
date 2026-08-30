"""What sources the vault already holds -- the ingested-source identity set.

One read-only question, asked from the gap-fill drain: *is this candidate URL
already in the vault?* A field report answered it the expensive way -- fourteen
approved suggestions all pointing at one SEP entry that had been ingested weeks
earlier, because discovery never looked at what ``store_source`` had already
persisted. The stored sources under ``sources/<topic>/`` each carry an
``origin_url`` in their provenance frontmatter (mirrored as ``resource``), and
that URL, pushed through the same identity rule discovery dedups with, is the
vault's side of the handshake.

Identity is **URL-only** by construction: :class:`~knotica.core.records.SourceProvenance`
records no DOI, so the comparison key is the normalized URL on both sides
rather than :func:`~knotica.discovery.normalize.source_key` (whose DOI-first
branch would let a DOI-carrying candidate slip past a URL-recorded ingest of
the same source).

Reads are tolerant -- a malformed stored source is skipped, never fatal -- and
schemeless URLs (a hand-edited ``resource:`` without ``https://``) are keyed as
https rather than dropped. ``discovery.normalize`` is imported lazily inside
the function, per the rule that keeps ``discovery/`` off the MCP cold-start
import path.
"""

from __future__ import annotations

from knotica.core.page import parse_page
from knotica.store import VaultStore

__all__ = ["stored_source_url_keys"]

#: Vault-root directory holding immutably stored sources, one topic per child.
_SOURCES_DIR = "sources"
#: Provenance frontmatter fields that may carry the origin URL, in precedence
#: order (``resource`` mirrors ``origin_url`` on every record ``store_source``
#: writes, but a hand-authored source may carry only one).
_URL_FIELDS = ("origin_url", "resource")


def stored_source_url_keys(store: VaultStore, topic: str) -> frozenset[str]:
    """Normalized URL identities of every source stored under ``topic``.

    Empty when the topic has no stored sources. Chunked ingests share one
    ``origin_url`` across chunks, so many files can collapse to one identity --
    the set answers "which sources", not "how many files".
    """
    from knotica.discovery.normalize import normalize_url

    directory = f"{_SOURCES_DIR}/{topic}"
    if not store.exists(directory):
        return frozenset()
    keys: set[str] = set()
    for name in store.list_dir(directory):
        if not name.endswith(".md"):
            continue
        url = _origin_url(store, f"{directory}/{name}")
        if url is not None:
            keys.add(normalize_url(_with_scheme(url)))
    return frozenset(keys)


def _origin_url(store: VaultStore, path: str) -> str | None:
    """The origin URL one stored source declares, or ``None`` on any defect."""
    try:
        frontmatter, error, _body = parse_page(store.read_text(path))
    except (OSError, UnicodeDecodeError):
        return None
    if error is not None or frontmatter is None:
        return None
    for field in _URL_FIELDS:
        value = frontmatter.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _with_scheme(url: str) -> str:
    """Key a schemeless URL as https; the identity lowercases the scheme anyway."""
    return url if "://" in url else f"https://{url}"
