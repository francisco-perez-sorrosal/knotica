"""Behavioral contract tests for ``knotica.core.transaction`` — the one writer.

``VaultTransaction`` is the single mutation path for the whole system: every MCP
tool, CLI command, and future headless loop reaches the vault through it. A
defect here corrupts every mutating surface at once, so this is the highest-value
unit under test. The contract proven below (vault constitution + mutation
discipline design + INTERFACE_DESIGN §1.5):

1. **A user's Obsidian note is sacred.** A concurrent, uncommitted foreign edit —
   untracked new file, unstaged modification, or a manually staged change — is
   never swept into a knotica commit and never destroyed by a knotica rollback,
   across *both* the commit and the rollback paths. This is the top project risk
   ("knotica ate my Obsidian note"); the canonical scenario is a user editing
   page A in Obsidian while the agent writes page B.
2. **Commit scope is exactly the touched pages plus ``log.md``**, one commit per
   effective transaction, subject in the frozen grammar.
3. **Idempotency by result-state**: a declared write whose scrubbed content is
   byte-identical to the vault is not a change — no commit, no log entry, clean
   tree, ``changed=False``.
4. **Rollback is complete**: a failure after N-of-M writes restores the N (or
   removes them if new), leaves ``log.md`` untouched, makes no commit, and
   releases the lock so a follow-up transaction succeeds.
5. **Lock discipline**: a concurrent transaction gets a retryable ``LOCK_BUSY``
   within the timeout, never a hang; the lock releases on success *and* on the
   exception path.
6. **Scrub is integrated and loud**: real-key content is committed scrubbed with
   spans on the result; a false-positive corpus (SHAs, arXiv ids, base64) is
   committed verbatim.
7. **Crash simulation**: an injected failure between the writes and the commit
   drives the rollback path and re-raises.

Everything runs against a real git vault (the ``template_vault`` fixture) and a
real ``LocalFSStore`` — nothing about git, the filesystem, or the lock is mocked
except the single deliberate crash-injection point.
"""

from pathlib import Path

import pytest

from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.lock import vault_lock
from knotica.core.transaction import LOG_PATH, VaultTransaction
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import LocalFSStore
from support.vault import (
    git_commit_count,
    git_head_sha,
    git_status_porcelain,
    make_foreign_edit,
    parse_knotica_commit,
    parse_log_entries,
    run_git,
)

# A new page (page B) the agent writes; distinct from any template page so a
# fresh write is always an effective change.
PAGE_B = "agentic-systems/reflexion.md"
PAGE_B_CONTENT = "# Reflexion\n\nAgent that reflects on failures to improve.\n"
# An existing tracked page (page A) — used as the file a foreign editor opens.
TRACKED_PAGE_A = "agentic-systems/agent-memory.md"


def _store(vault: Path) -> LocalFSStore:
    return LocalFSStore(vault)


def _committed_paths(vault: Path) -> list[str]:
    """Vault-relative paths touched by the ``HEAD`` commit, sorted."""
    out = run_git(vault, "show", "--name-only", "--format=", "HEAD")
    return sorted(line for line in out.splitlines() if line.strip())


def _write_page_b(vault: Path, *, title: str = "Reflexion") -> object:
    """Run one successful write of page B; return the transaction result."""
    with VaultTransaction(_store(vault), vault, "write_page", "agentic-systems", title) as txn:
        txn.write(PAGE_B, PAGE_B_CONTENT)
    return txn.result


# ---------------------------------------------------------------------------
# 1. Commit scope + one-commit-per-op + frozen grammar (REQ-MUT-01, REQ-MUT-04)
# ---------------------------------------------------------------------------


def test_effective_write_makes_exactly_one_commit(template_vault: Path) -> None:
    before = git_commit_count(template_vault)

    _write_page_b(template_vault)

    assert git_commit_count(template_vault) == before + 1


def test_commit_touches_exactly_the_page_and_log(template_vault: Path) -> None:
    _write_page_b(template_vault)

    assert _committed_paths(template_vault) == sorted([PAGE_B, LOG_PATH])


