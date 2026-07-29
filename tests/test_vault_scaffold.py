"""Behavioral tests for ``knotica.core.vault_scaffold`` -- the shared scaffolder.

``scaffold_vault`` is the console-free extraction backing both ``knotica init``
and ``vault action=create``: copy the packaged template, optionally seed a
topic, git-bootstrap the result. These tests exercise it directly (no CLI
subprocess, no MCP dispatch) against disposable ``tmp_path`` targets.
"""

from pathlib import Path

import pytest

from knotica.core import vault_scaffold
from knotica.core.errors import ErrorCode, KnoticaError
from support.vault import git_commit_count, git_status_porcelain


def _vault_inventory(vault: Path) -> set[str]:
    """Every file under ``vault`` (relative), excluding git's own ``.git`` tree."""
    files: set[str] = set()
    for p in vault.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(vault)
        if rel.parts and rel.parts[0] == ".git":
            continue
        files.add(str(rel))
    return files


def test_scaffolds_a_fresh_vault_with_template_git_and_commit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    result = vault_scaffold.scaffold_vault(vault)

    assert result == vault_scaffold.ScaffoldResult(path=vault, created=True, committed=True)
    # A scaffolded vault is bare: constitution present, packaged demo topic stripped.
    inventory = _vault_inventory(vault)
    assert "SCHEMA.md" in inventory and ".knotica/prompts/query.md" in inventory
    assert not any(f.startswith("agentic-systems/") for f in inventory), "scaffold must be bare"
    assert not any(f.startswith("sources/agentic-systems/") for f in inventory)
    assert (vault / ".git").is_dir()
    assert (vault / "SCHEMA.md").is_file()
    assert git_commit_count(vault) >= 1
    assert git_status_porcelain(vault) == ""


def test_rescaffolding_an_initialized_vault_is_idempotent_and_does_not_clobber(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault_scaffold.scaffold_vault(vault)
    inventory_after_first = _vault_inventory(vault)

    result = vault_scaffold.scaffold_vault(vault)

    assert result.created is False
    assert result.committed is False
    assert _vault_inventory(vault) == inventory_after_first
    assert git_status_porcelain(vault) == ""


def test_non_empty_non_vault_directory_raises_invalid_argument(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "unrelated.txt").write_text("not a knotica vault", encoding="utf-8")

    with pytest.raises(KnoticaError) as exc:
        vault_scaffold.scaffold_vault(vault)

    assert exc.value.code is ErrorCode.INVALID_ARGUMENT


def test_reserved_topic_name_raises_reserved_name(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    with pytest.raises(KnoticaError) as exc:
        vault_scaffold.scaffold_vault(vault, topic="sources")

    assert exc.value.code is ErrorCode.RESERVED_NAME
    assert not vault.exists(), "a rejected topic must not leave a partially scaffolded vault"


def test_scaffold_with_a_topic_seeds_only_that_topic_and_stays_bare(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    vault_scaffold.scaffold_vault(vault, topic="decision-making")

    # The requested topic is seeded; the packaged demo topic is not.
    assert (vault / "decision-making" / "SCHEMA.md").is_file()
    assert not (vault / "agentic-systems").exists(), "no demo topic in any scaffolded vault"
    assert not (vault / "sources" / "agentic-systems").exists()
    # index.md / log.md carry no demo entries, but the constitution prose survives.
    index_text = (vault / "index.md").read_text(encoding="utf-8")
    log_text = (vault / "log.md").read_text(encoding="utf-8")
    assert "agentic-systems" not in index_text and "### " not in index_text
    assert "agentic-systems" not in log_text
    assert index_text.startswith("# Index")
    assert log_text.startswith("# Directory Update Log")
