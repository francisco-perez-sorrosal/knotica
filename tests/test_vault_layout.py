"""Behavioral contract of the single folder-family module.

Derived from the folder-family contract, not from the implementation --
``core/vault_layout.py`` did not exist when this file was written, so every
expectation below is specified independently of it:

- ``RESERVED_TOP_LEVEL_NAMES`` is declared exactly once, a ``frozenset``, and
  now includes ``"notes"`` alongside the six names that exist today;
- ``TOP_LEVEL_FAMILY_DIRS`` is exactly ``{"sources", "notes"}``;
- ``SCORED_FAMILIES`` is exactly ``{"page", "source"}`` -- ``"note"`` is
  deliberately excluded, since that exclusion is the entire point of the
  folder-family concept;
- ``family_of``/``topic_of`` are **identity-preserving** for every layout that
  exists in a real vault today (``<topic>/page.md``, ``sources/<topic>/x.md``,
  a nested subdirectory under a topic such as guillotine reports,
  ``<topic>/SCHEMA.md``, and the vault-root reserved files) -- this is the
  guard against a "behaviour-preserving refactor" silently moving topic
  attribution and, downstream, eval scores. Only the
  previously-unrepresentable ``notes/<topic>/...`` layout is allowed to
  return something genuinely new;
- ``notes/<topic>/...`` mirrors ``sources/<topic>/...``'s existing
  ``len(parts) >= 3`` threshold for deriving a topic from a family
  directory, exactly as today's ``_classify`` does for ``sources/``;
- a structurally invalid vault-relative path (absolute, escaping the vault
  via a ``..`` segment anywhere, or empty) raises ``ValueError`` rather than
  silently returning a garbage topic derived from ``".."`` or ``"/"`` as a
  path segment -- a decision pinned by this test file, since no prior
  behaviour exists to preserve for these inputs (today's ``_classify`` was
  never exercised with them and would either crash with an unrelated
  ``IndexError`` or return nonsense);
- a dot-prefixed top-level segment (e.g. ``.knotica/...``) is *not*
  special-cased -- every real call site already filters dot-folders out of
  its own file walk before ``family_of``/``topic_of`` ever see them, so this
  module does not re-implement that filtering; a bare root-level filename
  with no reserved-name significance is governed by the same positional
  ``len(parts) == 1`` rule as the reserved root files, not by a reserved-name
  lookup (that business logic lives in ``lint._check_reserved_names``).
"""

import pytest

from knotica.core.vault_layout import (
    NOTES_DIR,
    RESERVED_TOP_LEVEL_NAMES,
    SCORED_FAMILIES,
    SOURCES_DIR,
    TOP_LEVEL_FAMILY_DIRS,
    family_of,
    topic_of,
)

# ---------------------------------------------------------------------------
# Module-level constants -- the single declaration, its family/scoring sets.
# ---------------------------------------------------------------------------


def test_reserved_top_level_names_is_a_frozenset_of_the_six_pre_existing_names_plus_notes():
    assert isinstance(RESERVED_TOP_LEVEL_NAMES, frozenset)
    assert RESERVED_TOP_LEVEL_NAMES == frozenset(
        {
            "sources",
            "notes",
            "index.md",
            "log.md",
            "SCHEMA.md",
            "START_HERE.md",
            ".knotica",
            ".git",
        }
    )


def test_notes_is_a_member_of_reserved_top_level_names():
    assert "notes" in RESERVED_TOP_LEVEL_NAMES


def test_top_level_family_dirs_is_exactly_sources_and_notes():
    assert TOP_LEVEL_FAMILY_DIRS == frozenset({SOURCES_DIR, NOTES_DIR})
    assert TOP_LEVEL_FAMILY_DIRS == frozenset({"sources", "notes"})


def test_sources_dir_and_notes_dir_constants_are_the_literal_directory_names():
    assert SOURCES_DIR == "sources"
    assert NOTES_DIR == "notes"


def test_scored_families_is_page_and_source_only_note_is_never_scored():
    assert SCORED_FAMILIES == frozenset({"page", "source"})
    assert "note" not in SCORED_FAMILIES


# ---------------------------------------------------------------------------
# family_of / topic_of -- identity-preserving for every layout that exists
# today (only notes/<topic>/... may return something new).
# ---------------------------------------------------------------------------

