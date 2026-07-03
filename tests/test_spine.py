"""Self-tests for the vault-fixture test spine.

Prove the guarantees the mutation tests will lean on: per-test vault isolation
(mutations never leak into the session seed), HOME isolation (no test can
reach real user config), config-fixture correctness, the foreign-edit factory
(the simulated concurrent Obsidian user), and the frozen-grammar and
frontmatter helpers.
"""

import os
import tomllib
from pathlib import Path

import pytest

from support.vault import (
    ForeignEdit,
    git_commit_count,
    git_status_porcelain,
    make_foreign_edit,
    parse_frontmatter,
    parse_knotica_commit,
    parse_log_entries,
    run_git,
)

# ---------------------------------------------------------------------------
# Vault isolation
# ---------------------------------------------------------------------------


def test_mutating_a_template_vault_never_touches_the_session_seed(
    template_vault: Path, vault_seed: Path
):
    (template_vault / "START_HERE.md").unlink()
    (template_vault / "scratch.md").write_text("instance-local\n", encoding="utf-8")
    run_git(template_vault, "add", "scratch.md")
    run_git(template_vault, "commit", "-m", "instance-local commit")

    assert (vault_seed / "START_HERE.md").is_file()
    assert not (vault_seed / "scratch.md").exists()
    assert git_commit_count(vault_seed) == 1
    assert git_status_porcelain(vault_seed) == ""


def test_template_vault_git_identity_allows_committing_out_of_the_box(template_vault: Path):
    (template_vault / "new-page.md").write_text("content\n", encoding="utf-8")
    run_git(template_vault, "add", "new-page.md")
    run_git(template_vault, "commit", "-m", "knotica(write_page): topic — title")

    assert git_commit_count(template_vault) == 2


# ---------------------------------------------------------------------------
# HOME isolation + config fixtures
# ---------------------------------------------------------------------------


def test_unconfigured_env_has_no_knotica_config_anywhere(unconfigured_env: Path, tmp_path: Path):
    assert os.environ["HOME"] == str(unconfigured_env)
    assert str(unconfigured_env).startswith(str(tmp_path)), (
        "the isolated home must live under the test's tmp_path — never the real HOME"
    )
    assert "KNOTICA_CONFIG" not in os.environ
    assert list(unconfigured_env.rglob("config.toml")) == []


def test_vault_config_points_the_default_vault_at_the_temp_vault(
    vault_config: Path, template_vault: Path, isolated_home: Path
):
    assert str(vault_config).startswith(str(isolated_home)), (
        "config.toml must live under the isolated home"
    )
    assert os.environ["KNOTICA_CONFIG"] == str(vault_config)

    config = tomllib.loads(vault_config.read_text(encoding="utf-8"))
    assert config["schema_version"] == 1
    assert config["default_vault"] == "main"
    vault_path = Path(config["vaults"]["main"]["path"])
    assert vault_path == template_vault
    assert (vault_path / "SCHEMA.md").is_file(), "the configured vault must be a real instance"


# ---------------------------------------------------------------------------
# Foreign uncommitted edit (concurrent Obsidian user)
# ---------------------------------------------------------------------------


def test_foreign_edit_creates_an_untracked_file_without_staging_it(template_vault: Path):
    edit = make_foreign_edit(template_vault)

    edit.assert_intact()
    status = git_status_porcelain(template_vault)
    assert f"?? {edit.path.relative_to(template_vault)}" in status
    assert git_commit_count(template_vault) == 1


def test_foreign_edit_can_modify_a_tracked_page_without_staging(template_vault: Path):
    edit = make_foreign_edit(
        template_vault,
        relpath="agentic-systems/agent-workflow-memory.md",
        content="unsaved rewrite typed in Obsidian\n",
    )

    edit.assert_intact()
    status = git_status_porcelain(template_vault)
    assert " M agentic-systems/agent-workflow-memory.md" in status