def test_commit_subject_follows_the_frozen_grammar(template_vault: Path) -> None:
    _write_page_b(template_vault, title="Reflexion")

    subject = run_git(template_vault, "log", "-1", "--format=%s").strip()
    parsed = parse_knotica_commit(subject)
    assert parsed == {
        "op": "write_page",
        "topic": "agentic-systems",
        "title": "Reflexion",
    }


def test_one_log_entry_appended_per_operation(template_vault: Path) -> None:
    before = len(parse_log_entries(_store(template_vault).read_text(LOG_PATH)))

    _write_page_b(template_vault, title="Reflexion")

    entries = parse_log_entries(_store(template_vault).read_text(LOG_PATH))
    assert len(entries) == before + 1
    newest = entries[-1]
    assert (newest.op, newest.topic, newest.title) == (
        "write_page",
        "agentic-systems",
        "Reflexion",
    )
    assert PAGE_B in newest.pages


def test_result_reports_touched_pages_without_the_log(template_vault: Path) -> None:
    result = _write_page_b(template_vault)

    assert result.changed is True
    assert result.touched_paths == (PAGE_B,)
    assert LOG_PATH not in result.touched_paths
    assert result.commit_sha == git_head_sha(template_vault)


def test_multi_page_write_is_one_commit_with_all_pages(template_vault: Path) -> None:
    before = git_commit_count(template_vault)
    second_page = "agentic-systems/react.md"

    with VaultTransaction(
        _store(template_vault), template_vault, "write_page", "agentic-systems", "Two pages"
    ) as txn:
        txn.write(PAGE_B, PAGE_B_CONTENT)
        txn.write(second_page, "# ReAct\n\nReason then act.\n")

    assert git_commit_count(template_vault) == before + 1
    assert _committed_paths(template_vault) == sorted([PAGE_B, second_page, LOG_PATH])
    assert set(txn.result.touched_paths) == {PAGE_B, second_page}


# ---------------------------------------------------------------------------
# 2. Idempotency by result-state (REQ-TOOL-07, INTERFACE_DESIGN §1.5)
# ---------------------------------------------------------------------------


def test_identical_content_is_a_no_op(template_vault: Path) -> None:
    store = _store(template_vault)
    current = store.read_text(TRACKED_PAGE_A)
    before_count = git_commit_count(template_vault)
    before_head = git_head_sha(template_vault)

    with VaultTransaction(
        store, template_vault, "write_page", "agentic-systems", "Agent memory"
    ) as txn:
        txn.write(TRACKED_PAGE_A, current)  # byte-identical to what is on disk

    result = txn.result
    assert result.changed is False
    assert result.touched_paths == ()
    assert result.commit_sha == before_head
    assert git_commit_count(template_vault) == before_count
    assert git_status_porcelain(template_vault) == ""


def test_noop_writes_no_log_entry(template_vault: Path) -> None:
    store = _store(template_vault)
    before = _store(template_vault).read_text(LOG_PATH)

    with VaultTransaction(
        store, template_vault, "write_page", "agentic-systems", "Agent memory"
    ) as txn:
        txn.write(TRACKED_PAGE_A, store.read_text(TRACKED_PAGE_A))

    assert store.read_text(LOG_PATH) == before


def test_only_changed_pages_of_a_mixed_write_are_committed(template_vault: Path) -> None:
    store = _store(template_vault)
    unchanged = store.read_text(TRACKED_PAGE_A)
    before_count = git_commit_count(template_vault)

    with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "Mixed") as txn:
        txn.write(TRACKED_PAGE_A, unchanged)  # no-op page
        txn.write(PAGE_B, PAGE_B_CONTENT)  # effective page

    assert git_commit_count(template_vault) == before_count + 1
    assert txn.result.touched_paths == (PAGE_B,)
    assert _committed_paths(template_vault) == sorted([PAGE_B, LOG_PATH])


# ---------------------------------------------------------------------------
# 3. Foreign-edit survival matrix — {untracked, unstaged, staged} × {commit, rollback}
#    (pre-mortem #1: "knotica ate my Obsidian note")
# ---------------------------------------------------------------------------


def _make_untracked_foreign(vault: Path):
    return make_foreign_edit(vault, "concurrent-obsidian-note.md")


