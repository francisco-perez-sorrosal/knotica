"""Behavioral contract tests for ``knotica.core.vcs`` — path-scoped git.

The contract under test (vault constitution + mutation-discipline design):

1. **Path scoping is absolute.** Every mutating operation touches exactly the
   paths it was given — a concurrent, uncommitted user edit (an Obsidian
   window left open) is never swept into a knotica commit and never destroyed
   by a knotica rollback. This holds even against adversarial inputs: content
   pre-staged by someone else, and pages literally named like glob patterns.
2. **No unscoped primitive exists.** The API surface itself must be incapable
   of an add-all or a history-destroying reset — scoping by convention is not
   enough; the capability must be structurally absent.
3. **Commit messages round-trip the frozen grammar** and failures surface as
   the typed ``GitError`` carrying actionable context.
4. **Cloning yields an independent, committable frozen corpus.** ``clone_to``
   produces a git clone pinned at the source ``HEAD`` (or an explicit ref) as
   its own ``VaultVcs``, stamps a clone-local committer identity so a later eval
   commit succeeds even on a machine with no ambient git identity, and leaves
   the source byte-identical.

All tests run against real git repositories (the ``template_vault`` fixture);
nothing about git is mocked, and every clone is a local-path clone (zero
network).
"""

import os
from pathlib import Path

import pytest

from knotica.core.vcs import GitError, VaultVcs
from support.vault import (
    git_commit_count,
    git_commit_subjects,
    git_head_sha,
    git_status_porcelain,
    make_foreign_edit,
    parse_knotica_commit,
    run_git,
)

A_GRAMMAR_MESSAGE = "knotica(write_page): agentic-systems — Agent Memory"


@pytest.fixture
def vcs(template_vault: Path) -> VaultVcs:
    return VaultVcs(template_vault)


def _paths_in_head_commit(vault: Path) -> set[str]:
    """File paths recorded by the HEAD commit (deletions included)."""
    output = run_git(vault, "show", "--name-only", "--format=", "HEAD")
    return {line.strip() for line in output.splitlines() if line.strip()}


