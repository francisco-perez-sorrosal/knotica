"""Behavioral contract of the deterministic mechanical lint.

Derived from the vault constitution (``vault-template/SCHEMA.md`` as amended)
and the doctor-model boundary (mechanical checks only; semantics are the client
LLM's job), never from the implementation:

- the pristine template is mechanically clean -- lint over it yields the empty
  list, whole-vault and topic-scoped alike;
- for each of the thirteen stable :class:`LintCheck` ids, a single planted
  corruption of a template copy triggers exactly that check (with the offending
  path and an actionable ``fix``), and nothing else -- except where two checks
  are *intrinsically* coupled (a fully-unlinked page is both orphaned and
  unindexed), which is asserted as the coupled pair it is;
- semantic corruption (a stale or self-contradicting *claim* in page prose)
  triggers NOTHING: the mechanical boundary holds;
- lint is a pure read -- a clean tree stays clean, with zero new commits;
- results are deterministic: the same vault yields byte-identical violation
  lists (order included) across runs, because downstream evals count per check;
- topic scoping excludes other topics' page-level findings; a multi-violation
  page reports every applicable finding.
"""

from pathlib import Path

from knotica.core.lint import LintCheck, Violation, lint_vault
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_status_porcelain

MEMORY_PAGE = "agentic-systems/agent-memory.md"
ROOT_SCHEMA = "SCHEMA.md"
OVERLAY = "agentic-systems/SCHEMA.md"
INDEX = "index.md"
LOG = "log.md"


# ---------------------------------------------------------------------------
# Helpers: mutate a template copy, then lint it.
# ---------------------------------------------------------------------------


def lint(vault: Path, topic: str = "") -> list[Violation]:
    return lint_vault(LocalFSStore(vault), topic)


def checks(violations: list[Violation]) -> set[LintCheck]:
    return {v.check for v in violations}


def only(violations: list[Violation], check: LintCheck) -> Violation:
    """The single violation of ``check`` -- asserts it is the only finding."""
    assert checks(violations) == {check}, checks(violations)
    matches = [v for v in violations if v.check is check]
    assert len(matches) == 1, matches
    return matches[0]


def read(vault: Path, relpath: str) -> str:
    return (vault / relpath).read_text()


def write(vault: Path, relpath: str, text: str) -> None:
    path = vault / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def edit_memory(vault: Path, old: str, new: str) -> None:
    """Apply one substring edit to the demo concept page (indexed, non-orphan)."""
    text = read(vault, MEMORY_PAGE)
    assert old in text, f"anchor {old!r} not present"
    write(vault, MEMORY_PAGE, text.replace(old, new, 1))


def append_to_memory(vault: Path, extra: str) -> None:
    write(vault, MEMORY_PAGE, read(vault, MEMORY_PAGE) + extra)


# ---------------------------------------------------------------------------
# The clean template is mechanically clean.
# ---------------------------------------------------------------------------


def test_pristine_template_is_mechanically_clean(template_vault: Path) -> None:
    assert lint(template_vault) == []


def test_pristine_template_is_clean_when_topic_scoped(template_vault: Path) -> None:
    assert lint(template_vault, topic="agentic-systems") == []


# ---------------------------------------------------------------------------
# Frontmatter checks.
# ---------------------------------------------------------------------------


def test_content_page_without_frontmatter_is_flagged(template_vault: Path) -> None:
    _, _, body = read(template_vault, MEMORY_PAGE).partition("---\n")
    # Drop the whole frontmatter block, keeping the body (still linked/indexed).
    stripped = body.split("---\n", 1)[1]
    write(template_vault, MEMORY_PAGE, stripped)

    violation = only(lint(template_vault), LintCheck.FRONTMATTER_MISSING)
    assert violation.path == MEMORY_PAGE
    assert violation.fix