def _make_unstaged_foreign(vault: Path):
    # A user edits page A (a tracked page) in Obsidian; the change is on disk,
    # unstaged and uncommitted, while the agent writes page B.
    return make_foreign_edit(
        vault, TRACKED_PAGE_A, "# Agent memory\n\nEdited live in Obsidian, not saved to git.\n"
    )


def _make_staged_foreign(vault: Path):
    edit = make_foreign_edit(
        vault, "manually-staged-note.md", "Content the user ran `git add` on but did not commit.\n"
    )
    run_git(vault, "add", "--", "manually-staged-note.md")
    return edit


FOREIGN_MAKERS = {
    "untracked-new-file": _make_untracked_foreign,
    "unstaged-edit-to-tracked-page-A": _make_unstaged_foreign,
    "staged-foreign-edit": _make_staged_foreign,
}


@pytest.mark.parametrize("kind", list(FOREIGN_MAKERS))
def test_foreign_edit_survives_a_successful_commit(template_vault: Path, kind: str) -> None:
    foreign = FOREIGN_MAKERS[kind](template_vault)

    _write_page_b(template_vault)  # the agent writes page B and commits

    foreign.assert_intact()
    # The foreign path is never part of knotica's commit.
    foreign_rel = foreign.path.relative_to(template_vault).as_posix()
    assert foreign_rel not in _committed_paths(template_vault)


@pytest.mark.parametrize("kind", list(FOREIGN_MAKERS))
def test_foreign_edit_survives_a_rollback(
    template_vault: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = FOREIGN_MAKERS[kind](template_vault)

    # Inject a crash between the writes and the commit: the commit step raises,
    # so the transaction rolls back exactly its own paths.
    def boom(self: VaultVcs, paths, message: str) -> str:  # noqa: ANN001
        raise RuntimeError("simulated failure after writes, before commit")

    monkeypatch.setattr(VaultVcs, "commit_paths", boom)

    with pytest.raises(RuntimeError):
        with VaultTransaction(
            _store(template_vault), template_vault, "write_page", "agentic-systems", "B"
        ) as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)

    foreign.assert_intact()


# ---------------------------------------------------------------------------
# 4. Rollback completeness (REQ-MUT-02)
# ---------------------------------------------------------------------------


def test_rollback_removes_a_new_page_and_makes_no_commit(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(template_vault)
    before_count = git_commit_count(template_vault)
    before_log = store.read_text(LOG_PATH)

    def boom(self: VaultVcs, paths, message: str) -> str:  # noqa: ANN001
        raise RuntimeError("commit failed")

    monkeypatch.setattr(VaultVcs, "commit_paths", boom)

    with pytest.raises(RuntimeError):
        with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "B") as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)

    assert not store.exists(PAGE_B), "the new page must be removed on rollback"
    assert store.read_text(LOG_PATH) == before_log, "log.md must be untouched"
    assert git_commit_count(template_vault) == before_count, "no commit on failure"
    assert git_status_porcelain(template_vault) == "", "rollback leaves a clean tree"


def test_rollback_after_partial_writes_restores_each_written_path(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail on the *second* physical page write: page one is written to disk,
    # page two never is — the rollback must undo the one that landed.
    store = _store(template_vault)
    second_page = "agentic-systems/react.md"
    before_count = git_commit_count(template_vault)
    real_write = store.write_text_atomic
    calls = {"n": 0}

    def failing_write(path, content: str) -> None:  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full on the second page")
        real_write(path, content)

    monkeypatch.setattr(store, "write_text_atomic", failing_write)

    with pytest.raises(OSError):
        with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "Two") as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)
            txn.write(second_page, "# ReAct\n")

    assert not store.exists(PAGE_B), "the first (landed) new page must be rolled back"
    assert not store.exists(second_page)
    assert git_commit_count(template_vault) == before_count
    assert git_status_porcelain(template_vault) == ""