def test_foreign_edit_intactness_check_fails_when_the_edit_is_clobbered(template_vault: Path):
    edit = make_foreign_edit(template_vault)
    edit.path.write_text("swept up by a buggy operation\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="altered"):
        edit.assert_intact()


def test_foreign_edit_intactness_check_fails_when_the_edit_is_deleted(template_vault: Path):
    edit = make_foreign_edit(template_vault)
    edit.path.unlink()

    with pytest.raises(AssertionError, match="vanished"):
        edit.assert_intact()


def test_foreign_edit_is_reconstructible_from_its_record(tmp_path: Path):
    edit = ForeignEdit(path=tmp_path / "note.md", content="x\n")
    edit.path.write_text("x\n", encoding="utf-8")

    edit.assert_intact()


# ---------------------------------------------------------------------------
# Frozen commit-message grammar
# ---------------------------------------------------------------------------


def test_commit_grammar_parses_a_conforming_subject():
    parsed = parse_knotica_commit("knotica(write_page): agentic-systems — Ingest ReAct paper")

    assert parsed == {
        "op": "write_page",
        "topic": "agentic-systems",
        "title": "Ingest ReAct paper",
    }


@pytest.mark.parametrize(
    "subject",
    [
        "knotica(write_page): agentic-systems - Ingest ReAct paper",  # hyphen, not em-dash
        "knotica: agentic-systems — Ingest ReAct paper",  # missing (<op>)
        "write_page: agentic-systems — Ingest ReAct paper",  # missing knotica prefix
        "knotica(write_page): agentic-systems —",  # empty title
        "vault: instantiate template",  # the fixture's baseline commit
    ],
)
def test_commit_grammar_rejects_nonconforming_subjects(subject: str):
    assert parse_knotica_commit(subject) is None


# ---------------------------------------------------------------------------
# Frozen log-entry grammar
# ---------------------------------------------------------------------------


def test_log_parser_reads_entries_with_their_touched_page_bullets():
    text = (
        "# Operation Log\n\n"
        "## [2026-07-03] write_page | agentic-systems | Ingest ReAct paper\n"
        "- agentic-systems/react.md\n"
        "- index.md\n\n"
        "## [2026-07-04] store_source | agentic-systems | ReAct source\n"
        "- sources/agentic-systems/yao2022react.md\n"
    )

    entries = parse_log_entries(text)

    assert [(e.op, e.topic, e.title) for e in entries] == [
        ("write_page", "agentic-systems", "Ingest ReAct paper"),
        ("store_source", "agentic-systems", "ReAct source"),
    ]
    assert entries[0].pages == ["agentic-systems/react.md", "index.md"]
    assert entries[1].date == "2026-07-04"


def test_log_parser_skips_entries_inside_fenced_code_blocks():
    text = (
        "The format is:\n\n"
        "```\n"
        "## [2026-07-03] write_page | agentic-systems | Fenced example\n"
        "- fenced.md\n"
        "```\n\n"
        "## [2026-07-03] write_page | agentic-systems | Real entry\n"
        "- real.md\n"
    )

    entries = parse_log_entries(text)

    assert [entry.title for entry in entries] == ["Real entry"]


def test_log_parser_stops_attributing_bullets_after_intervening_prose():
    text = (
        "## [2026-07-03] write_page | agentic-systems | Entry\n"
        "- touched.md\n\n"
        "Some prose paragraph.\n"
        "- not a touched page\n"
    )

    entries = parse_log_entries(text)

    assert entries[0].pages == ["touched.md"]


# ---------------------------------------------------------------------------
# Frontmatter helper
# ---------------------------------------------------------------------------


def test_frontmatter_parser_reads_scalars_inline_lists_and_block_lists():
    text = (
        "---\n"
        "type: paper\n"
        "schema_version: 1\n"
        "origin_url: https://arxiv.org/html/2409.07429\n"
        "corrected_answer: null\n"
        "tags: [demo-sample, web-agents]\n"
        "sources:\n"
        "  - wang2024awm\n"
        "  - yao2022react\n"
        "empty: []\n"
        "---\n"
        "\n"
        "# Body heading\n"
    )

    fields, body = parse_frontmatter(text)

    assert fields["type"] == "paper"
    assert fields["schema_version"] == 1
    assert fields["origin_url"] == "https://arxiv.org/html/2409.07429"
    assert fields["corrected_answer"] is None
    assert fields["tags"] == ["demo-sample", "web-agents"]
    assert fields["sources"] == ["wang2024awm", "yao2022react"]
    assert fields["empty"] == []
    assert body.strip() == "# Body heading"


def test_frontmatter_parser_rejects_a_page_without_frontmatter():
    with pytest.raises(ValueError, match="no frontmatter"):
        parse_frontmatter("# Just a heading\n\nBody.\n")


def test_frontmatter_parser_rejects_an_unterminated_block():
    with pytest.raises(ValueError, match="unterminated"):
        parse_frontmatter("---\ntype: paper\n\n# Body\n")
