"""Tests for branch scoreboard assembly and loop promote validation."""

from pathlib import Path

import pytest

from knotica.core.arena import ArenaState, ArenaStage, ArenaVariant, write_arena_state
from knotica.core.branch_delete import branch_delete
from knotica.core.branch_scoreboard import gather_branch_scoreboard
from knotica.core.compile_state import (
    CompileHistoryEntry,
    CompileStage,
    CompileState,
    compile_history_id,
    read_compile_state,
    write_compile_state,
)
from knotica.core.loop import DEFAULT_BRANCH_PREFIX, RESULT_BRANCH_PREFIX
from knotica.core.loop_promote import loop_promote, loop_result_branch_name
from knotica.core.loop_state import empty_loop_state, write_loop_state
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"


def _seed_compile_branch(vcs: VaultVcs, branch: str, *, marker_name: str) -> None:
    """Create a compile branch with a unique commit so merge detection stays accurate."""
    default = vcs.default_branch()
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    marker = vcs.root / TOPIC / ".knotica" / marker_name
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{marker_name}\n", encoding="utf-8")
    run_git(vcs.root, "add", "-A")
    run_git(vcs.root, "commit", "-m", f"test: {marker_name}")
    vcs.checkout_branch(default)


def test_branch_scoreboard_includes_default_compile_and_loop(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.57}),
        title="seed baseline",
    )
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=f"compile/{TOPIC}/abc123def456",
            scalar_before=0.55,
            scalar_after=0.62,
            updated_at="2026-07-17T12:00:00Z",
        ),
        title="compile done",
    )
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    compile_branch = f"compile/{TOPIC}/abc123def456"
    loop_branch = f"{DEFAULT_BRANCH_PREFIX}demo-wound"
    _seed_compile_branch(vcs, compile_branch, marker_name="compile-scoreboard-open.txt")
    if vcs.branch_exists(loop_branch):
        vcs.delete_branch(loop_branch, force=True)
    vcs.create_branch(loop_branch, default)

    payload = gather_branch_scoreboard(store, template_vault, TOPIC)
    assert payload["topic"] == TOPIC
    assert payload["schema_version"] == 3
    assert payload["baseline"] == 0.57
    assert payload["baseline_meta"]["scope"] == "topic"
    assert payload["baseline_meta"]["path"] == f"{TOPIC}/.knotica/loop-state.json"
    assert payload["baseline_meta"]["frozen"] is True
    assert payload["open_compile_branch"] == compile_branch
    kinds = {row["kind"] for row in payload["entries"]}
    assert "default" in kinds
    assert "compile" in kinds
    assert "loop_candidate" in kinds
    compile_row = next(row for row in payload["entries"] if row["kind"] == "compile")
    assert compile_row["scalar"] == 0.62
    assert compile_row["delta"] == pytest.approx(0.05)
    assert compile_row["delta_before"] == pytest.approx(0.07)
    assert compile_row["beats_baseline"] is True
    assert compile_row["slot"] == "open"
    assert compile_row["status"] == "ready-to-promote"
    assert compile_row["promotable"] is True
    assert compile_row.get("deletable") is not True


def test_branch_scoreboard_open_vs_history_compile_branches(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.57}),
        title="seed baseline",
    )
    vcs = VaultVcs(template_vault)
    older = f"compile/{TOPIC}/older00000001"
    newer = f"compile/{TOPIC}/newer00000002"
    _seed_compile_branch(vcs, older, marker_name="compile-scoreboard-older.txt")
    _seed_compile_branch(vcs, newer, marker_name="compile-scoreboard-newer.txt")
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=newer,
            scalar_before=0.55,
            scalar_after=0.52,
            updated_at="2026-07-17T14:00:00Z",
        ),
        title="compile newer under baseline",
    )

    payload = gather_branch_scoreboard(store, template_vault, TOPIC)
    compile_rows = [row for row in payload["entries"] if row["kind"] == "compile"]
    assert len(compile_rows) == 2
    open_row = next(row for row in compile_rows if row["slot"] == "open")
    history_rows = [row for row in compile_rows if row["slot"] == "history"]
    assert open_row["name"] == newer
    assert open_row["status"] == "under-baseline"
    assert open_row["beats_baseline"] is False
    assert open_row["promotable"] is False
    assert open_row["deletable"] is True
    assert len(history_rows) == 1
    assert history_rows[0]["name"] == older
    assert history_rows[0]["promotable"] is False
    assert history_rows[0]["deletable"] is True


