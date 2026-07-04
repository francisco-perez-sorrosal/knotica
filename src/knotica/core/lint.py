"""Deterministic mechanical lint -- violations as data, never as errors.

Read-only pure functions over the :class:`~knotica.store.VaultStore` protocol:
no git, no locking, no config resolution, zero mutation. :func:`lint_vault`
runs every check over the whole vault (or one topic) and returns a list of
:class:`Violation` records -- an empty list means mechanically clean. A
non-empty list is a *successful* result; adapters carry it inside a success
envelope, never inside ``{"error": ...}``.

Scope is strictly the constitution's mechanically checkable rules: frontmatter
conformance on content pages, wikilink resolution (same-directory-only bare
links, dot-folder links, the bare-``[[SCHEMA]]`` ban from subdirectories),
reserved top-level names, root/overlay ``schema_version`` agreement, index
coverage, log-entry path existence, and orphaned pages. Semantic linting
(contradictions between claims, staleness) is deliberately absent -- that is
the client LLM's job, guided by the schemas (see the vault's lint operation
prompt).

Check ids (:class:`LintCheck`) are stable: downstream consumers count
violations per check, so ids never change meaning and the same vault always
yields the same violations in the same order.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from knotica.core.links import Link, iter_page_paths, outbound_links
from knotica.core.page import TopicNotFoundError, parse_page, validate_frontmatter
from knotica.core.schema import (
    ROOT_SCHEMA_PATH,
    SchemaLayer,
    overlay_path,
    read_root_schema,
    read_topic_overlay,
    validated_topic,
)
from knotica.store import PathOutsideVaultError, VaultStore

__all__ = [
    "INDEX_PATH",
    "LOG_PATH",
    "RESERVED_TOP_LEVEL_NAMES",
    "LintCheck",
    "Violation",
    "lint_vault",
]

#: Top-level names that may never be used as topic names (root constitution
#: § Reserved names). Single source of truth -- the ``create_topic`` guard
#: shares this constant.
RESERVED_TOP_LEVEL_NAMES: frozenset[str] = frozenset(
    {"sources", "index.md", "log.md", "SCHEMA.md", "START_HERE.md", ".knotica", ".git"}
)

#: Vault-relative path of the global catalog.
INDEX_PATH = "index.md"

#: Citation-key shape: author surname(s) + 4-digit year + optional tag/section
#: suffix (e.g. ``wang2024awm``, ``hu2025memory-s3-forms``) -- distinctive enough
#: to pick source citations out of prose without matching ordinary words.
_CITATION_KEY_RE = re.compile(r"[a-z][a-z]+\d{4}[a-z0-9-]*")

#: Vault-relative path of the append-only operation log.
LOG_PATH = "log.md"

#: The source store is the one reserved directory that legitimately exists at
#: the vault root -- it is reserved *because* it is claimed, never a violation.
_SOURCES_DIR = "sources"

#: Filename of a topic's schema overlay (exempt from content-page checks).
_OVERLAY_FILENAME = "SCHEMA.md"

#: YAML block-scalar indicators. The strict-subset parser silently mis-parses a
#: block-scalar opener whose continuation lines contain colons (they become
#: separate fields), so a field whose *value* is one of these tokens is the
#: telltale of a block scalar that escaped the parser's error paths.
_BLOCK_SCALAR_TOKENS: frozenset[str] = frozenset({"|", "|-", "|+", ">", ">-", ">+"})

#: One log-entry header, exactly as frozen by the constitution:
#: ``## [YYYY-MM-DD] <op> | <topic> | <title>``.
_LOG_ENTRY_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] (?P<op>[^|]+?) \| (?P<topic>[^|]+?) \| .+$")

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MARKDOWN_SUFFIX = ".md"


class LintCheck(StrEnum):
    """Stable ids for the mechanical checks -- consumers count and branch on these."""

    FRONTMATTER_MISSING = "frontmatter-missing"
    FRONTMATTER_MALFORMED = "frontmatter-malformed"
    FRONTMATTER_FIELD = "frontmatter-field"
    FRONTMATTER_BLOCK_SCALAR = "frontmatter-block-scalar"
    LINK_UNRESOLVED = "link-unresolved"
    LINK_DOT_PATH = "link-dot-path"
    LINK_BARE_SCHEMA = "link-bare-schema"
    RESERVED_TOP_LEVEL_NAME = "reserved-top-level-name"
    SCHEMA_VERSION_MISSING = "schema-version-missing"
    OVERLAY_VERSION_CONFLICT = "overlay-version-conflict"
    INDEX_MISSING_ENTRY = "index-missing-entry"
    LOG_MISSING_PATH = "log-missing-path"
    PAGE_ORPHANED = "page-orphaned"
    CITATION_UNRESOLVED = "citation-unresolved"


@dataclass(frozen=True)
class Violation:
    """One mechanical lint finding, as data.

    ``check`` is the stable id; ``path`` is the vault-relative file the
    violation lives in (for reserved names, the offending top-level entry);
    ``line`` is 1-based where a specific line is known, else ``None``.
    ``message`` states what is wrong and why; ``fix`` is the concrete next
    action -- the same grammar as the error contract, riding on success.
    """

    check: LintCheck
    path: str
    message: str
    fix: str
    line: int | None = None

    def render(self) -> dict[str, object]:
        """Render as the plain-dict shape carried in the tool result."""
        return {
            "check": self.check.value,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "fix": self.fix,
        }


def lint_vault(store: VaultStore, topic: str = "") -> list[Violation]:
    """Run every mechanical check over the vault (or one topic); findings as data.

    An empty ``topic`` lints the whole vault. A named topic scopes page-level
    checks to that topic's pages and log checks to that topic's entries --
    though the link graph is always computed vault-wide, because index lines
    and cross-topic backlinks live outside the topic directory.

    Raises :class:`~knotica.core.page.TopicNotFoundError` for a missing or
    reserved ``topic`` and the typed ``NOT_CONFIGURED`` error (via schema
    resolution) for a vault with no root constitution. Never mutates and never
    takes the vault lock -- lint is a pure read.
    """
    scope = _validated_scope(store, topic)
    root = read_root_schema(store)
    topics = [scope] if scope else _topic_directories(store)
    vault_links = _vault_link_map(store)
    content_pages = [path for path in _content_page_paths(store, topics) if path in vault_links]
    scoped_pages = [path for path in vault_links if scope is None or _in_topic(path, scope)]

    violations: list[Violation] = []
    if scope is None:
        violations.extend(_check_reserved_names(store))
    violations.extend(_check_frontmatter(store, content_pages))
    violations.extend(_check_source_citations(store, content_pages))
    for page in scoped_pages:
        violations.extend(_check_page_links(page, vault_links[page]))
    violations.extend(_check_schema_layers(store, root, topics))
    violations.extend(_check_index(content_pages, vault_links))
    violations.extend(_check_orphans(content_pages, vault_links))
    violations.extend(_check_log(store, scope))
    return violations


def _vault_link_map(store: VaultStore) -> dict[str, list[Link]]:
    """Outbound links for every readable page in the vault.

    A ``.md``-named *directory* is yielded by the page walk but is not a page;
    it is skipped here (and flagged by the reserved-name check when its name
    is reserved) so lint never crashes on the malformed shapes it exists to
    report.
    """
    vault_links: dict[str, list[Link]] = {}
    for path in iter_page_paths(store):
        try:
            vault_links[path] = outbound_links(store, path)
        except IsADirectoryError:
            continue
    return vault_links


def _validated_scope(store: VaultStore, topic: str) -> str | None:
    """Return the validated topic scope, or ``None`` for a whole-vault lint."""
    if not topic.strip():
        return None
    cleaned = validated_topic(topic)
    if cleaned in RESERVED_TOP_LEVEL_NAMES or not store.exists(cleaned):
        raise TopicNotFoundError(cleaned)
    return cleaned


def _topic_directories(store: VaultStore) -> list[str]:
    """Visible top-level directories that are topics (reserved names excluded)."""
    return [
        name
        for name in store.list_dir("")
        if not name.startswith(".")
        and name not in RESERVED_TOP_LEVEL_NAMES
        and _is_directory(store, name)
    ]


def _content_page_paths(store: VaultStore, topics: Iterable[str]) -> list[str]:
    """Content pages: every page under a topic directory except its schema overlay."""
    pages: list[str] = []
    for topic in topics:
        overlay = f"{topic}/{_OVERLAY_FILENAME}"
        pages.extend(path for path in iter_page_paths(store, topic) if path != overlay)
    return pages


def _check_reserved_names(store: VaultStore) -> list[Violation]:
    """Flag visible top-level directories claiming a reserved name.

    ``sources`` is exempt: it is reserved *for* the source store, so its
    presence is the sanctioned use, not a misuse.
    """
    return [
        Violation(
            check=LintCheck.RESERVED_TOP_LEVEL_NAME,
            path=name,
            message=(
                f"Top-level directory '{name}' uses a reserved name -- reserved"
                " names can never be topic directories."
            ),
            fix="Rename the directory to a non-reserved kebab-case topic name.",
        )
        for name in store.list_dir("")
        if not name.startswith(".")
        and name in RESERVED_TOP_LEVEL_NAMES
        and name != _SOURCES_DIR
        and _is_directory(store, name)
    ]


def _check_frontmatter(store: VaultStore, content_pages: Iterable[str]) -> list[Violation]:
    """Validate every content page's frontmatter against the core field set."""
    violations: list[Violation] = []
    for path in content_pages:
        frontmatter, error, _body = parse_page(store.read_text(path))
        if error is not None:
            violations.append(
                Violation(
                    check=LintCheck.FRONTMATTER_MALFORMED,
                    path=path,
                    message=f"Frontmatter does not parse because {error}",
                    fix=(
                        "Rewrite the frontmatter using only 'key: value' scalars,"
                        " flow lists ([a, b]), or '- item' block lists."
                    ),
                )
            )
            continue
        if frontmatter is None:
            violations.append(
                Violation(
                    check=LintCheck.FRONTMATTER_MISSING,
                    path=path,
                    message=(
                        "Content page has no frontmatter block, but every content"
                        " page must carry the core fields."
                    ),
                    fix="Add the core frontmatter block defined in root SCHEMA.md.",
                )
            )
            continue
        violations.extend(_block_scalar_violations(path, frontmatter))
        violations.extend(
            Violation(
                check=LintCheck.FRONTMATTER_FIELD,
                path=path,
                message=f"Frontmatter field '{problem.field}' {problem.problem}.",
                fix=f"Fix '{problem.field}' to match root SCHEMA.md's core frontmatter table.",
            )
            for problem in validate_frontmatter(frontmatter)
        )
    return violations