def test_rollback_restores_a_modified_tracked_page(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The transaction overwrites an existing tracked page, then the commit
    # fails — rollback must restore the original committed bytes.
    store = _store(template_vault)
    original = store.read_text(TRACKED_PAGE_A)

    def boom(self: VaultVcs, paths, message: str) -> str:  # noqa: ANN001
        raise RuntimeError("commit failed")

    monkeypatch.setattr(VaultVcs, "commit_paths", boom)

    with pytest.raises(RuntimeError):
        with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "A") as txn:
            txn.write(TRACKED_PAGE_A, "# Agent memory\n\nRewritten by a doomed transaction.\n")

    assert store.read_text(TRACKED_PAGE_A) == original
    assert git_status_porcelain(template_vault) == ""


def test_lock_released_after_rollback_lets_next_transaction_succeed(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(template_vault)

    def boom(self: VaultVcs, paths, message: str) -> str:  # noqa: ANN001
        raise RuntimeError("commit failed")

    monkeypatch.setattr(VaultVcs, "commit_paths", boom)
    with pytest.raises(RuntimeError):
        with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "B") as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)

    # Undo the injection and prove a follow-up transaction acquires + commits.
    monkeypatch.undo()
    before = git_commit_count(template_vault)
    _write_page_b(template_vault)
    assert git_commit_count(template_vault) == before + 1


# ---------------------------------------------------------------------------
# 5. Lock discipline (pre-mortem #7: lock starvation)
# ---------------------------------------------------------------------------


def test_concurrent_transaction_raises_retryable_lock_busy(template_vault: Path) -> None:
    store = _store(template_vault)
    with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "A") as outer:
        outer.write("agentic-systems/page-a.md", "A content\n")

        with pytest.raises(KnoticaError) as caught:
            with VaultTransaction(
                store, template_vault, "write_page", "agentic-systems", "B", lock_timeout=0.2
            ) as inner:
                inner.write("agentic-systems/page-b.md", "B content\n")

    assert caught.value.code is ErrorCode.LOCK_BUSY
    assert caught.value.retryable is True


def test_lock_released_on_success(template_vault: Path) -> None:
    _write_page_b(template_vault)

    # A zero-timeout acquisition succeeds only because the transaction released.
    with vault_lock(template_vault, timeout=0):
        pass


def test_lock_released_when_block_raises(template_vault: Path) -> None:
    with pytest.raises(RuntimeError):
        with VaultTransaction(
            _store(template_vault), template_vault, "write_page", "agentic-systems", "B"
        ) as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)
            raise RuntimeError("boom inside the block, before exit")

    with vault_lock(template_vault, timeout=0):
        pass


def test_exception_inside_block_writes_nothing(template_vault: Path) -> None:
    store = _store(template_vault)
    before_count = git_commit_count(template_vault)

    with pytest.raises(RuntimeError):
        with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "B") as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)
            raise RuntimeError("abort before exit")

    assert not store.exists(PAGE_B), "buffered writes never applied on an in-block raise"
    assert git_commit_count(template_vault) == before_count
    assert git_status_porcelain(template_vault) == ""


# ---------------------------------------------------------------------------
# 6. Scrub integration (REQ-MUT-03) — loud redaction, no false positives
# ---------------------------------------------------------------------------


def test_real_key_is_committed_scrubbed_with_spans_on_the_result(template_vault: Path) -> None:
    store = _store(template_vault)
    leaked = "# Notes\n\nOops, pasted a key: AKIAIOSFODNN7EXAMPLE in the draft.\n"

    with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "Leaky") as txn:
        txn.write(PAGE_B, leaked)

    committed = store.read_text(PAGE_B)
    assert "AKIAIOSFODNN7EXAMPLE" not in committed, "the secret must not reach git history"
    assert "[REDACTED:aws-access-key-id]" in committed

    result = txn.result
    assert len(result.redactions) == 1
    redaction = result.redactions[0]
    assert redaction.path == PAGE_B
    assert [span.pattern for span in redaction.spans] == ["aws-access-key-id"]
    assert result.warnings(), "a redaction must surface as a loud warning"