def test_branch_scoreboard_merged_compile_branch_deletable_not_promotable(
    template_vault: Path,
) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.57}),
        title="seed baseline",
    )
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = f"compile/{TOPIC}/merged00000001"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    marker = template_vault / TOPIC / ".knotica" / "merged-compile-marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("merged\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: merged compile branch")
    vcs.checkout_branch(default)
    vcs.merge_branch(branch, no_ff=True)
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=branch,
            scalar_before=0.55,
            scalar_after=0.62,
            updated_at="2026-07-17T16:00:00Z",
        ),
        title="compile merged winner",
    )

    payload = gather_branch_scoreboard(store, template_vault, TOPIC)
    compile_row = next(row for row in payload["entries"] if row["kind"] == "compile")
    assert compile_row["name"] == branch
    assert compile_row["slot"] == "open"
    assert compile_row["status"] == "promoted"
    assert compile_row["beats_baseline"] is True
    assert compile_row["promotable"] is False
    assert compile_row["deletable"] is True


def test_branch_delete_allows_merged_winner(template_vault: Path) -> None:

    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = f"compile/{TOPIC}/merged00000002"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    marker = template_vault / TOPIC / ".knotica" / "merged-delete-marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("delete me\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: merged compile delete")
    vcs.checkout_branch(default)
    vcs.merge_branch(branch, no_ff=True)
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=branch,
            scalar_before=0.55,
            scalar_after=0.62,
        ),
        title="compile pointer",
    )

    dry = branch_delete(store, template_vault, TOPIC, branch, apply=False)
    assert dry.get("error") is None
    assert dry["mode"] == "dry-run"
    assert vcs.branch_exists(branch)

    applied = branch_delete(store, template_vault, TOPIC, branch, apply=True)
    assert applied.get("error") is None
    assert applied["deleted"] is True
    assert not vcs.branch_exists(branch)


def test_branch_scoreboard_surfaces_arena_variants(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.57}),
        title="seed baseline",
    )
    write_arena_state(
        store,
        template_vault,
        ArenaState(
            topic=TOPIC,
            race_id="race123",
            stage=ArenaStage.completed,
            baseline_scalar=0.57,
            variants=[
                ArenaVariant(id="v1", label="variant-1", scalar=0.59, status="winner"),
                ArenaVariant(id="v2", label="variant-2", scalar=0.52, status="lost"),
            ],
            winner_id="v1",
            winner_scalar=0.59,
        ),
        title="arena test",
    )
    payload = gather_branch_scoreboard(store, template_vault, TOPIC)
    arena_rows = [row for row in payload["entries"] if row["kind"] == "arena_variant"]
    assert len(arena_rows) >= 2
    winner = next(row for row in arena_rows if row["status"] == "winner")
    assert winner["scalar"] == 0.59


def test_loop_promote_rejects_a_branch_with_the_wrong_prefix_as_invalid_argument(
    template_vault: Path,
) -> None:
    """A branch outside loop/r/ or loop/c/ is an argument problem, not a stale cursor."""
    from knotica.core.errors import ErrorCode

    store = LocalFSStore(template_vault)
    payload = loop_promote(store, template_vault, TOPIC, "not-a-loop-branch", apply=False)
    assert payload["error"]["code"] == ErrorCode.INVALID_ARGUMENT.value