def _check_source_citations(store: VaultStore, content_pages: Iterable[str]) -> list[Violation]:
    """Every source a page cites must be stored under ``sources/<topic>/``.

    Catches a page that outruns its evidence -- a claim citing a source (or a
    section chunk) that was never stored, so the citation resolves to nothing a
    reader can verify. The cited keys are the page's declared ``sources``
    frontmatter plus any citation-key-shaped tokens in the body; each must
    resolve to a stored ``sources/<topic>/<key>.md`` under the page's own topic.
    """
    violations: list[Violation] = []
    for path in content_pages:
        topic = path.split("/", 1)[0]
        frontmatter, error, body = parse_page(store.read_text(path))
        if error is not None or frontmatter is None:
            continue  # a malformed/absent frontmatter is a separate check's finding
        for key in sorted(_cited_source_keys(frontmatter, body)):
            source_path = f"{_SOURCES_DIR}/{topic}/{key}.md"
            if store.exists(source_path):
                continue
            violations.append(
                Violation(
                    check=LintCheck.CITATION_UNRESOLVED,
                    path=path,
                    message=(
                        f"Page cites source '{key}' but no stored source exists at "
                        f"{source_path} -- the claim cannot be verified against the vault."
                    ),
                    fix=(
                        f"Store the source before citing it (store_source with citation_key "
                        f"'{key}'), or correct the citation to a stored source. For a long "
                        "paper, store each cited section as its own chunk."
                    ),
                )
            )
    return violations


