"""Behavioral contract of the wikilink graph: extraction, resolution, out/in.

Derived from the vault constitution (``vault-template/SCHEMA.md``) and the
recorded resolution decision, not from the implementation:

- extraction honors ``[[target]]`` / ``[[target|alias]]`` / ``#heading``
  stripping, and masks fenced code blocks and inline code spans -- the
  template deliberately shows wikilink *examples* inside code, and those must
  never count as links;
- resolution is conservative: a ``/`` target is a full vault path; a bare
  target resolves into the source page's own directory and NOWHERE else --
  a same-directory miss stays unresolved (lint fodder) even when a page of
  that name exists elsewhere in the vault. This deliberately pins the
  two-``SCHEMA.md`` basename ambiguity to the topic's own overlay;
- the shipped template's graph is the golden fixture: 27 outbound links,
  all resolved, with hand-counted per-page totals;
- inbound (backlink) queries agree edge-for-edge with the outbound scan.
"""

import pytest
from knotica.core.links import (
    extract_wikilinks,
    inbound_links,
    iter_page_paths,
    outbound_links,
    resolve_target,
)
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_status_porcelain

#: Complete page inventory of the instantiated template (dot-dirs excluded).
TEMPLATE_PAGES = (
    "SCHEMA.md",
    "START_HERE.md",
    "index.md",
    "log.md",
    "agentic-systems/SCHEMA.md",
    "agentic-systems/agent-memory.md",
    "agentic-systems/agent-workflow-memory.md",
    "agentic-systems/workflow-induction.md",
    "sources/agentic-systems/wang2024awm.md",
)

#: Hand-counted outbound links per template page (re-derived from the pages
#: themselves, independently of the implementation): index catalogs the three
#: demo pages plus both SCHEMA files; START_HERE links SCHEMA/index/log (log
#: twice); log links SCHEMA plus demo-operation wikilinks in OKF bullets; both
#: SCHEMA files and the stored source carry wikilink examples only in code.
GOLDEN_OUTBOUND_COUNTS = (
    ("SCHEMA.md", 0),
    ("START_HERE.md", 4),
    ("index.md", 5),
    ("log.md", 8),
    ("agentic-systems/SCHEMA.md", 0),
    ("agentic-systems/agent-memory.md", 3),
    ("agentic-systems/agent-workflow-memory.md", 4),
    ("agentic-systems/workflow-induction.md", 3),
    ("sources/agentic-systems/wang2024awm.md", 0),
)

GOLDEN_TOTAL_LINKS = 27


def _all_outbound_edges(store: LocalFSStore) -> list:
    """Every outbound edge in the vault, page by page (test-side ground truth)."""
    edges = []
    for page in iter_page_paths(store):
        edges.extend(outbound_links(store, page))
    return edges


# ---------------------------------------------------------------------------
# Extraction: syntax, aliases, code masking
# ---------------------------------------------------------------------------


def test_extraction_captures_targets_aliases_and_line_numbers():
    text = "# Title\nSee [[alpha]] and [[beta|The Beta Page]].\nAlso [[gamma#Details]].\n"
    links = extract_wikilinks(text)
    assert [(link.target, link.alias, link.line) for link in links] == [
        ("alpha", None, 2),
        ("beta", "The Beta Page", 2),
        ("gamma", None, 3),
    ]
    assert links[0].context == "See [[alpha]] and [[beta|The Beta Page]]."


def test_alias_splits_on_the_first_pipe():
    (link,) = extract_wikilinks("[[target|alias|with pipe]]")
    assert link.target == "target"
    assert link.alias == "alias|with pipe"


def test_pure_heading_self_references_are_not_links():
    assert extract_wikilinks("Jump to [[#summary]] or [[ ]] or [[|alias only]].") == []


def test_links_inside_fenced_code_blocks_are_not_extracted():
    text = (
        "Before [[real-one]].\n"
        "```\n[[fenced-away]]\n```\n"
        "~~~\n[[tilde-fenced]]\n~~~\n"
        "After [[real-two]].\n"
    )
    assert [link.target for link in extract_wikilinks(text)] == ["real-one", "real-two"]


def test_links_inside_inline_code_spans_are_not_extracted():
    text = "The syntax `[[page|display text]]` renders [[actual-link]] in Obsidian.\n"
    assert [link.target for link in extract_wikilinks(text)] == ["actual-link"]


# ---------------------------------------------------------------------------
# Resolution: full-path vs conservative same-directory
# ---------------------------------------------------------------------------


def test_bare_targets_resolve_into_the_sources_own_directory():
    assert resolve_target("react", "agentic-systems") == "agentic-systems/react.md"
    assert resolve_target("SCHEMA", "") == "SCHEMA.md"


def test_slash_targets_resolve_from_the_vault_root_regardless_of_source():
    assert resolve_target("agentic-systems/react", "other-topic") == "agentic-systems/react.md"
    assert resolve_target("agentic-systems/react", "") == "agentic-systems/react.md"