#: (rel_path, expected_family, expected_topic) for every layout a real vault
#: contains today. Hand-derived from the vault constitution and the codebase's
#: own path helpers (e.g. ``guillotine/paths.py:reports_dir``), independently
#: of the not-yet-written implementation.
EXISTING_LAYOUTS = (
    # A plain content page.
    ("agentic-systems/agent-memory.md", "page", "agentic-systems"),
    # A stored source under sources/<topic>/.
    ("sources/agentic-systems/wang2024awm.md", "source", "agentic-systems"),
    # A nested subdirectory under a topic (guillotine reports,
    # vault-template/SCHEMA.md:177's documented `reports/*.md` family) --
    # still just a page of its first path segment, regardless of depth.
    ("agentic-systems/reports/guillotine/2026-07-01-1200.md", "page", "agentic-systems"),
    # A topic's schema overlay.
    ("agentic-systems/SCHEMA.md", "page", "agentic-systems"),
    # Vault-root reserved files -- no topic.
    ("index.md", "page", ""),
    ("log.md", "page", ""),
    ("START_HERE.md", "page", ""),
    ("SCHEMA.md", "page", ""),
    # The sources directory itself with no filename (2-part path) -- mirrors
    # today's _classify len(parts) >= 3 threshold: no third segment means no
    # topic can be derived.
    ("sources/agentic-systems", "source", ""),
)

NEW_LAYOUTS = (
    # notes/<topic>/... is the one layout unrepresentable before this module
    # -- it is allowed to return the genuinely new family "note".
    ("notes/agentic-systems/20260729-120000-reflection.md", "note", "agentic-systems"),
    # notes/<topic> with no filename -- same len(parts) >= 3 threshold
    # applied to notes as sources already has.
    ("notes/agentic-systems", "note", ""),
)


@pytest.mark.parametrize(("rel_path", "expected_family", "expected_topic"), EXISTING_LAYOUTS)
def test_family_and_topic_are_identity_preserved_for_every_layout_that_exists_today(
    rel_path: str, expected_family: str, expected_topic: str
) -> None:
    assert family_of(rel_path) == expected_family
    assert topic_of(rel_path) == expected_topic


@pytest.mark.parametrize(("rel_path", "expected_family", "expected_topic"), NEW_LAYOUTS)
def test_notes_layout_is_the_only_case_allowed_to_return_something_new(
    rel_path: str, expected_family: str, expected_topic: str
) -> None:
    assert family_of(rel_path) == expected_family
    assert topic_of(rel_path) == expected_topic


# ---------------------------------------------------------------------------
# Edge cases: inputs that are not a legitimate vault-relative content path.
# ---------------------------------------------------------------------------

INVALID_VAULT_RELATIVE_PATHS = (
    pytest.param("../escape.md", id="leading-parent-segment"),
    pytest.param("agentic-systems/../../etc/passwd", id="embedded-parent-segment"),
    pytest.param("/agentic-systems/page.md", id="absolute-path"),
    pytest.param("", id="empty-string"),
)


@pytest.mark.parametrize("rel_path", INVALID_VAULT_RELATIVE_PATHS)
def test_family_of_raises_on_a_path_that_is_not_vault_relative(rel_path: str) -> None:
    with pytest.raises(ValueError):
        family_of(rel_path)


@pytest.mark.parametrize("rel_path", INVALID_VAULT_RELATIVE_PATHS)
def test_topic_of_raises_on_a_path_that_is_not_vault_relative(rel_path: str) -> None:
    with pytest.raises(ValueError):
        topic_of(rel_path)


def test_dot_prefixed_top_level_segment_is_not_special_cased():
    # .knotica/ and .git/ are already excluded from every real call site's
    # file walk before family_of/topic_of ever see them (ripgrep's docstring:
    # "dot-folders ... are skipped"; iter_page_paths does the same). This
    # module does not re-implement that filtering -- a dot-prefixed segment
    # falls through the same positional rule as any other non-family
    # top-level directory, exactly like today's _classify.
    assert family_of(".knotica/locks/vault.lock") == "page"
    assert topic_of(".knotica/locks/vault.lock") == ".knotica"


def test_bare_root_level_filename_with_no_reserved_name_significance_has_no_topic():
    # The len(parts) == 1 rule is positional, not a reserved-names lookup --
    # that business logic lives in lint._check_reserved_names, not here.
    assert family_of("scratch.md") == "page"
    assert topic_of("scratch.md") == ""