def _cited_source_keys(frontmatter: Mapping[str, object], body: str) -> set[str]:
    """Citation keys a page references: declared ``sources`` plus inline tokens."""
    keys: set[str] = set()
    declared = frontmatter.get("sources")
    if isinstance(declared, list):
        keys.update(str(item).strip() for item in declared if str(item).strip())
    keys.update(_CITATION_KEY_RE.findall(body or ""))
    return keys


def _block_scalar_violations(path: str, frontmatter: dict[str, object]) -> list[Violation]:
    """Flag values that are block-scalar indicators (mis-parsed block scalars)."""
    violations: list[Violation] = []
    for key, value in frontmatter.items():
        items = value if isinstance(value, list) else [value]
        if any(isinstance(item, str) and item in _BLOCK_SCALAR_TOKENS for item in items):
            violations.append(
                Violation(
                    check=LintCheck.FRONTMATTER_BLOCK_SCALAR,
                    path=path,
                    message=(
                        f"Frontmatter field '{key}' holds a block-scalar indicator"
                        f" ({value!r}) because the page uses a YAML block scalar,"
                        " which is outside the vault's strict subset and is"
                        " mis-parsed rather than rejected."
                    ),
                    fix="Replace the block scalar with a quoted single-line scalar.",
                )
            )
    return violations


