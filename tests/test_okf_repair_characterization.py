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
from knotica.core.status import gather_wiki_status
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
    return vault / ".knotica" / "reports" / "okf" / f"{date.today().isoformat()}-okf-repair.md"


def _report_relpath() -> str:
    return f".knotica/reports/okf/{date.today().isoformat()}-okf-repair.md"


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


def test_apply_force_skips_uncommitted_draft_and_does_not_commit_it(template_vault):
    """``--force`` proceeds despite a dirty tree; it does not adopt the dirt.

    A user's untracked draft that repair would otherwise rewrite (no
    frontmatter -> gets some injected) must survive byte-identical and never
    land in the repair commit, while repairs the vault genuinely needs (its
    log preamble, per ``test_apply_canonicalizes_log_md``) still happen.
    """
    store = LocalFSStore(template_vault)
    draft = make_foreign_edit(template_vault)
    draft_relpath = draft.path.relative_to(template_vault).as_posix()

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    draft.assert_intact()
    assert draft_relpath not in result.files_changed
    assert draft_relpath in result.skipped_dirty
    assert "log.md" in result.files_changed
    tracked = run_git(template_vault, "ls-files", draft_relpath).strip()
    assert tracked == ""


def test_dry_run_with_dirty_tree_reports_the_same_skips_force_would(template_vault):
    """A dry run on a dirty tree previews exactly what ``--force`` would decline."""
    store = LocalFSStore(template_vault)
    draft = make_foreign_edit(template_vault)
    draft_relpath = draft.path.relative_to(template_vault).as_posix()

    dry_result = repair_vault(store, RepairOptions(apply=False))

    assert draft_relpath not in dry_result.files_changed
    assert draft_relpath in dry_result.skipped_dirty
    draft.assert_intact()


def test_dirty_tracked_page_that_needs_repair_is_skipped_and_reported(template_vault):
    """An unstaged edit to an existing tracked page is declined the same way an
    untracked draft is -- dirty is dirty, tracked or not.

    ``START_HERE.md`` is a concept page the template vault already needs to
    repair (see the dry-run ``files_changed`` baseline); a stray edit to it
    must not change whether it needs repair, only whether this run may touch
    it. ``log.md`` is unsuitable for this case -- the transaction always
    rewrites it as part of every commit, regardless of what repair plans.
    """
    store = LocalFSStore(template_vault)
    page_path = template_vault / "START_HERE.md"
    original = page_path.read_text(encoding="utf-8")
    page_path.write_text(original + "\nstray user edit\n", encoding="utf-8")

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    assert "START_HERE.md" in result.skipped_dirty
    assert "START_HERE.md" not in result.files_changed
    assert page_path.read_text(encoding="utf-8") == original + "\nstray user edit\n"


def test_clean_tree_apply_reports_no_skips(template_vault):
    """On a clean tree the dirty-path filter is a no-op -- unchanged behaviour."""
    store = LocalFSStore(template_vault)

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    assert result.skipped_dirty == []


def test_repeated_same_day_apply_on_a_settled_vault_makes_exactly_one_commit(template_vault):
    """td-022 regression: the report used to land in a vault-root ``reports/``,
    which every walk of the vault's pages re-swept as a concept file needing
    normalization -- so a second same-day apply always re-planned and
    re-committed its own prior report, and the vault never reached a fixed
    point even when it needed no repair at all.

    The report now lands under ``.knotica/`` (dot-prefixed, skipped by every
    page walk), and a run that finds nothing to repair, relocate, or skip
    writes no report and makes no commit. Three consecutive applies: the
    first does the vault's real repair work, the second and third are true
    no-ops.
    """
    store = LocalFSStore(template_vault)
    before_count = git_commit_count(template_vault)

    first = repair_vault(store, RepairOptions(apply=True, force=True))
    second = repair_vault(store, RepairOptions(apply=True, force=True))
    third = repair_vault(store, RepairOptions(apply=True, force=True))

    assert first.files_changed  # fixture-sanity: the template needs real repair
    assert second.files_changed == []
    assert second.commit_sha is None
    assert third.files_changed == []
    assert third.commit_sha is None
    assert git_commit_count(template_vault) == before_count + 1


def test_dry_run_after_a_settled_apply_reports_nothing_to_do(template_vault):
    """The td-022 symptom seen from the CLI/MCP side: before the fix, a settled
    vault's own report was permanently re-discovered as needing repair, so a
    dry run run after an apply would forever claim work was pending."""
    store = LocalFSStore(template_vault)

    repair_vault(store, RepairOptions(apply=True, force=True))
    dry = repair_vault(store, RepairOptions(apply=False))

    assert dry.files_changed == []
    assert dry.status == "DRY-RUN"


