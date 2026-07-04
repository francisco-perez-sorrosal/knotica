"""Three-way safety tests for ``knotica migrate`` — schema-version migration.

``migrate`` is a mutating vault operation: it brings a vault's schema up to the
version the running code ships, via a template-diff three-way merge that **never
clobbers a file the user has evolved**. These tests pin the interface contract
(exit-code table §4.4, one-commit-per-effective-mutation §6) and the load-bearing
safety property (an evolved file survives an applied migration byte-identical) —
not the migration's internal diff/merge mechanics.

The migration compares the vault's shipped state against the current template. A
vault is made *stale* by lowering its root ``schema_version`` (an older version is
unambiguously behind current under both a version-compare and a content-diff
reading) and committing that edit so the tree stays clean. The pristine
``template_vault`` is already at the current version — the up-to-date case.

Commands run as a subprocess (the faithful CLI surface a user drives); the config
fixtures redirect ``HOME``/``KNOTICA_CONFIG`` at a tmp vault, so no real user
config or vault is ever read. Assertions read the exit code, the git commit graph,
the frozen commit-message grammar, and the ``log.md`` audit trail.

RED until the ``migrate`` command lands: the registered stub raises
``NotImplementedError`` and exits 1 for every invocation.
"""

import os
import subprocess
import sys
from pathlib import Path

from knotica.cli.common import EXIT_MIGRATION_AVAILABLE, EXIT_SUCCESS
from support.vault import (
    git_commit_count,
    git_status_porcelain,
    parse_knotica_commit,
    parse_log_entries,
    run_git,
)

ROOT_SCHEMA = "SCHEMA.md"
#: A template file a user is apt to refine — the topic overlay. Its survival
#: through an applied migration is the three-way safety property.
EVOLVED_FILE = "agentic-systems/SCHEMA.md"
LOG_FILE = "log.md"


# ---------------------------------------------------------------------------
# CLI invocation + vault-shaping helpers
# ---------------------------------------------------------------------------


def _cli(*args: str) -> list[str]:
    console = Path(sys.executable).with_name("knotica")
    if console.exists():
        return [str(console), *args]
    return [
        sys.executable,
        "-c",
        "import sys; from knotica.cli import main; sys.exit(main())",
        *args,
    ]


def _migrate(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``knotica migrate <args>`` as the user would, inheriting the
    test's already-redirected environment (config points at the tmp vault)."""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        _cli("migrate", *args),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _make_stale(vault: Path) -> None:
    """Regress the vault one schema version behind current, leaving a clean tree.

    Lowering ``schema_version`` puts the vault behind the shipped template under
    either a version-compare or a content-diff reading of "stale"; committing the
    edit keeps the working tree clean so a later migration commit is the only
    change we count.
    """
    schema = vault / ROOT_SCHEMA
    text = schema.read_text(encoding="utf-8")
    assert "schema_version: 1" in text, "fixture drift: root SCHEMA is no longer version 1"
    schema.write_text(text.replace("schema_version: 1", "schema_version: 0", 1), encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: simulate a stale vault")


def _evolve(vault: Path, relpath: str) -> bytes:
    """Simulate a user's own refinement of a template file; return its exact bytes.

    Commits so the divergence is part of the clean baseline (a tracked, evolved
    file — the case the three-way merge must preserve rather than overwrite).
    """
    target = vault / relpath
    evolved = target.read_bytes() + b"\n<!-- user-authored refinement; keep me -->\n"
    target.write_bytes(evolved)
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: user evolves a template file")
    return evolved


def _newest_commit_subject(vault: Path) -> str:
    return run_git(vault, "log", "-1", "--format=%s").strip()


def _log_entries(vault: Path) -> list:
    return parse_log_entries((vault / LOG_FILE).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Up-to-date vault — nothing to do, no mutation (§4.4, §6.4)
# ---------------------------------------------------------------------------


def test_up_to_date_migrate_makes_no_commit(vault_config: Path, template_vault: Path) -> None:
    before = git_commit_count(template_vault)

    result = _migrate("--yes")

    assert result.returncode == EXIT_SUCCESS
    assert git_commit_count(template_vault) == before, (
        "an up-to-date vault must not be re-committed"
    )
    assert git_status_porcelain(template_vault) == "", (
        "an up-to-date migration must not dirty the tree"
    )


def test_up_to_date_check_reports_success(vault_config: Path, template_vault: Path) -> None:
    result = _migrate("--check")

    assert result.returncode == EXIT_SUCCESS, (
        "up-to-date --check must be exit 0, not migration-available"
    )


# ---------------------------------------------------------------------------
# 2. Stale vault — availability reporting and non-destructive preview (§4.4)
# ---------------------------------------------------------------------------


def test_stale_check_reports_migration_available(vault_config: Path, template_vault: Path) -> None:
    _make_stale(template_vault)
    before = git_commit_count(template_vault)

    result = _migrate("--check")

    assert result.returncode == EXIT_MIGRATION_AVAILABLE
    assert git_commit_count(template_vault) == before, "--check must never mutate the vault"


def test_dry_run_shows_diff_without_writing(vault_config: Path, template_vault: Path) -> None:
    _make_stale(template_vault)
    before = git_commit_count(template_vault)

    result = _migrate("--dry-run")

    assert result.returncode == EXIT_SUCCESS
    assert result.stdout.strip(), "--dry-run must render a diff preview to stdout"
    assert git_commit_count(template_vault) == before, "--dry-run must make no commit"
    assert git_status_porcelain(template_vault) == "", "--dry-run must make no writes to the vault"


# ---------------------------------------------------------------------------
# 3. Applied migration — safety, one commit, audit trail (§6.4, safety property)
# ---------------------------------------------------------------------------


def test_evolved_file_preserved_byte_identical(vault_config: Path, template_vault: Path) -> None:
    _make_stale(template_vault)
    evolved_bytes = _evolve(template_vault, EVOLVED_FILE)

    result = _migrate("--yes")

    assert result.returncode == EXIT_SUCCESS
    assert (template_vault / EVOLVED_FILE).read_bytes() == evolved_bytes, (
        "an applied migration clobbered a user-evolved file — the three-way merge "
        "must never overwrite divergence"
    )


def test_applied_migration_commits_once_with_grammar_and_log(
    vault_config: Path, template_vault: Path
) -> None:
    _make_stale(template_vault)
    before_commits = git_commit_count(template_vault)
    before_entries = len(_log_entries(template_vault))

    result = _migrate("--yes")

    assert result.returncode == EXIT_SUCCESS
    assert git_commit_count(template_vault) == before_commits + 1, (
        "an effective migration must be exactly one commit"
    )

    parsed = parse_knotica_commit(_newest_commit_subject(template_vault))
    assert parsed is not None, "migration commit subject must follow the frozen knotica() grammar"
    assert parsed["op"] == "migrate"

    entries = _log_entries(template_vault)
    assert len(entries) == before_entries + 1, "one log entry per mutating operation"
    assert entries[-1].op == "migrate"


def test_reapplying_an_applied_migration_is_a_noop(
    vault_config: Path, template_vault: Path
) -> None:
    _make_stale(template_vault)

    first = _migrate("--yes")
    assert first.returncode == EXIT_SUCCESS
    after_first = git_commit_count(template_vault)

    second = _migrate("--yes")

    assert second.returncode == EXIT_SUCCESS
    assert git_commit_count(template_vault) == after_first, (
        "re-running a completed migration must be a no-op — no second commit"
    )
    assert _migrate("--check").returncode == EXIT_SUCCESS, "the vault is now up to date"
