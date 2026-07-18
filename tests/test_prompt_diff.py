"""Tests for prompt diff assembly and git helpers."""

from pathlib import Path

import pytest

from knotica.core.branch_delete import branch_delete
from knotica.core.compile_promote import compile_promote
from knotica.core.compile_state import (
    CompileHistoryEntry,
    CompileStage,
    CompileState,
    compile_history_id,
    read_compile_state,
    write_compile_state,
)
from knotica.core.errors import KnoticaError
from knotica.core.metrics import read_last_metrics
from knotica.core.compiled import (
    CompiledArtifact,
    CompiledDemo,
    artifact_write_bodies,
    compiled_artifact_path,
)
from knotica.core.prompt_diff import compiled_prompt_diff, prompt_diff, resolve_query_path_at
from knotica.core.vcs import VaultVcs
from knotica.store import LocalFSStore
from support.vault import run_git

TOPIC = "agentic-systems"
ROOT_QUERY = ".knotica/prompts/query.md"


def _write_and_commit(vcs: VaultVcs, path: str, body: str, message: str) -> None:
    target = vcs.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    run_git(vcs.root, "add", path)
    run_git(vcs.root, "commit", "-m", message)


def _seed_compile_branch_with_prompt(
    vcs: VaultVcs,
    branch: str,
    *,
    prompt_body: str,
) -> tuple[str, str]:
    default = vcs.default_branch()
    base_sha = vcs.ref_sha(default)
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    _write_and_commit(vcs, ROOT_QUERY, prompt_body, f"test: prompt on {branch}")
    head_sha = vcs.head_sha()
    vcs.checkout_branch(default)
    return base_sha, head_sha


def test_resolve_query_path_prefers_topic_override(template_vault: Path) -> None:
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    override = f"{TOPIC}/.knotica/prompts/query.md"
    _write_and_commit(vcs, override, "topic override\n", "test: topic query override")
    resolved = resolve_query_path_at(vcs, TOPIC, default, default)
    assert resolved == override


