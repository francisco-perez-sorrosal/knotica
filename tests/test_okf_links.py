"""Tests for OKF link parsing, resolution, and export conversion."""

from knotica.okf.index import VaultIndex, build_vault_index
from knotica.okf.links import (
    extract_internal_links,
    resolve_internal_link,
    rewrite_links_for_export,
)
from knotica.store import LocalFSStore


def test_extract_wikilink_and_markdown(template_vault):
    store = LocalFSStore(template_vault)
    index = build_vault_index(store)
    body = (
        "See [[agent-memory]] and [[agent-memory|Agent Memory]].\n"
        "[Paper](https://arxiv.org/abs/2210.03629)\n"
        "`[[ignored]]`\n"
    )
    (template_vault / "agentic-systems" / "note.md").write_text(body, encoding="utf-8")
    index = build_vault_index(store)
    links = extract_internal_links("agentic-systems/note.md", body)
    internal = [link for link in links if not link.is_external]
    assert len(internal) == 2
    assert links[0].syntax == "wikilink"
    assert links[1].syntax == "markdown" or links[0].target_ref == "agent-memory"


def test_wikilink_resolves_same_directory(template_vault):
    store = LocalFSStore(template_vault)
    index = build_vault_index(store)
    body = "Link [[agent-memory]].\n"
    (template_vault / "agentic-systems" / "note.md").write_text(body, encoding="utf-8")
    index = build_vault_index(store)
    (link,) = extract_internal_links("agentic-systems/note.md", body)
    resolved = resolve_internal_link(link, index)
    assert resolved.resolved
    assert resolved.target_path == "agentic-systems/agent-memory.md"


def test_export_converts_wikilink_to_bundle_relative(template_vault):
    store = LocalFSStore(template_vault)
    index = build_vault_index(store)
    body = "See [[agentic-systems/agent-memory]].\n"
    converted, warnings = rewrite_links_for_export(
        "index.md", body, index, link_style="bundle-relative"
    )
    assert (
        "[Agent Memory](/agentic-systems/agent-memory.md)" in converted
        or "agent-memory" in converted
    )
    assert "[[agentic-systems/agent-memory]]" not in converted


def test_code_block_wikilinks_not_rewritten(template_vault):
    store = LocalFSStore(template_vault)
    index = build_vault_index(store)
    body = "```\n[[secret]]\n```\n"
    converted, _warnings = rewrite_links_for_export("index.md", body, index)
    assert "[[secret]]" in converted
