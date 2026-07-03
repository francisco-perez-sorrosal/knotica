"""Wikilink graph -- extraction, resolution, outbound and inbound queries.

Pure functions over the :class:`~knotica.store.VaultStore` protocol: no git,
no locking, no config resolution.

Extraction honors the constitution's wikilink syntax (``[[target]]`` and
``[[target|alias]]``, ``.md`` omitted) and masks fenced code blocks and inline
code spans first -- the template deliberately shows wikilink *examples* inside
backticks, and those must not count as links.

Resolution follows the constitution's two rules, conservatively:

* a target containing ``/`` is a full vault path (the cross-topic form);
* a bare target resolves same-directory-first -- relative to the source
  page's own directory -- and nowhere else. There is no vault-wide basename
  fallback: the constitution requires the full path for anything outside the
  source's directory, so a bare link that misses same-dir is simply
  unresolved (a lint violation), never a guess.

``inbound_links`` computes backlinks by a linear scan of every page in the
vault on each call. At MVP scale (tens of pages) this is the right trade; a
persisted link index is the deliberate future seam if vaults grow past that.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from knotica.store import VaultStore

__all__ = [
    "Link",
    "WikiLink",
    "extract_wikilinks",
    "inbound_links",
    "iter_page_paths",
    "outbound_links",
    "resolve_target",
]

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MARKDOWN_SUFFIX = ".md"


@dataclass(frozen=True)
class WikiLink:
    """One raw wikilink occurrence in a page body (pre-resolution).

    ``target`` is the reference text before the ``|`` (alias) separator, with
    any ``#heading`` fragment stripped; ``line`` is 1-based; ``context`` is the
    stripped source line the link appears on.
    """

    target: str
    alias: str | None
    line: int
    context: str


@dataclass(frozen=True)
class Link:
    """One resolved link edge in the vault graph.

    ``source`` and ``target`` are vault-relative page paths (``target`` is the
    resolution *candidate*; ``resolved`` says whether it exists in the vault).
    ``raw_target`` preserves the reference as written inside ``[[ ]]``.
    """

    source: str
    target: str
    raw_target: str
    alias: str | None
    line: int
    context: str
    resolved: bool


def extract_wikilinks(text: str) -> list[WikiLink]:
    """Extract wikilinks from markdown ``text``, skipping code blocks and spans.

    Fenced code blocks (``` or ~~~) and inline code spans are masked before
    matching, so wikilink-shaped examples in code never count. Links whose
    target is empty after stripping an alias and a ``#`` fragment (pure
    heading self-references) are skipped.
    """
    links: list[WikiLink] = []
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        masked = _INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), line)
        for match in _WIKILINK_RE.finditer(masked):
            link = _parse_reference(match.group(1), line_number, line.strip())
            if link is not None:
                links.append(link)
    return links


def resolve_target(target: str, source_dir: str) -> str:
    """Resolve a wikilink ``target`` to a vault-relative ``.md`` path candidate.

    ``source_dir`` is the directory of the page containing the link
    (``""`` for a root page). A target containing ``/`` is vault-root
    relative; a bare target is same-directory only (see module docs).
    """
    if "/" in target:
        return target + _MARKDOWN_SUFFIX
    if source_dir:
        return f"{source_dir}/{target}{_MARKDOWN_SUFFIX}"
    return target + _MARKDOWN_SUFFIX


def outbound_links(store: VaultStore, page_path: str) -> list[Link]:
    """Return the links *from* the page at vault-relative ``page_path``.

    Raises ``FileNotFoundError`` (from the store) if the page is absent --
    callers own the not-found envelope.
    """
    text = store.read_text(page_path)
    source_dir = page_path.rsplit("/", 1)[0] if "/" in page_path else ""
    links: list[Link] = []
    for wikilink in extract_wikilinks(text):
        candidate = resolve_target(wikilink.target, source_dir)
        links.append(
            Link(
                source=page_path,
                target=candidate,
                raw_target=wikilink.target,
                alias=wikilink.alias,
                line=wikilink.line,
                context=wikilink.context,
                resolved=store.exists(candidate),
            )
        )
    return links


def inbound_links(store: VaultStore, page_path: str) -> list[Link]:
    """Return the backlinks *to* the page at vault-relative ``page_path``.

    Linear scan of every vault page on each call (fine at MVP scale; see
    module docs for the future index seam). The target page itself is not
    required to exist -- backlinks to a missing page are how dangling
    references get found.
    """
    backlinks: list[Link] = []
    for source_path in iter_page_paths(store):
        if source_path == page_path:
            continue
        backlinks.extend(
            link for link in outbound_links(store, source_path) if link.target == page_path
        )
    return backlinks


def iter_page_paths(store: VaultStore, directory: str = "") -> Iterator[str]:
    """Yield every ``.md`` page path in the vault, depth-first, sorted.

    Dot-prefixed entries (``.knotica``, ``.git``, ``.obsidian``, hidden files)
    are skipped -- they are never pages. Non-markdown files are ignored.
    """
    for name in store.list_dir(directory):
        if name.startswith("."):
            continue
        path = f"{directory}/{name}" if directory else name
        if name.endswith(_MARKDOWN_SUFFIX):
            yield path
            continue
        try:
            yield from iter_page_paths(store, path)
        except NotADirectoryError:
            continue  # non-markdown file: not a page, nothing to recurse into


def _parse_reference(reference: str, line_number: int, context: str) -> WikiLink | None:
    """Split one ``[[ ]]`` reference into target and alias; None when empty."""
    target, _, alias_part = reference.partition("|")
    alias = alias_part.strip() or None
    target = target.split("#", 1)[0].strip()
    if not target:
        return None
    return WikiLink(target=target, alias=alias, line=line_number, context=context)
