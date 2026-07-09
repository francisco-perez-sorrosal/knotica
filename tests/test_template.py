"""Behavioral validation of the instantiated vault template.

Mechanical checks over a ``template_vault`` instance: the shipped inventory is
complete, the git spine is a single clean initial commit, the demo-ingest
sample (source + entity pages + index/log entries) obeys the frozen formats,
and no user-facing page links into a dot-folder (Obsidian hard-ignores
dot-paths, so such links would render broken).
"""

import re
from pathlib import Path

from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_is_ignored,
    git_status_porcelain,
    parse_frontmatter,
    parse_knotica_commit,
    parse_log_entries,
)

DEMO_ENTITY_PAGES = (
    "agentic-systems/agent-workflow-memory.md",
    "agentic-systems/workflow-induction.md",
    "agentic-systems/agent-memory.md",
)
DEMO_SOURCE = "sources/agentic-systems/wang2024awm.md"

CORE_FRONTMATTER_FIELDS = (
    "type",
    "topic",
    "created",
    "updated",
    "confidence",
    "sources",
    "status",
    "tags",
)

PROVENANCE_FIELDS = (
    "schema_version",
    "type",
    "topic",
    "citation_key",
    "retrieved",
    "origin_url",
    "sha256",
    "source_type",
    "ingested_by",
)

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_template_instantiates_with_the_full_root_and_topic_inventory(template_vault: Path):
    required_files = [
        "SCHEMA.md",
        "index.md",
        "log.md",
        "START_HERE.md",
        ".gitignore",
        ".knotica/prompts/ingest.md",
        ".knotica/prompts/query.md",
        ".knotica/prompts/lint.md",
        ".knotica/prompts/curate.md",
        "agentic-systems/SCHEMA.md",
        "agentic-systems/.knotica/datasets/qa.jsonl",
        DEMO_SOURCE,
        *DEMO_ENTITY_PAGES,
    ]
    required_dirs = [
        "agentic-systems/.knotica/prompts",
        "agentic-systems/.knotica/compiled",
    ]

    missing = [rel for rel in required_files if not (template_vault / rel).is_file()]
    missing += [rel for rel in required_dirs if not (template_vault / rel).is_dir()]
    assert missing == [], f"template instantiation is missing required entries: {missing}"


def test_topic_qa_dataset_ships_empty(template_vault: Path):
    qa = template_vault / "agentic-systems/.knotica/datasets/qa.jsonl"

    assert qa.read_text(encoding="utf-8").strip() == "", (
        "the flywheel dataset must ship empty — records are appended by curate operations"
    )


def test_no_metrics_file_ships_in_the_template(template_vault: Path):
    # Absence means "not yet evaluated": the eval harness is the only producer.
    stray = [str(p.relative_to(template_vault)) for p in template_vault.rglob("metrics.jsonl")]

    assert stray == [], f"metrics.jsonl must not ship in the template: {stray}"


# ---------------------------------------------------------------------------
# Git spine
# ---------------------------------------------------------------------------


def test_fresh_vault_has_exactly_one_initial_commit_and_a_clean_tree(template_vault: Path):
    assert git_commit_count(template_vault) == 1
    assert git_status_porcelain(template_vault) == "", (
        "a fresh vault instantiation must leave nothing uncommitted"
    )


def test_fresh_vault_baseline_carries_no_operation_commits(template_vault: Path):
    # Tests counting knotica(<op>) commits rely on a zero-op baseline.
    op_commits = [s for s in git_commit_subjects(template_vault) if parse_knotica_commit(s)]

    assert op_commits == []


def test_vault_gitignore_keeps_agent_state_committed_but_ignores_app_state(template_vault: Path):
    ignored = [
        ".obsidian/workspace.json",
        ".trash/deleted-note.md",
        ".DS_Store",
        ".knotica/locks/vault.lock",
    ]
    committed = [".knotica/prompts/ingest.md", "agentic-systems/.knotica/datasets/qa.jsonl"]

    wrongly_visible = [rel for rel in ignored if not git_is_ignored(template_vault, rel)]
    wrongly_ignored = [rel for rel in committed if git_is_ignored(template_vault, rel)]
    assert wrongly_visible == [], f"app/device state must be gitignored: {wrongly_visible}"
    assert wrongly_ignored == [], f"agent state must stay committed: {wrongly_ignored}"