def test_loop_promote_rejects_missing_result_branch(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    payload = loop_promote(
        store,
        template_vault,
        TOPIC,
        f"{RESULT_BRANCH_PREFIX}deadbeef0000",
        apply=False,
    )
    assert payload.get("error") is not None


def test_loop_promote_dry_run_with_result_branch(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    result_branch = loop_result_branch_name("abc123def4567890")
    if vcs.branch_exists(result_branch):
        vcs.delete_branch(result_branch, force=True)
    vcs.create_branch(result_branch, default)

    dry = loop_promote(store, template_vault, TOPIC, result_branch, apply=False)
    assert dry.get("error") is None
    assert dry["mode"] == "dry-run"
    assert dry["merged"] is False
    assert dry["branch"] == result_branch


def test_loop_promote_resolves_loop_candidate(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    candidate = f"{DEFAULT_BRANCH_PREFIX}manual-keep"
    if vcs.branch_exists(candidate):
        vcs.delete_branch(candidate, force=True)
    vcs.create_branch(candidate, default)
    vcs.checkout_branch(candidate)
    marker = template_vault / TOPIC / ".knotica" / "loop-promote-test.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("marker\n", encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: loop promote candidate")
    tip_sha = vcs.head_sha()
    result_branch = loop_result_branch_name(tip_sha)
    if vcs.branch_exists(result_branch):
        vcs.delete_branch(result_branch, force=True)
    vcs.create_branch(result_branch, tip_sha)
    vcs.checkout_branch(default)

    dry = loop_promote(store, template_vault, TOPIC, candidate, apply=False)
    assert dry.get("error") is None
    assert dry["branch"] == result_branch


def test_mcp_branch_scoreboard_registered() -> None:
    """`branch_scoreboard`/`loop_promote`/`branch_promote`/`branch_delete` were
    fully retired, not deprecated; the `branches` dispatcher is the sole
    registration to check for now."""
    from knotica.mcp_server.server import build_server

    mcp = build_server()
    names = {tool.name for tool in mcp._tool_manager.list_tools()}  # noqa: SLF001
    assert "branches" in names


def test_branch_delete_dry_run_and_apply(template_vault: Path) -> None:

    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = f"compile/{TOPIC}/deadbeef0001"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=branch,
            scalar_before=0.55,
            scalar_after=0.52,
        ),
        title="compile pointer",
    )

    dry = branch_delete(store, template_vault, TOPIC, branch, apply=False)
    assert dry.get("error") is None
    assert dry["mode"] == "dry-run"
    assert dry["deleted"] is False
    assert vcs.branch_exists(branch)

    applied = branch_delete(store, template_vault, TOPIC, branch, apply=True)
    assert applied.get("error") is None
    assert applied["mode"] == "apply"
    assert applied["deleted"] is True
    assert applied["compile_state_cleared"] is True
    assert not vcs.branch_exists(branch)

    state = read_compile_state(store, TOPIC)
    assert state is not None
    assert state.branch is None
    assert state.scalar_after == 0.52


def test_branch_delete_rejects_default_and_head(template_vault: Path) -> None:

    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    vcs.checkout_branch(default)

    default_result = branch_delete(store, template_vault, TOPIC, default, apply=False)
    assert default_result.get("error") is not None

    branch = f"compile/{TOPIC}/checkedout01"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)

    head_result = branch_delete(store, template_vault, TOPIC, branch, apply=False)
    assert head_result.get("error") is not None
    vcs.checkout_branch(default)


def test_branch_delete_rejects_a_branch_with_the_wrong_prefix_as_invalid_argument(
    template_vault: Path,
) -> None:
    """A branch outside compile/<topic>/ is an argument problem, not a stale cursor."""
    from knotica.core.errors import ErrorCode

    store = LocalFSStore(template_vault)
    payload = branch_delete(store, template_vault, TOPIC, "not-a-compile-branch", apply=False)
    assert payload["error"]["code"] == ErrorCode.INVALID_ARGUMENT.value


def test_branch_scoreboard_archived_deleted_compile_history(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    write_loop_state(
        store,
        template_vault,
        empty_loop_state(TOPIC).model_copy(update={"baseline_scalar": 0.57}),
        title="seed baseline",
    )
    vcs = VaultVcs(template_vault)
    branch = f"compile/{TOPIC}/archived000001"
    default = vcs.default_branch()
    base_sha = vcs.ref_sha(default)
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    query = template_vault / ".knotica" / "prompts" / "query.md"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text("# archived compile prompt\n", encoding="utf-8")
    run_git(template_vault, "add", ".knotica/prompts/query.md")
    run_git(template_vault, "commit", "-m", "test: archived compile prompt")
    head_sha = vcs.head_sha()
    vcs.checkout_branch(default)
    vcs.merge_branch(branch, no_ff=True)
    merge_sha = vcs.head_sha()
    vcs.delete_branch(branch, force=True)

    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=None,
            scalar_before=0.55,
            scalar_after=0.62,
            history=[
                CompileHistoryEntry(
                    history_id=compile_history_id(branch),
                    branch=branch,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    merge_sha=merge_sha,
                    scalar_before=0.55,
                    scalar_after=0.62,
                    promoted=True,
                    branch_deleted=True,
                )
            ],
        ),
        title="archived compile history",
    )

    payload = gather_branch_scoreboard(store, template_vault, TOPIC)
    archived = [
        row
        for row in payload["entries"]
        if row["kind"] == "compile" and row.get("slot") == "archived"
    ]
    assert len(archived) == 1
    row = archived[0]
    assert row["name"] == branch
    assert row["branch_deleted"] is True
    assert row["diff_available"] is True
    assert row["history_id"] == compile_history_id(branch)
    assert "branch deleted" in (row.get("note") or "")