def _staged_paths(vault: Path) -> set[str]:
    output = run_git(vault, "diff", "--cached", "--name-only")
    return {line.strip() for line in output.splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# Commit: scoping
# ---------------------------------------------------------------------------


def test_commit_records_exactly_the_named_paths(vcs: VaultVcs, template_vault: Path) -> None:
    (template_vault / "a.md").write_text("page a\n", encoding="utf-8")
    (template_vault / "b.md").write_text("page b\n", encoding="utf-8")

    vcs.commit_paths(["a.md", "b.md"], A_GRAMMAR_MESSAGE)

    assert _paths_in_head_commit(template_vault) == {"a.md", "b.md"}


def test_a_foreign_uncommitted_edit_survives_a_scoped_commit(
    vcs: VaultVcs, template_vault: Path
) -> None:
    untracked_note = make_foreign_edit(template_vault)
    tracked_edit = make_foreign_edit(
        template_vault, relpath="index.md", content="user rewrote the index in Obsidian\n"
    )
    (template_vault / "ops-output.md").write_text("written by the operation\n", encoding="utf-8")

    vcs.commit_paths(["ops-output.md"], A_GRAMMAR_MESSAGE)

    untracked_note.assert_intact()
    tracked_edit.assert_intact()
    committed = _paths_in_head_commit(template_vault)
    assert committed == {"ops-output.md"}
    # The user's edits are still theirs to commit: one untracked, one modified.
    status = git_status_porcelain(template_vault)
    assert "?? concurrent-obsidian-note.md" in status
    assert " M index.md" in status


def test_content_pre_staged_by_someone_else_stays_out_of_the_commit(
    vcs: VaultVcs, template_vault: Path
) -> None:
    # A user ran `git add` on their own draft before the operation started.
    (template_vault / "user-staged-draft.md").write_text("user draft\n", encoding="utf-8")
    run_git(template_vault, "add", "user-staged-draft.md")
    (template_vault / "ops-output.md").write_text("written by the operation\n", encoding="utf-8")

    vcs.commit_paths(["ops-output.md"], A_GRAMMAR_MESSAGE)

    assert _paths_in_head_commit(template_vault) == {"ops-output.md"}
    assert _staged_paths(template_vault) == {"user-staged-draft.md"}  # still staged, uncommitted


def test_a_page_named_like_a_glob_never_widens_the_commit(
    vcs: VaultVcs, template_vault: Path
) -> None:
    # A page literally named "*.md" must commit only itself, not every .md file.
    (template_vault / "*.md").write_text("a literally-star-named page\n", encoding="utf-8")
    (template_vault / "victim.md").write_text("must stay untracked\n", encoding="utf-8")

    vcs.commit_paths(["*.md"], A_GRAMMAR_MESSAGE)

    assert _paths_in_head_commit(template_vault) == {"*.md"}
    assert "?? victim.md" in git_status_porcelain(template_vault)


def test_commit_records_a_named_deletion(vcs: VaultVcs, template_vault: Path) -> None:
    page = "agentic-systems/agent-memory.md"
    (template_vault / page).unlink()

    vcs.commit_paths([page], "knotica(curate_example): agentic-systems — drop stale page")

    assert _paths_in_head_commit(template_vault) == {page}
    assert run_git(template_vault, "ls-tree", "--name-only", "HEAD", page).strip() == ""
    assert git_status_porcelain(template_vault) == ""


# ---------------------------------------------------------------------------
# Commit: grammar, result, refusals, failure typing
# ---------------------------------------------------------------------------


def test_commit_message_round_trips_the_frozen_grammar(vcs: VaultVcs, template_vault: Path) -> None:
    (template_vault / "new-page.md").write_text("content\n", encoding="utf-8")

    vcs.commit_paths(["new-page.md"], A_GRAMMAR_MESSAGE)

    subject = git_commit_subjects(template_vault)[0]
    assert subject == A_GRAMMAR_MESSAGE  # transported byte-exact (em-dash included)
    parsed = parse_knotica_commit(subject)
    assert parsed == {"op": "write_page", "topic": "agentic-systems", "title": "Agent Memory"}


def test_commit_returns_the_new_head_sha(vcs: VaultVcs, template_vault: Path) -> None:
    before = git_head_sha(template_vault)
    (template_vault / "new-page.md").write_text("content\n", encoding="utf-8")

    returned = vcs.commit_paths(["new-page.md"], A_GRAMMAR_MESSAGE)

    assert returned == git_head_sha(template_vault)
    assert returned != before


def test_an_empty_path_list_is_refused_before_touching_git(
    vcs: VaultVcs, template_vault: Path
) -> None:
    before = git_commit_count(template_vault)

    with pytest.raises(ValueError, match="path"):
        vcs.commit_paths([], A_GRAMMAR_MESSAGE)

    assert git_commit_count(template_vault) == before


def test_an_absolute_path_is_refused(vcs: VaultVcs, template_vault: Path) -> None:
    outside = template_vault.parent / "outside.md"
    outside.write_text("not vault content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="relative"):
        vcs.commit_paths([outside], A_GRAMMAR_MESSAGE)


def test_a_commit_with_nothing_to_record_raises_a_typed_git_error(
    vcs: VaultVcs, template_vault: Path
) -> None:
    before = git_commit_count(template_vault)

    with pytest.raises(GitError) as exc_info:
        vcs.commit_paths(["index.md"], A_GRAMMAR_MESSAGE)  # tracked, unchanged

    assert git_commit_count(template_vault) == before
    error = exc_info.value
    assert error.command, "GitError must carry the failing command for diagnostics"
    assert error.output, "GitError must carry git's output for diagnostics"


# ---------------------------------------------------------------------------
# Rollback: scoping
# ---------------------------------------------------------------------------


def test_rollback_restores_named_paths_to_their_state_at_the_ref(
    vcs: VaultVcs, template_vault: Path
) -> None:
    original = (template_vault / "index.md").read_text(encoding="utf-8")
    ref = git_head_sha(template_vault)
    (template_vault / "index.md").write_text("half-written operation output\n", encoding="utf-8")

    vcs.rollback_paths(["index.md"], ref)

    assert (template_vault / "index.md").read_text(encoding="utf-8") == original
    assert git_status_porcelain(template_vault) == ""


def test_rollback_deletes_paths_created_since_the_ref(vcs: VaultVcs, template_vault: Path) -> None:
    ref = git_head_sha(template_vault)
    (template_vault / "created-unstaged.md").write_text("mid-op write\n", encoding="utf-8")
    (template_vault / "created-staged.md").write_text("mid-op write, staged\n", encoding="utf-8")
    run_git(template_vault, "add", "created-staged.md")

    vcs.rollback_paths(["created-unstaged.md", "created-staged.md"], ref)

    assert not (template_vault / "created-unstaged.md").exists()
    assert not (template_vault / "created-staged.md").exists()
    assert git_status_porcelain(template_vault) == ""


def test_a_foreign_uncommitted_edit_survives_a_rollback(
    vcs: VaultVcs, template_vault: Path
) -> None:
    original_index = (template_vault / "index.md").read_text(encoding="utf-8")
    ref = git_head_sha(template_vault)
    untracked_note = make_foreign_edit(template_vault)
    tracked_edit = make_foreign_edit(
        template_vault, relpath="log.md", content="user annotated the log in Obsidian\n"
    )
    # The failing operation had touched two paths before dying.
    (template_vault / "tx-page.md").write_text("half-written\n", encoding="utf-8")
    (template_vault / "index.md").write_text("half-updated index\n", encoding="utf-8")

    vcs.rollback_paths(["tx-page.md", "index.md"], ref)

    untracked_note.assert_intact()
    tracked_edit.assert_intact()
    assert not (template_vault / "tx-page.md").exists()
    assert (template_vault / "index.md").read_text(encoding="utf-8") == original_index


def test_rollback_with_no_paths_is_refused(vcs: VaultVcs) -> None:
    with pytest.raises(ValueError, match="path"):
        vcs.rollback_paths([], "HEAD")


# ---------------------------------------------------------------------------
# API surface: the unscoped capability must not exist
# ---------------------------------------------------------------------------

_FORBIDDEN_NAME_FRAGMENTS = ("add_all", "stage_all", "reset", "hard", "clean", "checkout_all")


def test_the_vcs_surface_exposes_no_unscoped_mutation_primitive() -> None:
    from knotica.core import vcs as vcs_module

    public_names = [name for name in dir(VaultVcs) if not name.startswith("_")]
    public_names += [
        name
        for name in dir(vcs_module)
        if not name.startswith("_") and callable(getattr(vcs_module, name))
    ]
    offenders = [
        name
        for name in public_names
        if any(fragment in name.lower() for fragment in _FORBIDDEN_NAME_FRAGMENTS)
    ]
    assert offenders == [], (
        f"unscoped/history-destroying primitives must not exist on the vcs surface: {offenders}"
    )
    # The scoped replacements are the only mutating surface.
    assert {"commit_paths", "rollback_paths"} <= set(public_names)


# ---------------------------------------------------------------------------
# Inspection + construction
# ---------------------------------------------------------------------------


def test_is_dirty_scopes_to_the_given_paths(vcs: VaultVcs, template_vault: Path) -> None:
    make_foreign_edit(template_vault, relpath="index.md", content="edited\n")

    assert vcs.is_dirty() is True
    assert vcs.is_dirty(["index.md"]) is True
    assert vcs.is_dirty(["log.md"]) is False


def test_a_missing_vault_root_is_rejected_at_construction(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        VaultVcs(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# clone_to: the frozen-corpus mechanism
# ---------------------------------------------------------------------------


def _blind_ambient_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the process look like a machine with no git identity configured.

    ``VaultVcs._run`` copies ``os.environ`` verbatim -- unlike the suite's
    ``run_git`` helper, it does not blind git's global/system config. So a
    commit it drives would otherwise borrow the developer's ambient global
    identity and silently mask a *missing* clone-local identity. Blinding global
    + system config and clearing the ``GIT_*_NAME``/``GIT_*_EMAIL`` overrides
    leaves clone-local config as the only possible source of committer
    identity -- which only ``clone_to`` stamps. This is what makes the identity
    carry-forward assertions below bite on a normally-configured machine instead
    of passing vacuously.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for override in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(override, raising=False)


def test_a_fresh_clone_is_pinned_to_the_source_head(vcs: VaultVcs, tmp_path: Path) -> None:
    clone_root = tmp_path / "clone"

    clone_vcs = vcs.clone_to(clone_root)

    assert isinstance(clone_vcs, VaultVcs), "clone_to returns a VaultVcs bound to the clone"
    assert clone_vcs.root == clone_root.resolve(), "the returned wrapper is rooted at the clone"
    assert clone_vcs.head_sha() == vcs.head_sha(), (
        "a fresh clone snapshots the source HEAD -- the corpus_ref the eval pins"
    )


def test_clone_at_an_explicit_sha_pins_that_ref_not_the_source_head(
    vcs: VaultVcs, template_vault: Path, tmp_path: Path
) -> None:
    older_sha = vcs.head_sha()
    (template_vault / "advance.md").write_text("newer state\n", encoding="utf-8")
    newer_sha = vcs.commit_paths(["advance.md"], A_GRAMMAR_MESSAGE)
    assert newer_sha != older_sha, "precondition: the source advanced past the pinned ref"

    clone_vcs = vcs.clone_to(tmp_path / "clone", ref=older_sha)

    assert clone_vcs.head_sha() == older_sha, "the clone is checked out at the requested ref"
    assert clone_vcs.head_sha() != newer_sha, "not the source's newer HEAD"


def test_clone_at_an_explicit_branch_pins_that_branch(
    vcs: VaultVcs, template_vault: Path, tmp_path: Path
) -> None:
    pinned_sha = vcs.head_sha()
    run_git(template_vault, "branch", "eval-pin", pinned_sha)
    (template_vault / "advance.md").write_text("newer state\n", encoding="utf-8")
    vcs.commit_paths(["advance.md"], A_GRAMMAR_MESSAGE)

    clone_vcs = vcs.clone_to(tmp_path / "clone", ref="eval-pin")

    assert clone_vcs.head_sha() == pinned_sha, "the clone is checked out at the named branch"
    assert clone_vcs.head_sha() != vcs.head_sha(), "not the source's advanced HEAD"


def test_a_commit_on_the_clone_succeeds_with_no_ambient_git_identity(
    vcs: VaultVcs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh `git clone` inherits no local committer identity; clone_to must
    # stamp one so a later eval transaction commits cleanly on an identity-less
    # machine. Blinding the ambient identity is what makes this assertion bite.
    _blind_ambient_git_identity(monkeypatch)
    clone_vcs = vcs.clone_to(tmp_path / "clone")

    (clone_vcs.root / "eval-output.md").write_text("written on the clone\n", encoding="utf-8")
    returned = clone_vcs.commit_paths(["eval-output.md"], A_GRAMMAR_MESSAGE)

    assert returned == clone_vcs.head_sha(), (
        "the commit landed on clone-local identity alone -- no ambient git identity was available"
    )


def test_a_commit_on_the_clone_never_reaches_the_source_history(
    vcs: VaultVcs, template_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _blind_ambient_git_identity(monkeypatch)
    clone_vcs = vcs.clone_to(tmp_path / "clone")
    source_subjects_before = git_commit_subjects(template_vault)

    (clone_vcs.root / "clone-only.md").write_text("only on the clone\n", encoding="utf-8")
    clone_vcs.commit_paths(["clone-only.md"], A_GRAMMAR_MESSAGE)

    assert git_commit_subjects(template_vault) == source_subjects_before, (
        "the clone is an independent repository -- its commits never enter the source's log"
    )


def test_cloning_leaves_the_source_byte_identical(
    vcs: VaultVcs, template_vault: Path, tmp_path: Path
) -> None:
    head_before = vcs.head_sha()
    count_before = git_commit_count(template_vault)
    status_before = git_status_porcelain(template_vault)

    vcs.clone_to(tmp_path / "clone")

    assert vcs.head_sha() == head_before, "the source HEAD must not move"
    assert git_commit_count(template_vault) == count_before, "the source gains no commit"
    assert git_status_porcelain(template_vault) == status_before, "the source tree is unchanged"


def test_cloning_a_non_repository_source_raises_a_typed_git_error(tmp_path: Path) -> None:
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    non_repo_vcs = VaultVcs(plain_dir)  # an existing directory, but not a git work tree

    with pytest.raises(GitError) as exc_info:
        non_repo_vcs.clone_to(tmp_path / "clone")

    error = exc_info.value
    assert error.command, "GitError must carry the failing command for diagnostics"
    assert error.output, "GitError must carry git's output for diagnostics"
