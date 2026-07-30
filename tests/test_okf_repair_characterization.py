"""Characterization tests for ``okf.repair.repair_vault`` (td-020).

``repair_vault`` used to mutate the vault and commit entirely outside
``core.transaction`` -- writing pages and its own report via raw filesystem
calls, then shelling out to ``git add``/``git commit``, with no vault flock.
That was the defect td-020 tracked. This file pinned the behaviour the routing
fix had to preserve, so that swapping the write and commit path underneath
``repair_vault`` was provably behaviour-preserving; it now guards the routed
implementation against regressing back out of the single writer.

Two assertions were deliberately re-baselined when the routing landed, each
annotated at its site: the transaction takes the vault flock (a gitignored
runtime file appears under the vault), and it appends this operation's own
entry to ``log.md`` (repair previously left no audit record of its own runs).
"""

from datetime import date
from pathlib import Path
from shutil import copytree

import pytest

from knotica.core.lock import LOCK_RELATIVE_PATH
from knotica.okf.log_fmt import canonicalize_log
from knotica.okf.repair import RepairOptions, repair_vault
from knotica.store import LocalFSStore
from support.vault import (
    git_commit_count,
    git_head_sha,
    git_status_porcelain,
    make_foreign_edit,
    parse_frontmatter,
    run_git,
)

#: Vault-relative flock file the single-writer transaction creates lazily.
VAULT_LOCK_RELPATH = LOCK_RELATIVE_PATH.as_posix()


def _snapshot(vault: Path) -> dict[str, bytes]:
    """Every tracked-or-not file under ``vault`` (excluding ``.git``), by content."""
    return {
        str(path.relative_to(vault)): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(vault).parts
    }


def _log_preamble(log: str) -> str:
    """The log's preamble -- everything before the first date heading."""
    return log.split("\n## ", 1)[0]


def _log_entries(log: str) -> str:
    """The log's date-grouped entry section -- from the first date heading on."""
    return "## " + log.split("\n## ", 1)[1]


def _report_path_for(vault: Path) -> Path:
    """The report path ``repair_vault`` writes to on a same-day apply run."""
    return vault / "reports" / "okf" / f"{date.today().isoformat()}-okf-repair.md"


def _report_relpath() -> str:
    return f"reports/okf/{date.today().isoformat()}-okf-repair.md"


def test_dry_run_leaves_the_vault_byte_identical(template_vault):
    store = LocalFSStore(template_vault)
    before = _snapshot(template_vault)

    result = repair_vault(store, RepairOptions(apply=False))

    # Fixture-sanity guard: if the template vault ever needs no repair, this
    # test would pass vacuously (nothing to mutate either way).
    assert result.files_changed
    assert result.status == "DRY-RUN"
    assert _snapshot(template_vault) == before


def test_apply_changes_exactly_the_dry_run_planned_files(vault_seed, tmp_path):
    dry_vault = tmp_path / "dry"
    apply_vault = tmp_path / "apply"
    copytree(vault_seed, dry_vault)
    copytree(vault_seed, apply_vault)

    dry_result = repair_vault(LocalFSStore(dry_vault), RepairOptions(apply=False))

    before = _snapshot(apply_vault)
    apply_result = repair_vault(LocalFSStore(apply_vault), RepairOptions(apply=True, force=True))
    after = _snapshot(apply_vault)

    new_files = set(after) - set(before)
    changed = {path for path, content in after.items() if before.get(path) != content}

    # Apply has exactly two side effects beyond the planned files: the dated
    # report (covered on its own below), and the vault flock file the
    # single-writer transaction takes -- gitignored runtime state rather than
    # vault content, and itself the evidence that the repair is lock-guarded
    # (this entry is the re-baseline the td-020 routing introduced). Everything
    # else that changed must be exactly what the dry run planned, nothing more.
    extras = {_report_relpath(), VAULT_LOCK_RELPATH}
    assert new_files == extras
    assert apply_result.report_path is not None
    assert changed - extras == set(dry_result.files_changed)


