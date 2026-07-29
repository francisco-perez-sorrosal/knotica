"""Vault folder families -- the single source of truth for top-level layout.

Zero-dependency leaf module: imports nothing from ``knotica`` (deliberately, so
every layer -- ``core``, ``search``, ``cli``, ``service`` -- can depend on it
without a cycle). Pure string functions over vault-relative paths; no
:class:`~knotica.store.VaultStore`, no filesystem, no git.

A *folder family* answers "what kind of thing does this path hold?".
``sources/<topic>/`` holds the ``source`` family, ``notes/<topic>/`` holds the
``note`` family, and everything else is an ordinary ``page``.
:data:`SCORED_FAMILIES` names the two families that feed the eval scalar --
``note`` is absent, which is the entire point of the concept.

**Load-bearing contract: ``notes`` is a member of
:data:`RESERVED_TOP_LEVEL_NAMES`, and that membership is what keeps notes out
of topic enumeration.** Several consumers enumerate a vault's topics with the
shape ``if name.startswith(".") or name in RESERVED_TOP_LEVEL_NAMES: continue``
(``core.vault_metadata_tree``, ``core.status``, ``mcp_server.tools_read``,
``service.manager``). Because ``notes`` is reserved, ``notes/`` is skipped by
all of them for free -- exclusion by omission rather than by a filter list that
must be grown and kept correct. This is a *contract*, not a happy accident: a
future simplification of any of those reserved-name checks would silently
re-admit personal notes into the scored topic corpus, with no error and no
failing test elsewhere. Keep ``notes`` reserved, or replace the guarantee
before removing it.

:func:`family_of` and :func:`topic_of` preserve the positional rules that
``search.ripgrep._classify`` has always applied -- a family directory needs a
third path segment before a topic can be derived from it, and a bare
vault-root file has no topic at all. Dot-prefixed segments are *not*
special-cased: every call site already filters dot-folders out of its own walk
(see :func:`knotica.core.links.iter_page_paths`), so this module does not
re-implement that filtering.
"""

from pathlib import PurePosixPath
from typing import Literal

__all__ = [
    "NOTES_DIR",
    "RESERVED_TOP_LEVEL_NAMES",
    "SCORED_FAMILIES",
    "SOURCES_DIR",
    "TOP_LEVEL_FAMILY_DIRS",
    "Family",
    "family_of",
    "topic_of",
]

#: What kind of content a vault path holds (see module docs).
Family = Literal["page", "source", "note"]

#: Vault-root directory holding stored source documents, by topic.
SOURCES_DIR = "sources"

#: Vault-root directory holding personal notes, by topic.
NOTES_DIR = "notes"

#: Family of each vault-root directory that is not an ordinary topic.
_FAMILY_BY_DIR: dict[str, Family] = {SOURCES_DIR: "source", NOTES_DIR: "note"}

#: Vault-root directories whose children are topics rather than content --
#: reserved names that legitimately exist, never a lint violation.
TOP_LEVEL_FAMILY_DIRS: frozenset[str] = frozenset(_FAMILY_BY_DIR)

#: Families that feed the eval scalar. ``note`` is deliberately absent: the
#: unscored layer must not be able to move a KB quality measurement.
SCORED_FAMILIES: frozenset[Family] = frozenset({"page", "source"})

#: Top-level names that may never be used as topic names (root constitution
#: § Reserved names). The single declaration -- ``core.lint`` re-exports it and
#: ``core.vault_scaffold`` aliases it, so every consumer shares one set.
RESERVED_TOP_LEVEL_NAMES: frozenset[str] = frozenset(
    {
        SOURCES_DIR,
        NOTES_DIR,
        "index.md",
        "log.md",
        "SCHEMA.md",
        "START_HERE.md",
        ".knotica",
        ".git",
    }
)

#: Segments a family path needs before a topic can be derived from it:
#: ``sources/<topic>/<file>``. Two segments name the topic *directory* itself,
#: which carries no content and therefore no topic attribution.
_MIN_FAMILY_PARTS_FOR_TOPIC = 3


def family_of(rel_path: str) -> Family:
    """Return the folder family of vault-relative ``rel_path``.

    Raises ``ValueError`` if ``rel_path`` is not a legitimate vault-relative
    path (see :func:`topic_of`).
    """
    return _classify(rel_path)[0]


def topic_of(rel_path: str) -> str:
    """Return the topic vault-relative ``rel_path`` belongs to, ``""`` if none.

    A family path (``sources/<topic>/x.md``) yields its second segment; any
    other path yields its first, except a bare vault-root file which has no
    topic. For a note the topic is the *filing* location, which a cross-topic
    anchor may legitimately disagree with.

    Raises ``ValueError`` if ``rel_path`` is empty, absolute, or contains a
    ``..`` segment -- fail fast rather than derive a garbage topic such as
    ``".."`` and propagate it into a scored surface.
    """
    return _classify(rel_path)[1]


def _classify(rel_path: str) -> tuple[Family, str]:
    """Derive ``(family, topic)`` from a validated vault-relative path."""
    parts = _vault_relative_parts(rel_path)
    family = _FAMILY_BY_DIR.get(parts[0])
    if family is not None:
        topic = parts[1] if len(parts) >= _MIN_FAMILY_PARTS_FOR_TOPIC else ""
        return (family, topic)
    if len(parts) == 1:
        return ("page", "")
    return ("page", parts[0])


def _vault_relative_parts(rel_path: str) -> tuple[str, ...]:
    """Split ``rel_path`` into segments, rejecting anything not vault-relative."""
    path = PurePosixPath(rel_path)
    if path.is_absolute():
        raise ValueError(f"path must be vault-relative, got absolute: {rel_path!r}")
    parts = path.parts
    if not parts:
        raise ValueError("path must be a non-empty vault-relative path")
    if ".." in parts:
        # PurePosixPath does not normalize `..`, so an embedded parent segment
        # would otherwise survive into a derived topic.
        raise ValueError(f"path must not escape the vault via '..': {rel_path!r}")
    return parts