def _check_page_links(page: str, links: Iterable[Link]) -> list[Violation]:
    """Flag dot-folder links, bare-``[[SCHEMA]]`` links, and unresolved links.

    The three shapes are mutually exclusive per link, in that precedence order:
    a dot-path or bare-``SCHEMA`` link gets its specific violation (whose fix
    also resolves any resolution failure), so a single bad link never produces
    two findings.
    """
    in_subdirectory = "/" in page
    violations: list[Violation] = []
    for link in links:
        if _has_dot_segment(link.target):
            violations.append(_dot_path_violation(page, link))
        elif in_subdirectory and link.raw_target == "SCHEMA":
            violations.append(_bare_schema_violation(page, link))
        elif not link.resolved:
            violations.append(_unresolved_violation(page, link))
    return violations


def _dot_path_violation(page: str, link: Link) -> Violation:
    return Violation(
        check=LintCheck.LINK_DOT_PATH,
        path=page,
        line=link.line,
        message=(
            f"Wikilink [[{link.raw_target}]] points into a dot-folder, which"
            " Obsidian hard-ignores -- the link renders broken and hides the"
            " target from the reader."
        ),
        fix="Move the target out of the dot-folder or remove the link.",
    )


def _bare_schema_violation(page: str, link: Link) -> Violation:
    topic = page.split("/", 1)[0]
    return Violation(
        check=LintCheck.LINK_BARE_SCHEMA,
        path=page,
        line=link.line,
        message=(
            "Wikilink [[SCHEMA]] uses the bare form, which is ambiguous between"
            " the root constitution and a topic overlay -- SCHEMA files must be"
            " linked by full vault path."
        ),
        fix=f"Link the overlay as [[{topic}/SCHEMA]] or the constitution as [[SCHEMA]] full-path"
        " from a root page.",
    )


def _unresolved_violation(page: str, link: Link) -> Violation:
    hint = (
        " Bare links resolve only within the same directory; use the full"
        " vault path for anything else."
        if "/" not in link.raw_target
        else ""
    )
    return Violation(
        check=LintCheck.LINK_UNRESOLVED,
        path=page,
        line=link.line,
        message=(
            f"Wikilink [[{link.raw_target}]] does not resolve because no page"
            f" exists at '{link.target}'.{hint}"
        ),
        fix="Fix the target path, create the target page, or remove the link.",
    )


def _check_schema_layers(
    store: VaultStore, root: SchemaLayer, topics: Iterable[str]
) -> list[Violation]:
    """Check root/overlay ``schema_version`` presence and agreement.

    A version mismatch is the one *mechanically* detectable root/overlay
    contradiction: the layers must be at the same constitution version.
    Prose-level contradictions are the client's semantic pass.
    """
    violations: list[Violation] = []
    if root.schema_version is None:
        violations.append(_missing_version_violation(ROOT_SCHEMA_PATH))
    for topic in topics:
        overlay = read_topic_overlay(store, topic)
        if overlay is None:
            continue  # no overlay is a normal state -- divergence is earned
        if overlay.schema_version is None:
            violations.append(_missing_version_violation(overlay_path(topic)))
        elif root.schema_version is not None and overlay.schema_version != root.schema_version:
            violations.append(
                Violation(
                    check=LintCheck.OVERLAY_VERSION_CONFLICT,
                    path=overlay_path(topic),
                    message=(
                        f"Overlay schema_version {overlay.schema_version} contradicts"
                        f" the root constitution's {root.schema_version} -- overlays"
                        " extend the root and never contradict it."
                    ),
                    fix="Run `knotica migrate` to bring the overlay to the root's schema_version.",
                )
            )
    return violations