def test_apply_canonicalizes_log_md(template_vault):
    """Repair's own re-render of ``log.md`` is the base the operation entry lands on.

    This is the guard against the routing's central trap: the transaction always
    rewrites ``log.md``, so a naive routing would apply repair's canonicalized
    log and then immediately clobber it with a render built from the stale
    on-disk bytes, silently discarding the canonicalization.

    Re-baselined by the td-020 routing: the log is now the canonicalized log
    *plus this operation's own entry*, not byte-equal to it. Pre-routing, repair
    wrote no log entry at all -- it left no audit record of its own runs -- and
    that entry is precisely the one-commit-per-operation invariant the routing
    exists to restore.
    """
    store = LocalFSStore(template_vault)
    before_log = (template_vault / "log.md").read_text(encoding="utf-8")
    canonical = canonicalize_log(before_log)
    # Fixture-sanity guard: a template log needing no canonicalization would
    # make every assertion below pass vacuously.
    assert canonical != before_log

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    after_log = (template_vault / "log.md").read_text(encoding="utf-8")
    assert "log.md" in result.files_changed
    assert _log_preamble(after_log) == _log_preamble(canonical)
    assert _log_preamble(after_log) != _log_preamble(before_log)
    # Below the new entry, the canonicalized log is present verbatim.
    assert after_log.endswith(_log_entries(canonical))
    prepended = after_log[: -len(_log_entries(canonical))]
    assert prepended.count("* **") == 1
    assert "* **Repair**: repair · okf — native OKF compatibility" in prepended


def test_apply_writes_a_dated_report_with_expected_frontmatter(template_vault):
    store = LocalFSStore(template_vault)

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    expected_path = _report_path_for(template_vault)
    assert expected_path.exists()
    assert Path(result.report_path).resolve() == expected_path.resolve()

    fields, _body = parse_frontmatter(expected_path.read_text(encoding="utf-8"))
    assert fields["type"] == "report"
    assert fields["title"] == "OKF Repair Report"
    assert fields["timestamp"]
    assert fields["topic"] == "okf"
    assert fields["tags"] == ["okf", "repair"]


def test_apply_commits_the_report_it_writes(template_vault):
    store = LocalFSStore(template_vault)

    repair_vault(store, RepairOptions(apply=True, force=True))

    tracked = run_git(template_vault, "ls-files", _report_relpath()).strip()
    assert tracked == _report_relpath()
    assert git_status_porcelain(template_vault) == ""


def test_apply_adds_exactly_one_commit(template_vault):
    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    assert git_commit_count(template_vault) == before_count + 1
    assert result.commit_sha == git_head_sha(template_vault)


def test_dirty_tree_without_force_raises_and_writes_nothing(template_vault):
    store = LocalFSStore(template_vault)
    make_foreign_edit(template_vault, relpath="scratch.txt")
    before = _snapshot(template_vault)
    before_count = git_commit_count(template_vault)

    with pytest.raises(ValueError, match="dirty"):
        repair_vault(store, RepairOptions(apply=True, force=False))

    assert _snapshot(template_vault) == before
    assert git_commit_count(template_vault) == before_count


def test_dirty_tree_with_force_proceeds(template_vault):
    store = LocalFSStore(template_vault)
    make_foreign_edit(template_vault, relpath="scratch.txt")

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    assert result.status != "FAILED"
    assert result.commit_sha is not None


def test_second_same_day_apply_replans_its_own_prior_report(template_vault):
    """Characterizes a measured (not assumed) quirk: ``repair_vault`` walks every
    ``.md`` page in the vault via ``iter_page_paths``, including the report file
    it just wrote and committed. On a second same-day apply, that report is
    itself swept up as a "concept file" needing normalization, so the run is
    NOT a no-op fixed point -- it re-plans and re-commits its own prior report.
    Pin this as-is; a routing fix may legitimately change it, but that would be
    a deliberate behavioural decision to record, not a silent regression.
    """
    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)

    first = repair_vault(store, RepairOptions(apply=True, force=True))
    second = repair_vault(store, RepairOptions(apply=True, force=True))

    assert second.files_changed == [_report_relpath()]
    assert second.commit_sha != first.commit_sha
    assert git_commit_count(template_vault) == before_count + 2