def test_unparseable_frontmatter_is_flagged_as_malformed(template_vault: Path) -> None:
    # A duplicate key is outside the strict subset -- parse fails, as data.
    edit_memory(template_vault, "type: concept\n", "type: concept\ntype: paper\n")

    violation = only(lint(template_vault), LintCheck.FRONTMATTER_MALFORMED)
    assert violation.path == MEMORY_PAGE
    assert violation.fix


def test_core_field_out_of_range_is_flagged(template_vault: Path) -> None:
    edit_memory(template_vault, "confidence: medium", "confidence: bogus")

    violation = only(lint(template_vault), LintCheck.FRONTMATTER_FIELD)
    assert violation.path == MEMORY_PAGE
    assert "confidence" in violation.message


def test_block_scalar_indicator_value_is_flagged(template_vault: Path) -> None:
    # A field whose value is a bare block-scalar token is the telltale of a
    # `desc: |` shape the strict-subset parser mis-parses rather than rejects.
    edit_memory(template_vault, "status: active\n", "status: active\nsupersedes: |\n")

    violation = only(lint(template_vault), LintCheck.FRONTMATTER_BLOCK_SCALAR)
    assert violation.path == MEMORY_PAGE
    assert violation.fix


# ---------------------------------------------------------------------------
# Wikilink checks.
# ---------------------------------------------------------------------------


def test_unresolved_wikilink_is_flagged(template_vault: Path) -> None:
    append_to_memory(template_vault, "\n\nSee [[does-not-exist-xyz]] for more.\n")

    violation = only(lint(template_vault), LintCheck.LINK_UNRESOLVED)
    assert violation.path == MEMORY_PAGE
    assert violation.line is not None


def test_link_into_dot_folder_is_flagged(template_vault: Path) -> None:
    append_to_memory(template_vault, "\n\nPrompt: [[.knotica/prompts/lint]].\n")

    violation = only(lint(template_vault), LintCheck.LINK_DOT_PATH)
    assert violation.path == MEMORY_PAGE


def test_bare_schema_link_from_subdirectory_is_flagged(template_vault: Path) -> None:
    append_to_memory(template_vault, "\n\nConventions: [[SCHEMA]].\n")

    violation = only(lint(template_vault), LintCheck.LINK_BARE_SCHEMA)
    assert violation.path == MEMORY_PAGE


# ---------------------------------------------------------------------------
# Reserved names.
# ---------------------------------------------------------------------------


def test_top_level_directory_with_reserved_name_is_flagged(template_vault: Path) -> None:
    # Turn the reserved root page START_HERE.md into a directory of that name.
    (template_vault / "START_HERE.md").unlink()
    (template_vault / "START_HERE.md").mkdir()
    write(template_vault, "START_HERE.md/placeholder.md", "# placeholder\n")

    violation = only(lint(template_vault), LintCheck.RESERVED_TOP_LEVEL_NAME)
    assert violation.path == "START_HERE.md"


def test_sources_directory_is_not_a_reserved_name_violation(template_vault: Path) -> None:
    # `sources/` is reserved *for* the source store -- its presence is sanctioned.
    assert LintCheck.RESERVED_TOP_LEVEL_NAME not in checks(lint(template_vault))


# ---------------------------------------------------------------------------
# Schema-version layers.
# ---------------------------------------------------------------------------


def test_missing_root_schema_version_is_flagged(template_vault: Path) -> None:
    text = read(template_vault, ROOT_SCHEMA)
    write(template_vault, ROOT_SCHEMA, text.replace("schema_version: 1\n", "", 1))

    violation = only(lint(template_vault), LintCheck.SCHEMA_VERSION_MISSING)
    assert violation.path == ROOT_SCHEMA


def test_overlay_schema_version_conflict_is_flagged(template_vault: Path) -> None:
    text = read(template_vault, OVERLAY)
    write(template_vault, OVERLAY, text.replace("schema_version: 1", "schema_version: 2", 1))

    violation = only(lint(template_vault), LintCheck.OVERLAY_VERSION_CONFLICT)
    assert violation.path == OVERLAY
    assert "2" in violation.message and "1" in violation.message