def test_false_positive_corpus_is_committed_verbatim(template_vault: Path) -> None:
    store = _store(template_vault)
    # Content that legitimately contains token-shaped strings but no real
    # credential: a full commit SHA, an arXiv id, a DOI, and a base64 blob.
    corpus = (
        "# Provenance\n\n"
        "Commit 8602311abcdef0123456789abcdef0123456789ab references "
        "arXiv:2409.07429 (doi:10.48550/arXiv.2409.07429).\n"
        "Figure encoded as aGVsbG8gd29ybGQgdGhpcyBpcyBub3QgYSBzZWNyZXQ= for reference.\n"
    )

    with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "Clean") as txn:
        txn.write(PAGE_B, corpus)

    assert store.read_text(PAGE_B) == corpus, "legitimate token-shaped prose must be untouched"
    assert txn.result.redactions == ()
    assert txn.result.warnings() == ()


# ---------------------------------------------------------------------------
# 7. Crash simulation — git failure maps to the typed envelope (REQ-MUT-02)
# ---------------------------------------------------------------------------


def test_git_failure_maps_to_git_error_and_rolls_back(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(template_vault)
    before_count = git_commit_count(template_vault)

    def raise_git_error(self: VaultVcs, paths, message: str) -> str:  # noqa: ANN001
        raise GitError("simulated git commit failure")

    monkeypatch.setattr(VaultVcs, "commit_paths", raise_git_error)

    with pytest.raises(KnoticaError) as caught:
        with VaultTransaction(store, template_vault, "write_page", "agentic-systems", "B") as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)

    assert caught.value.code is ErrorCode.GIT_ERROR
    assert not store.exists(PAGE_B), "writes rolled back after the git failure"
    assert git_commit_count(template_vault) == before_count


def test_construction_rejects_a_bad_commit_grammar_before_any_lock(template_vault: Path) -> None:
    # A grammar violation must fail fast at construction — before a lock is
    # taken or a byte is written — so a malformed op can never hold the vault.
    with pytest.raises(ValueError):
        VaultTransaction(_store(template_vault), template_vault, "Write Page", "topic", "title")


def test_write_rejects_the_reserved_log_path(template_vault: Path) -> None:
    with VaultTransaction(
        _store(template_vault), template_vault, "write_page", "agentic-systems", "B"
    ) as txn:
        with pytest.raises(ValueError):
            txn.write(LOG_PATH, "the transaction owns the log; callers may not write it")


# ---------------------------------------------------------------------------
# 8. Worktree-targeted transactions — an isolated multi-step ingest session
#
# A worktree lets a multi-step client-driven session (store a source, then
# write several pages, one transaction per step) land on its own branch
# without ever touching the canonical vault's checked-out branch or working
# tree. PINNED interface assumption, flagged rather than silently resolved:
# an additive ``work_dir`` keyword on ``VaultTransaction`` redirects both the
# file writes and the git commit onto the worktree's checked-out branch,
# while ``vault_root`` keeps naming the *canonical* root the vault lock is
# taken against (mirroring ``VaultVcs``'s own worktree primitives already
# exercised in ``test_vcs.py``). If the shipped parameter name differs, the
# integration checkpoint reconciles this file against it.
# ---------------------------------------------------------------------------


def _canonical_tree_snapshot(vault: Path) -> dict[str, bytes]:
    """Every file's vault-relative path -> bytes, excluding ``.git`` and the lock file.

    The lock file (``.knotica/locks/vault.lock``) is gitignored runtime
    infrastructure that a transaction's own lock acquisition creates as a
    side effect -- taking the lock at all is not a change to vault *content*,
    so it is excluded the same way the vault's own ``.gitignore`` excludes it.
    """
    return {
        str(rel): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
        and ".git" not in (rel := path.relative_to(vault)).parts
        and rel.as_posix() != ".knotica/locks/vault.lock"
    }


def _open_worktree(template_vault: Path, tmp_path: Path, name: str) -> tuple[VaultVcs, Path, str]:
    """Create a worktree off the canonical vault's current HEAD.

    Returns ``(canonical_vcs, work_dir, branch)``.
    """
    vcs = VaultVcs(template_vault)
    work_dir = tmp_path / "wip" / name
    branch = f"loop/wip/agentic-systems/{name}"
    vcs.add_worktree(work_dir, branch=branch, start_ref="HEAD")
    return vcs, work_dir, branch