def _missing_version_violation(path: str) -> Violation:
    return Violation(
        check=LintCheck.SCHEMA_VERSION_MISSING,
        path=path,
        message=(
            "Schema file carries no integer 'schema_version' frontmatter, so its"
            " constitution version cannot be checked."
        ),
        fix="Add 'schema_version: <integer>' frontmatter to the schema file.",
    )


def _check_index(
    content_pages: Iterable[str], vault_links: dict[str, list[Link]]
) -> list[Violation]:
    """Every content page must have a full-path catalog line in ``index.md``.

    Stale index lines pointing at deleted pages are already covered by the
    unresolved-link check (``index.md`` is a page like any other).
    """
    indexed = {link.target for link in vault_links.get(INDEX_PATH, ())}
    return [
        Violation(
            check=LintCheck.INDEX_MISSING_ENTRY,
            path=path,
            message=(
                f"Content page '{path}' has no catalog line in {INDEX_PATH},"
                " but every content page must be indexed."
            ),
            fix="Call write_page on the page with an index_entry to add its catalog line.",
        )
        for path in content_pages
        if path not in indexed
    ]


def _check_orphans(
    content_pages: Iterable[str], vault_links: dict[str, list[Link]]
) -> list[Violation]:
    """Flag content pages no other page links to (self-links do not count)."""
    inbound_targets = {
        link.target
        for source, links in vault_links.items()
        for link in links
        if link.target != source
    }
    return [
        Violation(
            check=LintCheck.PAGE_ORPHANED,
            path=path,
            message=(
                f"Content page '{path}' has no inbound wikilinks -- nothing in"
                " the vault leads a reader to it."
            ),
            fix="Link the page from a related page and give it an index catalog line.",
        )
        for path in content_pages
        if path not in inbound_targets
    ]


def _check_log(store: VaultStore, scope: str | None) -> list[Violation]:
    """Every log entry's touched paths must exist in the vault.

    Fenced code blocks are masked (the log's own header shows the entry format
    as an example). A missing ``log.md`` is a vault-shape problem for
    ``doctor``, not a lint violation -- the check degrades to no findings.
    """
    if not store.exists(LOG_PATH):
        return []
    violations: list[Violation] = []
    for line_number, entry_topic, touched_path in _iter_log_touched_paths(
        store.read_text(LOG_PATH)
    ):
        if scope is not None and entry_topic != scope:
            continue
        if not _path_exists(store, touched_path):
            violations.append(
                Violation(
                    check=LintCheck.LOG_MISSING_PATH,
                    path=LOG_PATH,
                    line=line_number,
                    message=(
                        f"Log entry for topic '{entry_topic}' touches"
                        f" '{touched_path}', but no such file exists in the vault."
                    ),
                    fix="Restore the missing file or correct the log entry's path.",
                )
            )
    return violations


def _iter_log_touched_paths(text: str) -> list[tuple[int, str, str]]:
    """Yield ``(line, entry_topic, touched_path)`` for every bullet under an entry."""
    rows: list[tuple[int, str, str]] = []
    current_topic: str | None = None
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        entry = _LOG_ENTRY_RE.match(line)
        if entry is not None:
            current_topic = entry.group("topic").strip()
            continue
        stripped = line.strip()
        if line.startswith("#"):
            current_topic = None  # any other heading closes the entry
        elif current_topic is not None and stripped.startswith("- "):
            rows.append((line_number, current_topic, stripped[2:].strip()))
    return rows


def _path_exists(store: VaultStore, path: str) -> bool:
    """Existence check that treats vault-escaping paths as missing, not fatal."""
    try:
        return store.exists(path)
    except (PathOutsideVaultError, ValueError):
        return False


def _in_topic(path: str, topic: str) -> bool:
    return path.startswith(f"{topic}/")


def _has_dot_segment(path: str) -> bool:
    return any(segment.startswith(".") for segment in path.split("/"))


def _is_directory(store: VaultStore, name: str) -> bool:
    """Whether the top-level entry ``name`` is a directory (via the store protocol)."""
    try:
        store.list_dir(name)
    except NotADirectoryError:
        return False
    return True