# ---------------------------------------------------------------------------
# Index coverage.
# ---------------------------------------------------------------------------


def test_content_page_absent_from_index_is_flagged(template_vault: Path) -> None:
    # A new page linked from an existing page (so not orphaned) but never added
    # to index.md -- the index-consistency world after write_page's index_entry.
    write(
        template_vault,
        "agentic-systems/new-page.md",
        "---\n"
        "type: concept\ntopic: agentic-systems\ncreated: 2026-07-03\n"
        "updated: 2026-07-03\nconfidence: medium\nsources: [wang2024awm]\n"
        "status: active\ntags: [demo]\n---\n\n# New page\n",
    )
    append_to_memory(template_vault, "\n\nRelated: [[new-page]].\n")

    violation = only(lint(template_vault), LintCheck.INDEX_MISSING_ENTRY)
    assert violation.path == "agentic-systems/new-page.md"


def test_demo_pages_are_all_indexed(template_vault: Path) -> None:
    # The template's demo pages carry catalog lines -- none is index-missing.
    missing = {v.path for v in lint(template_vault) if v.check is LintCheck.INDEX_MISSING_ENTRY}
    assert missing == set()


# ---------------------------------------------------------------------------
# Log-entry path existence.
# ---------------------------------------------------------------------------


def test_log_entry_touching_absent_path_is_flagged(template_vault: Path) -> None:
    entry = (
        "\n## [2026-07-03] write_page | agentic-systems | Ghost\n- agentic-systems/ghost-page.md\n"
    )
    write(template_vault, LOG, read(template_vault, LOG) + entry)

    violation = only(lint(template_vault), LintCheck.LOG_MISSING_PATH)
    assert violation.path == LOG
    assert violation.line is not None


# ---------------------------------------------------------------------------
# Orphaned pages (coupled with index-missing for a fully-unlinked page).
# ---------------------------------------------------------------------------


def test_fully_unlinked_page_is_orphaned_and_unindexed(template_vault: Path) -> None:
    # A page nothing links to has no inbound edge -- not even an index line --
    # so orphan and index-missing fire together, by construction.
    write(
        template_vault,
        "agentic-systems/lonely.md",
        "---\n"
        "type: concept\ntopic: agentic-systems\ncreated: 2026-07-03\n"
        "updated: 2026-07-03\nconfidence: medium\nsources: [wang2024awm]\n"
        "status: active\ntags: [demo]\n---\n\n# Lonely\n",
    )

    result = lint(template_vault)
    assert checks(result) == {LintCheck.PAGE_ORPHANED, LintCheck.INDEX_MISSING_ENTRY}
    orphan = next(v for v in result if v.check is LintCheck.PAGE_ORPHANED)
    assert orphan.path == "agentic-systems/lonely.md"


# ---------------------------------------------------------------------------
# The mechanical boundary: semantics trigger nothing.
# ---------------------------------------------------------------------------


def test_stale_and_contradicting_claims_trigger_no_violation(template_vault: Path) -> None:
    # Rewrite the body prose to be self-contradicting and stale, leaving every
    # mechanical property (frontmatter, links, index line) intact. Mechanical
    # lint reads structure, never claims -- so it stays silent.
    text = read(template_vault, MEMORY_PAGE)
    frontmatter, _, _ = text.partition("\n# Agent memory\n")
    poisoned = (
        frontmatter + "\n# Agent memory\n\n## Summary\n\n"
        "Agent memory does not exist and never worked; this directly contradicts\n"
        "the claim two lines down that it improves agents. This page is stale.\n\n"
        "Agent memory reliably improves agents over time.\n\n"
        "## Relations\n\n- [[agent-workflow-memory]] related.\n"
    )
    write(template_vault, MEMORY_PAGE, poisoned)

    assert lint(template_vault) == []