# ---------------------------------------------------------------------------
# Demo-ingest sample (source, entity pages, index/log entries)
# ---------------------------------------------------------------------------


def test_demo_entity_pages_carry_schema_conformant_frontmatter(template_vault: Path):
    for rel in DEMO_ENTITY_PAGES:
        fields, _ = parse_frontmatter((template_vault / rel).read_text(encoding="utf-8"))

        missing = [f for f in CORE_FRONTMATTER_FIELDS if f not in fields]
        assert missing == [], f"{rel} is missing core frontmatter fields: {missing}"
        assert fields["topic"] == "agentic-systems", rel
        assert fields["confidence"] in ("low", "medium", "high"), rel
        assert fields["status"] in ("active", "stale"), rel
        assert isinstance(fields["sources"], list) and fields["sources"], (
            f"{rel} must cite at least one stored source"
        )


def test_demo_pages_are_clearly_marked_deletable(template_vault: Path):
    unmarked = [
        rel
        for rel in DEMO_ENTITY_PAGES
        if "delete" not in (template_vault / rel).read_text(encoding="utf-8").lower()
    ]

    assert unmarked == [], f"demo pages must carry a delete-me marker: {unmarked}"


def test_demo_source_carries_the_frozen_provenance_frontmatter(template_vault: Path):
    fields, _ = parse_frontmatter((template_vault / DEMO_SOURCE).read_text(encoding="utf-8"))

    missing = [f for f in PROVENANCE_FIELDS if f not in fields]
    assert missing == [], f"provenance frontmatter is missing fields: {missing}"
    assert fields["schema_version"] == 1
    assert fields["type"] == "source"
    assert fields["citation_key"] == Path(DEMO_SOURCE).stem, (
        "the citation key must match the source filename"
    )
    assert fields["source_type"] in ("html", "pdf", "markdown", "text")
    assert re.fullmatch(r"[0-9a-f]{64}", str(fields["sha256"])), (
        f"sha256 must be a 64-char hex digest, got {fields['sha256']!r}"
    )


def test_index_catalogs_every_demo_entity_page_with_full_path_links(template_vault: Path):
    index_text = (template_vault / "index.md").read_text(encoding="utf-8")
    expected_links = {rel.removesuffix(".md") for rel in DEMO_ENTITY_PAGES}

    linked = set(WIKILINK_RE.findall(index_text))
    uncataloged = expected_links - linked
    assert uncataloged == set(), f"index.md misses full-path links to demo pages: {uncataloged}"


def test_template_log_entries_obey_the_frozen_grammar(template_vault: Path):
    log_text = (template_vault / "log.md").read_text(encoding="utf-8")

    entries = parse_log_entries(log_text)
    assert entries, "the demo ingest must have appended real log entries"
    assert all("Fenced example" not in entry.title for entry in entries)
    ops = {entry.op for entry in entries}
    assert ops <= {"write_page", "store_source", "create_topic", "curate_example", "migrate"}, (
        f"log entries carry unknown operations: {ops}"
    )


def test_log_entry_bullets_point_at_files_that_exist(template_vault: Path):
    log_text = (template_vault / "log.md").read_text(encoding="utf-8")

    dangling = [
        page
        for entry in parse_log_entries(log_text)
        for page in entry.pages
        if not (template_vault / page).is_file()
    ]
    assert dangling == [], f"log bullets reference paths missing from the vault: {dangling}"


# ---------------------------------------------------------------------------
# Dot-path linking (Obsidian hard-ignores dot-folders)
# ---------------------------------------------------------------------------


def _user_facing_pages(vault: Path) -> list[Path]:
    return [
        page
        for page in vault.rglob("*.md")
        if not any(part.startswith(".") for part in page.relative_to(vault).parts)
    ]


def test_user_facing_pages_never_link_into_dot_folders(template_vault: Path):
    offenders = []
    for page in _user_facing_pages(template_vault):
        text = page.read_text(encoding="utf-8")
        targets = WIKILINK_RE.findall(text) + [
            t for t in MARKDOWN_LINK_RE.findall(text) if not t.startswith(("http://", "https://"))
        ]
        offenders += [
            (str(page.relative_to(template_vault)), target)
            for target in targets
            if target.startswith(".") or "/." in target
        ]

    assert offenders == [], f"user-facing pages link into dot-paths: {offenders}"