def test_worktree_targeted_transaction_commits_on_the_worktree_branch_not_the_canonical_one(
    template_vault: Path, tmp_path: Path
) -> None:
    vcs, work_dir, branch = _open_worktree(template_vault, tmp_path, "source-branch")
    canonical_branch_before = vcs.current_branch()
    canonical_head_before = vcs.head_sha()
    worktree_store = LocalFSStore(work_dir)

    with VaultTransaction(
        worktree_store,
        template_vault,
        "write_page",
        "agentic-systems",
        "Worktree Page",
        work_dir=work_dir,
    ) as txn:
        txn.write(PAGE_B, PAGE_B_CONTENT)

    worktree_vcs = VaultVcs(work_dir)
    assert worktree_vcs.current_branch() == branch, "the commit must land on the worktree's branch"
    assert txn.result.commit_sha == worktree_vcs.head_sha()
    assert (work_dir / PAGE_B).read_text(encoding="utf-8") == PAGE_B_CONTENT
    # The canonical repo's own checkout must never move or gain the new page.
    assert vcs.current_branch() == canonical_branch_before
    assert vcs.head_sha() == canonical_head_before
    assert not (template_vault / PAGE_B).exists(), (
        "the write must not appear in the canonical working tree"
    )


def test_worktree_transaction_lock_blocks_a_concurrent_canonical_transaction(
    template_vault: Path, tmp_path: Path
) -> None:
    _, work_dir, _ = _open_worktree(template_vault, tmp_path, "source-lock-a")
    worktree_store = LocalFSStore(work_dir)
    canonical_store = _store(template_vault)

    with VaultTransaction(
        worktree_store,
        template_vault,
        "write_page",
        "agentic-systems",
        "A",
        work_dir=work_dir,
    ) as outer:
        outer.write(PAGE_B, PAGE_B_CONTENT)

        with pytest.raises(KnoticaError) as caught:
            with VaultTransaction(
                canonical_store,
                template_vault,
                "write_page",
                "agentic-systems",
                "B",
                lock_timeout=0.2,
            ) as inner:
                inner.write("agentic-systems/canonical-during-worktree.md", "B content\n")

    assert caught.value.code is ErrorCode.LOCK_BUSY
    assert caught.value.retryable is True


def test_canonical_transaction_lock_blocks_a_concurrent_worktree_transaction(
    template_vault: Path, tmp_path: Path
) -> None:
    _, work_dir, _ = _open_worktree(template_vault, tmp_path, "source-lock-b")
    worktree_store = LocalFSStore(work_dir)
    canonical_store = _store(template_vault)

    with VaultTransaction(
        canonical_store,
        template_vault,
        "write_page",
        "agentic-systems",
        "A",
    ) as outer:
        outer.write("agentic-systems/canonical-first.md", "A content\n")

        with pytest.raises(KnoticaError) as caught:
            with VaultTransaction(
                worktree_store,
                template_vault,
                "write_page",
                "agentic-systems",
                "B",
                work_dir=work_dir,
                lock_timeout=0.2,
            ) as inner:
                inner.write(PAGE_B, PAGE_B_CONTENT)

    assert caught.value.code is ErrorCode.LOCK_BUSY
    assert caught.value.retryable is True


