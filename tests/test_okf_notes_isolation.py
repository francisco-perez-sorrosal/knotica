"""Personal notes must be invisible to the ``knotica.okf`` package.

``notes/<topic>/`` is a private layer excluded from every scored surface by
construction (``core.vault_layout.SCORED_FAMILIES``). The standalone
``knotica.okf`` package (``check``, ``repair``, ``export``, index-building)
used to walk the vault with the raw, unfiltered
``knotica.core.links.iter_page_paths`` and therefore treated every note as an
ordinary concept document -- reporting it as a malformed KB page, rewriting
its frontmatter, and shipping it inside an export bundle. These tests pin the
opposite: a note is untouched, unreported, and unexported.
"""

from pathlib import Path

from knotica.okf.check import check_vault
from knotica.okf.export import ExportOptions, export_bundle
from knotica.okf.index import build_vault_index
from knotica.okf.repair import RepairOptions, repair_vault
from knotica.store import LocalFSStore

_NOTE_RELPATH = "notes/agentic-systems/20260730-private-marginalia.md"
_NOTE_CONTENT = "---\nstatus: active\ntags: []\n---\n\nPrivate marginalia about phlogiston.\n"


def _write_note(vault: Path) -> Path:
    note_path = vault / _NOTE_RELPATH
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(_NOTE_CONTENT, encoding="utf-8")
    return note_path


def test_build_vault_index_does_not_index_a_note(template_vault: Path) -> None:
    _write_note(template_vault)
    store = LocalFSStore(template_vault)

    index = build_vault_index(store)

    assert _NOTE_RELPATH not in index.concept_paths
    assert _NOTE_RELPATH not in index.body_by_path


def test_okf_check_reports_nothing_about_a_note(template_vault: Path) -> None:
    _write_note(template_vault)
    store = LocalFSStore(template_vault)
    baseline = check_vault(store)

    result = check_vault(store)

    assert result.concept_files_checked == baseline.concept_files_checked
    assert not any(_NOTE_RELPATH in error.path for error in result.errors)
    assert not any(_NOTE_RELPATH in warning for warning in result.warnings)


def test_okf_repair_apply_leaves_a_note_byte_identical(template_vault: Path) -> None:
    note_path = _write_note(template_vault)
    before = note_path.read_bytes()
    store = LocalFSStore(template_vault)

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    assert result.status != "FAILED"
    assert _NOTE_RELPATH not in result.files_changed
    assert note_path.read_bytes() == before


def test_okf_repair_dry_run_does_not_plan_a_note(template_vault: Path) -> None:
    _write_note(template_vault)
    store = LocalFSStore(template_vault)

    result = repair_vault(store, RepairOptions(apply=False))

    assert _NOTE_RELPATH not in result.files_changed


def test_okf_export_does_not_ship_a_note(template_vault: Path, tmp_path: Path) -> None:
    _write_note(template_vault)
    store = LocalFSStore(template_vault)
    output = tmp_path / "okf-export"

    result = export_bundle(store, ExportOptions(output=output, force=True))

    assert result.status == "EXPORTED"
    assert not (output / _NOTE_RELPATH).exists()
    assert not (output / "notes").exists()
