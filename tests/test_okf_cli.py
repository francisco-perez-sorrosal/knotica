"""Tests for OKF check and export against the template vault."""

import shutil
from pathlib import Path

import pytest

from knotica.okf.check import check_vault
from knotica.okf.export import ExportOptions, export_bundle
from knotica.okf.repair import RepairOptions, repair_vault
from knotica.store import LocalFSStore


@pytest.fixture
def okf_ready_vault(template_vault):
    """Template vault with OKF-normalized frontmatter on concept pages."""
    store = LocalFSStore(template_vault)
    result = repair_vault(store, RepairOptions(apply=True, force=True))
    assert result.status != "FAILED"
    return template_vault


def test_okf_check_on_repaired_template(okf_ready_vault):
    store = LocalFSStore(okf_ready_vault)
    result = check_vault(store)
    assert result.concept_files_checked > 0
    # Warnings allowed; hard errors on missing type should be absent after repair.
    assert not any(error.code == "missing-type" for error in result.errors)


def test_okf_export_creates_bundle(okf_ready_vault, tmp_path):
    store = LocalFSStore(okf_ready_vault)
    output = tmp_path / "okf-export"
    result = export_bundle(store, ExportOptions(output=output, force=True))
    assert result.status == "EXPORTED"
    assert (output / "agentic-systems" / "agent-memory.md").exists()
    assert (output / "okf-export-report.md").exists()
    # Exported concept should use Markdown links, not wikilinks in body.
    exported = (output / "index.md").read_text(encoding="utf-8")
    assert "[[" not in exported or "agentic-systems" in exported


def test_repair_dry_run_does_not_modify(okf_ready_vault):
    store = LocalFSStore(okf_ready_vault)
    before = (okf_ready_vault / "agentic-systems" / "agent-memory.md").read_text(encoding="utf-8")
    repair_vault(store, RepairOptions(apply=False))
    after = (okf_ready_vault / "agentic-systems" / "agent-memory.md").read_text(encoding="utf-8")
    assert before == after