def test_a_multi_step_worktree_ingest_leaves_the_canonical_vault_byte_identical(
    template_vault: Path, tmp_path: Path
) -> None:
    # The load-bearing proof behind the whole worktree-transaction seam: a
    # simulated multi-step ingest session (several separate transactions, the
    # way a client-driven store-source-then-write-pages session would run)
    # must never move the canonical vault's default-branch head or change one
    # byte of its working tree, however many worktree-scoped commits it makes.
    vcs, work_dir, _ = _open_worktree(template_vault, tmp_path, "source-multi-step")
    worktree_store = LocalFSStore(work_dir)
    head_before = vcs.head_sha()
    commit_count_before = git_commit_count(template_vault)
    status_before = git_status_porcelain(template_vault)
    snapshot_before = _canonical_tree_snapshot(template_vault)

    with VaultTransaction(
        worktree_store,
        template_vault,
        "store_source",
        "agentic-systems",
        "Source",
        work_dir=work_dir,
    ) as step_one:
        step_one.write(
            "sources/agentic-systems/new-source.md",
            "# Source\n\nFull source text, faithfully stored.\n",
        )

    with VaultTransaction(
        worktree_store,
        template_vault,
        "write_page",
        "agentic-systems",
        "First Page",
        work_dir=work_dir,
    ) as step_two:
        step_two.write(PAGE_B, PAGE_B_CONTENT)

    with VaultTransaction(
        worktree_store,
        template_vault,
        "write_page",
        "agentic-systems",
        "Second Page",
        work_dir=work_dir,
    ) as step_three:
        step_three.write("agentic-systems/react.md", "# ReAct\n\nReason then act.\n")

    worktree_vcs = VaultVcs(work_dir)
    assert worktree_vcs.head_sha() != head_before, (
        "sanity check: the worktree branch actually advanced across the three steps"
    )
    assert vcs.head_sha() == head_before, "the canonical default-branch ref must not move"
    assert git_commit_count(template_vault) == commit_count_before, (
        "the canonical vault must gain zero commits from a worktree-scoped ingest"
    )
    assert git_status_porcelain(template_vault) == status_before, (
        "the canonical working tree must report no changes"
    )
    assert _canonical_tree_snapshot(template_vault) == snapshot_before, (
        "the canonical working tree's bytes must be identical before and after"
    )


# ---------------------------------------------------------------------------
# Acquire-time self-heal: a crash-left merge remnant must never block a plain
# transaction (the daemon can die mid-merge; the flock auto-releases but
# MERGE_HEAD survives, and a scoped commit would fail on it)
# ---------------------------------------------------------------------------


def _induce_dangling_merge(vault: Path) -> None:
    """Leave a real conflicted in-progress merge on ``vault`` -- a crash remnant."""
    import subprocess

    vcs = VaultVcs(vault)
    default = vcs.default_branch()
    conflict = vault / "agentic-systems" / "heal-conflict.md"
    conflict.parent.mkdir(parents=True, exist_ok=True)

    conflict.write_text("base\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: heal conflict base")

    vcs.create_branch("crash/heal-side", default)
    vcs.checkout_branch("crash/heal-side")
    conflict.write_text("side\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: heal side change")

    vcs.checkout_branch(default)
    conflict.write_text("default\n", encoding="utf-8")
    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "test: heal default change")

    subprocess.run(
        ["git", "-C", str(vault), "merge", "crash/heal-side"],
        capture_output=True,
        text=True,
    )
    assert (vault / ".git" / "MERGE_HEAD").exists(), "fixture failed to leave MERGE_HEAD"


def test_a_crash_left_dangling_merge_is_healed_on_acquire_and_the_write_lands(
    template_vault: Path,
) -> None:
    _induce_dangling_merge(template_vault)

    with VaultTransaction(
        _store(template_vault), template_vault, "write_page", "agentic-systems", "heal test"
    ) as txn:
        txn.write(PAGE_B, PAGE_B_CONTENT)

    assert not (template_vault / ".git" / "MERGE_HEAD").exists(), (
        "acquire-time self-heal must clear the crash remnant"
    )
    assert (template_vault / PAGE_B).read_text(encoding="utf-8") == PAGE_B_CONTENT


def test_no_heal_happens_inside_an_active_mutation_span(
    template_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from knotica.core.transaction import vault_mutation_span

    heal_calls: list[str] = []
    original = VaultVcs.heal_git_mutation_state

    def _recording_heal(self: VaultVcs) -> None:
        heal_calls.append(str(self.root))
        original(self)

    monkeypatch.setattr(VaultVcs, "heal_git_mutation_state", _recording_heal)

    with vault_mutation_span(template_vault):
        span_entry_heals = len(heal_calls)
        with VaultTransaction(
            _store(template_vault), template_vault, "write_page", "agentic-systems", "span test"
        ) as txn:
            txn.write(PAGE_B, PAGE_B_CONTENT)

    assert span_entry_heals == 1, "span entry heals exactly once"
    assert len(heal_calls) == 1, (
        "a transaction nested inside a live span must NOT heal -- the span's "
        "own merge may be legitimately in flight"
    )