def test_prompt_diff_without_branch_shows_last_change(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    original = vcs.read_file_at("HEAD", ROOT_QUERY) or ""
    _write_and_commit(
        vcs,
        ROOT_QUERY,
        original + "\n<!-- prompt-diff marker -->\n",
        "test: tweak query prompt",
    )

    payload = prompt_diff(store, template_vault, TOPIC)
    assert payload["path"] == ROOT_QUERY
    assert payload["head_ref"] == "HEAD"
    assert payload["empty"] is False
    assert payload["hunks"]
    joined = "\n".join(line["text"] for hunk in payload["hunks"] for line in hunk["lines"])
    assert "prompt-diff marker" in joined


def test_prompt_diff_branch_vs_default(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = f"compile/{TOPIC}/promptdiff"
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    _write_and_commit(
        vcs,
        ROOT_QUERY,
        "# compiled query prompt\n\nUse citations.\n",
        "test: compile branch prompt",
    )
    vcs.checkout_branch(default)

    payload = prompt_diff(store, template_vault, TOPIC, branch=branch)
    assert payload["head_ref"] == branch
    assert payload["base_ref"] == default
    assert payload["path"] == ROOT_QUERY
    assert payload["empty"] is False
    adds = [line for hunk in payload["hunks"] for line in hunk["lines"] if line["type"] == "add"]
    assert any("compiled query prompt" in line["text"] for line in adds)


def test_prompt_diff_with_explicit_shas(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    branch = f"compile/{TOPIC}/sha-diff"
    base_sha, head_sha = _seed_compile_branch_with_prompt(
        vcs,
        branch,
        prompt_body="# sha diff prompt\n\nExplicit refs.\n",
    )

    payload = prompt_diff(
        store,
        template_vault,
        TOPIC,
        base_ref=base_sha,
        head_ref=head_sha,
    )
    assert payload["source"] == "refs"
    assert payload["base_ref"] == base_sha
    assert payload["head_ref"] == head_sha
    assert payload["empty"] is False


def test_prompt_diff_history_id_after_branch_delete(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    branch = f"compile/{TOPIC}/deleted00001"
    base_sha, head_sha = _seed_compile_branch_with_prompt(
        vcs,
        branch,
        prompt_body="# deleted branch prompt\n\nHistory lookup.\n",
    )
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=branch,
            scalar_before=0.55,
            scalar_after=0.62,
            history=[
                CompileHistoryEntry(
                    history_id=compile_history_id(branch),
                    branch=branch,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    scalar_before=0.55,
                    scalar_after=0.62,
                    branch_deleted=True,
                )
            ],
        ),
        title="seed deleted compile history",
    )

    payload = prompt_diff(
        store,
        template_vault,
        TOPIC,
        history_id=compile_history_id(branch),
    )
    assert payload["source"] == "history"
    assert payload["empty"] is False
    joined = "\n".join(line["text"] for hunk in payload["hunks"] for line in hunk["lines"])
    assert "deleted branch prompt" in joined


def test_prompt_diff_merge_commit_fallback(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    default = vcs.default_branch()
    branch = f"compile/{TOPIC}/merge00000001"
    base_sha, head_sha = _seed_compile_branch_with_prompt(
        vcs,
        branch,
        prompt_body="# merge fallback prompt\n\nFrom merge parents.\n",
    )
    vcs.merge_branch(branch, no_ff=True)
    merge_sha = vcs.head_sha()
    vcs.delete_branch(branch, force=True)

    payload = prompt_diff(store, template_vault, TOPIC, branch=branch)
    assert payload["source"] == "merge_commit"
    assert payload["empty"] is False
    assert vcs.merge_parents(merge_sha) == (payload["base_ref"], payload["head_ref"])


def test_prompt_diff_missing_shas_raises(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    branch = f"compile/{TOPIC}/missing000001"
    with pytest.raises(KnoticaError, match="No preserved SHAs"):
        prompt_diff(store, template_vault, TOPIC, branch=branch)


def test_branch_delete_preserves_history_shas(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    branch = f"compile/{TOPIC}/preserve00001"
    base_sha, head_sha = _seed_compile_branch_with_prompt(
        vcs,
        branch,
        prompt_body="# preserve on delete\n",
    )
    write_compile_state(
        store,
        template_vault,
        CompileState(
            topic=TOPIC,
            stage=CompileStage.completed,
            branch=branch,
            scalar_before=0.55,
            scalar_after=0.62,
            history=[
                CompileHistoryEntry(
                    history_id=compile_history_id(branch),
                    branch=branch,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    scalar_before=0.55,
                    scalar_after=0.62,
                )
            ],
        ),
        title="compile pointer",
    )

    applied = branch_delete(store, template_vault, TOPIC, branch, apply=True)
    assert applied.get("error") is None
    assert not vcs.branch_exists(branch)

    state = read_compile_state(store, TOPIC)
    assert state is not None
    entry = state.history[0]
    assert entry.branch_deleted is True
    assert entry.base_sha == base_sha
    assert entry.head_sha == head_sha

    payload = prompt_diff(
        store,
        template_vault,
        TOPIC,
        history_id=entry.history_id,
    )
    assert payload["empty"] is False


def test_compile_promote_records_merge_shas(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    branch = f"compile/{TOPIC}/promote000001"
    _seed_compile_branch_with_prompt(
        vcs,
        branch,
        prompt_body="# promoted prompt\n",
    )
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

    result = compile_promote(store, template_vault, TOPIC, branch, apply=True)
    assert result.get("error") is None
    merge_sha = result["commit_sha"]

    state = read_compile_state(store, TOPIC)
    assert state is not None
    entry = next(row for row in state.history if row.branch == branch)
    assert entry.promoted is True
    assert entry.merge_sha == merge_sha
    assert entry.base_sha and entry.head_sha
    parents = vcs.merge_parents(merge_sha)
    assert parents == (entry.base_sha, entry.head_sha)

    last = read_last_metrics(store, TOPIC)
    assert last is not None
    assert last.scalar == 0.62


def _write_compiled_on_branch(
    vcs: VaultVcs,
    branch: str,
    *,
    instructions: str,
    demos: tuple[CompiledDemo, ...] = (),
) -> tuple[str, str]:
    default = vcs.default_branch()
    base_sha = vcs.ref_sha(default)
    if vcs.branch_exists(branch):
        vcs.delete_branch(branch, force=True)
    vcs.create_branch(branch, default)
    vcs.checkout_branch(branch)
    artifact = CompiledArtifact(
        optimized_instructions=instructions,
        metrics={"baseline": 0.4, "compiled": 0.55},
        demos=demos,
    )
    art_body, man_body = artifact_write_bodies(artifact)
    art_path = compiled_artifact_path(TOPIC)
    man_path = f"{TOPIC}/.knotica/compiled/MANIFEST.json"
    _write_and_commit(vcs, art_path, art_body, f"test: compiled on {branch}")
    manifest = vcs.root / man_path
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(man_body, encoding="utf-8")
    run_git(vcs.root, "add", man_path)
    run_git(vcs.root, "commit", "-m", f"test: manifest on {branch}")
    head_sha = vcs.head_sha()
    vcs.checkout_branch(default)
    return base_sha, head_sha


def _sample_demos() -> tuple[CompiledDemo, ...]:
    return (
        CompiledDemo(
            "What gains does AWM report on SWE-bench?",
            "AWM reports roughly 12% absolute gains on SWE-bench.",
            ("wang2024awm",),
        ),
    )


def test_compiled_prompt_diff_head_vs_vault(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    vault_body = vcs.read_file_at("HEAD", ROOT_QUERY) or ""
    instructions = vault_body + "\n## Compiled guidance\nPrefer citations.\n"
    art_path = compiled_artifact_path(TOPIC)
    artifact = CompiledArtifact(optimized_instructions=instructions, demos=_sample_demos())
    art_body, man_body = artifact_write_bodies(artifact)
    _write_and_commit(vcs, art_path, art_body, "test: active compiled artifact")
    man_path = f"{TOPIC}/.knotica/compiled/MANIFEST.json"
    _write_and_commit(vcs, man_path, man_body, "test: compiled manifest")

    payload = prompt_diff(store, template_vault, TOPIC, mode="compiled")
    assert payload["source"] == "compiled"
    assert payload["comparison"] == "vault_query_md_vs_compiled_program"
    assert payload["demo_count"] == 1
    assert payload["artifact_path"] == art_path
    assert payload["head_sha"]
    assert payload["empty"] is False
    joined = "\n".join(line["text"] for hunk in payload["hunks"] for line in hunk["lines"])
    assert "Compiled guidance" in joined
    assert "## Compiled few-shot demos" in joined
    assert "What gains does AWM report on SWE-bench?" in joined


def test_compiled_prompt_diff_open_branch(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    branch = f"compile/{TOPIC}/compilediff"
    vault_body = vcs.read_file_at("HEAD", ROOT_QUERY) or ""
    _write_compiled_on_branch(
        vcs,
        branch,
        instructions=vault_body + "\n## Branch compile\nExtra instruction.\n",
        demos=_sample_demos(),
    )

    payload = compiled_prompt_diff(store, template_vault, TOPIC, branch=branch)
    assert payload["source"] == "compiled"
    assert payload["branch"] == branch
    assert payload["base_sha"]
    assert payload["head_sha"]
    assert payload["demo_count"] == 1
    assert payload["empty"] is False
    joined = "\n".join(line["text"] for hunk in payload["hunks"] for line in hunk["lines"])
    assert "Branch compile" in joined
    assert "## Compiled few-shot demos" in joined
    assert "What gains does AWM report on SWE-bench?" in joined


def test_compiled_prompt_diff_empty_only_when_program_matches(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    vault_body = vcs.read_file_at("HEAD", ROOT_QUERY) or ""
    art_path = compiled_artifact_path(TOPIC)
    artifact = CompiledArtifact(optimized_instructions=vault_body)
    art_body, man_body = artifact_write_bodies(artifact)
    _write_and_commit(vcs, art_path, art_body, "test: matching compiled artifact")
    man_path = f"{TOPIC}/.knotica/compiled/MANIFEST.json"
    _write_and_commit(vcs, man_path, man_body, "test: matching manifest")

    payload = prompt_diff(store, template_vault, TOPIC, mode="compiled")
    assert payload["empty"] is True


def test_compiled_prompt_diff_not_empty_when_only_demos_differ(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    vcs = VaultVcs(template_vault)
    vault_body = vcs.read_file_at("HEAD", ROOT_QUERY) or ""
    art_path = compiled_artifact_path(TOPIC)
    artifact = CompiledArtifact(optimized_instructions=vault_body, demos=_sample_demos())
    art_body, man_body = artifact_write_bodies(artifact)
    _write_and_commit(vcs, art_path, art_body, "test: demos-only delta")
    man_path = f"{TOPIC}/.knotica/compiled/MANIFEST.json"
    _write_and_commit(vcs, man_path, man_body, "test: demos-only manifest")

    payload = prompt_diff(store, template_vault, TOPIC, mode="compiled")
    assert payload["empty"] is False
    joined = "\n".join(line["text"] for hunk in payload["hunks"] for line in hunk["lines"])
    assert "## Compiled few-shot demos" in joined


def test_compiled_prompt_diff_git_mode_unchanged(template_vault: Path) -> None:
    store = LocalFSStore(template_vault)
    payload = prompt_diff(store, template_vault, TOPIC, mode="git")
    assert payload.get("source") != "compiled"
    assert "comparison" not in payload or payload.get("comparison") is None