# ---------------------------------------------------------------------------
# Lint is a pure read.
# ---------------------------------------------------------------------------


def test_lint_makes_no_commit_and_leaves_a_clean_tree(template_vault: Path) -> None:
    commits_before = git_commit_count(template_vault)

    lint(template_vault)
    lint(template_vault, topic="agentic-systems")

    assert git_commit_count(template_vault) == commits_before
    assert git_status_porcelain(template_vault) == ""


# ---------------------------------------------------------------------------
# Determinism: same vault -> identical violation list (order included).
# ---------------------------------------------------------------------------


def test_same_vault_yields_identical_violation_list_across_runs(template_vault: Path) -> None:
    # Plant several independent violations so ordering is observable.
    edit_memory(template_vault, "confidence: medium", "confidence: bogus")
    append_to_memory(template_vault, "\n\nSee [[does-not-exist-xyz]].\n")
    write(
        template_vault,
        OVERLAY,
        read(template_vault, OVERLAY).replace("schema_version: 1", "schema_version: 9", 1),
    )

    first = [v.render() for v in lint(template_vault)]
    second = [v.render() for v in lint(template_vault)]

    assert len(first) >= 3
    assert first == second


# ---------------------------------------------------------------------------
# Topic scoping.
# ---------------------------------------------------------------------------


def test_topic_scope_excludes_other_topics_page_findings(template_vault: Path) -> None:
    # A malformed page in a second topic is seen whole-vault but not when the
    # lint is scoped to agentic-systems.
    write(
        template_vault,
        "other-topic/bad.md",
        "---\ntype: concept\ntype: paper\n---\n\n# Bad\n",
    )

    whole = {v.path for v in lint(template_vault) if v.check is LintCheck.FRONTMATTER_MALFORMED}
    scoped = {
        v.path
        for v in lint(template_vault, topic="agentic-systems")
        if v.check is LintCheck.FRONTMATTER_MALFORMED
    }

    assert "other-topic/bad.md" in whole
    assert "other-topic/bad.md" not in scoped


# ---------------------------------------------------------------------------
# A multi-violation page reports every applicable finding.
# ---------------------------------------------------------------------------


def test_page_with_several_defects_reports_all_of_them(template_vault: Path) -> None:
    edit_memory(template_vault, "confidence: medium", "confidence: bogus")
    append_to_memory(
        template_vault, "\n\nSee [[does-not-exist-xyz]] and [[.knotica/prompts/lint]].\n"
    )

    page_checks = {v.check for v in lint(template_vault) if v.path == MEMORY_PAGE}
    assert page_checks == {
        LintCheck.FRONTMATTER_FIELD,
        LintCheck.LINK_UNRESOLVED,
        LintCheck.LINK_DOT_PATH,
    }


# ---------------------------------------------------------------------------
# Every check id has coverage above -- self-guard the roster.
# ---------------------------------------------------------------------------


def test_all_thirteen_checks_have_a_planting_test() -> None:
    # Guards against a check id being added without a corresponding violation
    # test. Each id below is asserted by exactly one planting test above.
    covered = {
        LintCheck.FRONTMATTER_MISSING,
        LintCheck.FRONTMATTER_MALFORMED,
        LintCheck.FRONTMATTER_FIELD,
        LintCheck.FRONTMATTER_BLOCK_SCALAR,
        LintCheck.LINK_UNRESOLVED,
        LintCheck.LINK_DOT_PATH,
        LintCheck.LINK_BARE_SCHEMA,
        LintCheck.RESERVED_TOP_LEVEL_NAME,
        LintCheck.SCHEMA_VERSION_MISSING,
        LintCheck.OVERLAY_VERSION_CONFLICT,
        LintCheck.INDEX_MISSING_ENTRY,
        LintCheck.LOG_MISSING_PATH,
        LintCheck.PAGE_ORPHANED,
    }
    assert covered == set(LintCheck)