def test_bare_link_missing_same_directory_stays_unresolved_never_a_root_fallback(template_vault):
    store = LocalFSStore(template_vault)
    (template_vault / "agentic-systems" / "note.md").write_text(
        "A dangling bare reference: [[log]].\n", encoding="utf-8"
    )
    (link,) = outbound_links(store, "agentic-systems/note.md")
    assert link.target == "agentic-systems/log.md"
    assert link.resolved is False, (
        "bare-link miss must stay unresolved even though log.md exists at the vault root"
    )


def test_bare_schema_link_from_a_topic_page_pins_to_the_topics_own_overlay(template_vault):
    store = LocalFSStore(template_vault)
    (template_vault / "agentic-systems" / "note.md").write_text(
        "Conventions: [[SCHEMA]].\n", encoding="utf-8"
    )
    (link,) = outbound_links(store, "agentic-systems/note.md")
    assert link.target == "agentic-systems/SCHEMA.md"
    assert link.resolved is True


def test_explicit_md_extension_is_not_stripped_and_misses(template_vault):
    store = LocalFSStore(template_vault)
    (template_vault / "agentic-systems" / "note.md").write_text(
        "The constitution says omit the extension: [[agent-memory.md]].\n", encoding="utf-8"
    )
    (link,) = outbound_links(store, "agentic-systems/note.md")
    assert link.target == "agentic-systems/agent-memory.md.md"
    assert link.resolved is False


def test_outbound_edges_carry_source_raw_target_and_context(template_vault):
    store = LocalFSStore(template_vault)
    links = outbound_links(store, "index.md")
    overlay = next(link for link in links if link.raw_target == "agentic-systems/SCHEMA")
    assert overlay.source == "index.md"
    assert overlay.target == "agentic-systems/SCHEMA.md"
    assert overlay.alias is None
    assert overlay.line > 0
    assert "agentic-systems/SCHEMA" in overlay.context


# ---------------------------------------------------------------------------
# The template's golden graph
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("page", "expected_count"), GOLDEN_OUTBOUND_COUNTS)
def test_template_outbound_counts_match_the_hand_counted_graph(
    template_vault, page, expected_count
):
    store = LocalFSStore(template_vault)
    links = outbound_links(store, page)
    assert len(links) == expected_count, [link.raw_target for link in links]
    assert all(link.resolved for link in links), (
        f"{page}: the shipped template must not contain dangling links: "
        f"{[link.target for link in links if not link.resolved]}"
    )


def test_template_graph_totals_twenty_links_all_resolved(template_vault):
    store = LocalFSStore(template_vault)
    edges = _all_outbound_edges(store)
    assert len(edges) == GOLDEN_TOTAL_LINKS
    assert all(edge.resolved for edge in edges)


def test_page_scan_covers_the_inventory_and_skips_dot_folders_and_non_markdown(template_vault):
    (template_vault / "attachment.png").write_bytes(b"\x89PNG")
    (template_vault / ".obsidian").mkdir()
    (template_vault / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")
    (template_vault / "agentic-systems" / ".draft.md").write_text("hidden", encoding="utf-8")
    store = LocalFSStore(template_vault)
    assert set(iter_page_paths(store)) == set(TEMPLATE_PAGES)


# ---------------------------------------------------------------------------
# Inbound: backlinks agree with the outbound scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", TEMPLATE_PAGES)
def test_inbound_links_agree_edge_for_edge_with_the_outbound_scan(template_vault, page):
    store = LocalFSStore(template_vault)
    expected = [
        edge for edge in _all_outbound_edges(store) if edge.target == page and edge.source != page
    ]
    assert inbound_links(store, page) == expected


def test_demo_anchor_page_collects_backlinks_from_index_and_both_entity_pages(template_vault):
    store = LocalFSStore(template_vault)
    backlinks = inbound_links(store, "agentic-systems/agent-workflow-memory.md")
    sources = sorted(link.source for link in backlinks)
    assert sources == [
        "agentic-systems/agent-memory.md",
        "agentic-systems/agent-memory.md",
        "agentic-systems/workflow-induction.md",
        "agentic-systems/workflow-induction.md",
        "index.md",
        "log.md",
    ]


def test_backlinks_to_a_missing_page_expose_the_dangling_reference(template_vault):
    store = LocalFSStore(template_vault)
    (template_vault / "agentic-systems" / "note.md").write_text(
        "A reference to a page nobody wrote: [[ghost]].\n", encoding="utf-8"
    )
    (backlink,) = inbound_links(store, "agentic-systems/ghost.md")
    assert backlink.source == "agentic-systems/note.md"
    assert backlink.resolved is False


# ---------------------------------------------------------------------------
# Read-side discipline: link queries never touch git
# ---------------------------------------------------------------------------


def test_link_queries_never_commit_or_dirty_the_vault(template_vault):
    store = LocalFSStore(template_vault)
    _all_outbound_edges(store)
    inbound_links(store, "agentic-systems/agent-workflow-memory.md")
    assert git_commit_count(template_vault) == 1
    assert git_status_porcelain(template_vault) == ""