def test_repair_report_is_not_a_phantom_topic(template_vault):
    """The report's old vault-root ``reports/`` home was an ordinary, unreserved
    directory, so topic enumeration (``gather_wiki_status``) treated it as a
    topic. The new ``.knotica/`` home is dot-prefixed and therefore invisible
    to topic enumeration -- no phantom topic before or after repair."""
    store = LocalFSStore(template_vault)
    before = gather_wiki_status(store, template_vault, view="summary")["topics"]

    repair_vault(store, RepairOptions(apply=True, force=True))

    after = gather_wiki_status(store, template_vault, view="summary")["topics"]
    assert "reports" not in {row["topic"] for row in after}
    assert {row["topic"] for row in after} == {row["topic"] for row in before}


_LEGACY_REPORT_RELPATH = "reports/okf/2026-07-08-okf-repair.md"
_MIGRATED_REPORT_RELPATH = ".knotica/reports/okf/2026-07-08-okf-repair.md"


def _seed_legacy_report(vault: Path, *, extra_files: dict[str, str] | None = None) -> Path:
    """Commit a pre-fix ``reports/okf/`` leftover (plus any ``extra_files``)."""
    leftover = vault / _LEGACY_REPORT_RELPATH
    leftover.parent.mkdir(parents=True)
    leftover.write_text("---\ntype: report\n---\n# Old report\n", encoding="utf-8")
    for relpath, content in (extra_files or {}).items():
        (vault / relpath).write_text(content, encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "seed a legacy okf report")
    return leftover


def test_leftover_legacy_reports_are_relocated_in_the_same_commit(template_vault):
    """Migration for vaults that already ran ``okf repair`` before the fix and
    still carry ``reports/okf/*.md`` on disk: one apply moves them to the new
    location, cleans up the now-empty legacy directories, and does it all in
    the same commit as whatever else the run does."""
    store = LocalFSStore(template_vault)
    leftover = _seed_legacy_report(template_vault)
    before_count = git_commit_count(template_vault)

    result = repair_vault(store, RepairOptions(apply=True, force=True))

    assert (template_vault / _MIGRATED_REPORT_RELPATH).exists()
    assert not leftover.exists()
    assert not (template_vault / "reports").exists()
    assert result.relocated_reports == [(_LEGACY_REPORT_RELPATH, _MIGRATED_REPORT_RELPATH)]
    assert git_commit_count(template_vault) == before_count + 1

    topics = gather_wiki_status(store, template_vault, view="summary")["topics"]
    assert "reports" not in {row["topic"] for row in topics}


def test_relocation_leaves_unrelated_reports_content_untouched(template_vault):
    """Only ``reports/okf/`` is this tool's own output; a sibling file under
    ``reports/`` -- one a user may have filed there themselves, and which
    remains a legitimate topic since it holds real content -- must survive as
    the same file at the same path, and ``reports/`` itself must not be
    removed while it holds it. Ordinary repair (unrelated to relocation) may
    still normalize its frontmatter -- that is not this migration's concern."""
    store = LocalFSStore(template_vault)
    _seed_legacy_report(template_vault, extra_files={"reports/something-else.md": "# Not ours\n"})
    other = template_vault / "reports" / "something-else.md"

    repair_vault(store, RepairOptions(apply=True, force=True))

    assert other.exists()
    assert "# Not ours" in other.read_text(encoding="utf-8")
    assert (template_vault / "reports").exists()
    assert not (template_vault / "reports" / "okf").exists()


def test_relocation_dry_run_previews_without_moving(template_vault):
    """A dry run reports what would move without touching the filesystem."""
    store = LocalFSStore(template_vault)
    leftover = _seed_legacy_report(template_vault)

    result = repair_vault(store, RepairOptions(apply=False))

    assert result.relocated_reports == [(_LEGACY_REPORT_RELPATH, _MIGRATED_REPORT_RELPATH)]
    assert leftover.exists()
    assert not (template_vault / ".knotica" / "reports" / "okf").exists()


def test_second_relocation_run_moves_and_commits_nothing(template_vault):
    """Running relocation twice: the second run finds no legacy leftovers."""
    store = LocalFSStore(template_vault)
    _seed_legacy_report(template_vault)

    repair_vault(store, RepairOptions(apply=True, force=True))
    before_count = git_commit_count(template_vault)
    second = repair_vault(store, RepairOptions(apply=True, force=True))

    assert second.relocated_reports == []
    assert second.commit_sha is None
    assert git_commit_count(template_vault) == before_count
