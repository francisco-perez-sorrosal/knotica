"""Deterministic git diff for vault ``query.md`` (topic override or root default).

Used by the dashboard scoreboard and compile panel to show what compile/loop
changed in the operation prompt. Read-only — no commits.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from knotica.core.compiled import CompiledArtifact, compiled_artifact_path, format_compiled_program
from knotica.core.compile_state import find_compile_history, read_compile_state
from knotica.core.errors import ErrorCode, KnoticaError
from knotica.core.prompts import override_prompt_path, resolve_prompt, root_prompt_path
from knotica.core.schema import validated_topic
from knotica.core.vcs import GitError, VaultVcs
from knotica.store import VaultStore

_SCHEMA_VERSION = 1
_MAX_HUNK_LINES = 400
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

LineType = Literal["context", "add", "del"]


@dataclass(frozen=True)
class DiffLine:
    type: LineType
    text: str
    old_no: int | None
    new_no: int | None


@dataclass(frozen=True)
class DiffHunk:
    header: str
    lines: tuple[DiffLine, ...]


def prompt_diff(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    *,
    branch: str | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
    history_id: str | None = None,
    mode: Literal["git", "compiled"] = "git",
) -> dict[str, Any]:
    """Return a structured unified diff for query prompts.

    ``mode="git"`` (default) diffs ``query.md`` between git refs (compile/loop branches).
    ``mode="compiled"`` diffs the vault ``query.md`` body against the full compiled runtime
    program (``optimized_instructions`` plus few-shot demos — same assembly as
    :class:`~knotica.evals.compiled_runner.CompiledRunner`).
    """
    if mode == "compiled":
        return compiled_prompt_diff(
            store,
            vault_root,
            topic,
            branch=branch,
            base_ref=base_ref,
            head_ref=head_ref,
            history_id=history_id,
        )

    cleaned_topic = validated_topic(topic)
    vcs = VaultVcs(vault_root)
    compile_state = read_compile_state(store, cleaned_topic)

    cleaned_base = base_ref.strip() if base_ref else None
    cleaned_head = head_ref.strip() if head_ref else None
    cleaned_history = history_id.strip() if history_id else None
    cleaned_branch = branch.strip() if branch else None

    source = "branch"
    if cleaned_base and cleaned_head:
        base_ref_resolved = cleaned_base
        head_ref_resolved = cleaned_head
        triple_dot = False
        source = "refs"
    elif cleaned_history or (cleaned_branch and not vcs.branch_exists(cleaned_branch)):
        resolved = _resolve_from_history(
            vcs,
            compile_state,
            branch=cleaned_branch,
            history_id=cleaned_history,
        )
        if resolved is None:
            raise KnoticaError(
                code=ErrorCode.INVALID_CURSOR,
                message=("No preserved SHAs for this compile run — can't rebuild diff."),
                fix=(
                    "Re-run compile, or recover parents from a merge commit on the "
                    "default branch (`git log --merges --grep='Merge branch'`)."
                ),
            )
        base_ref_resolved, head_ref_resolved, source = resolved
        triple_dot = False
    elif cleaned_branch:
        head_ref_resolved = cleaned_branch
        base_ref_resolved = vcs.default_branch()
        triple_dot = True
    else:
        head_ref_resolved = "HEAD"
        base_ref_resolved = _previous_ref_for_query(vcs, cleaned_topic)
        triple_dot = False

    path = resolve_query_path_at(
        vcs,
        cleaned_topic,
        head_ref_resolved,
        base_ref_resolved,
    )
    if path is None:
        raise KnoticaError(
            code=ErrorCode.PAGE_NOT_FOUND,
            message=(
                f"No query.md prompt found for topic {cleaned_topic!r} at "
                f"{head_ref_resolved} or {base_ref_resolved}."
            ),
            fix=("Ensure `.knotica/prompts/query.md` exists at the vault root or under the topic."),
        )

    patch = vcs.diff_between(
        base_ref_resolved,
        head_ref_resolved,
        path,
        triple_dot=triple_dot,
    )
    hunks, truncated = _parse_unified_patch(patch)
    return {
        "schema_version": _SCHEMA_VERSION,
        "topic": cleaned_topic,
        "path": path,
        "base_ref": base_ref_resolved,
        "head_ref": head_ref_resolved,
        "source": source,
        "patch": patch,
        "hunks": [
            {
                "header": hunk.header,
                "lines": [
                    {
                        "type": line.type,
                        "text": line.text,
                        "old_no": line.old_no,
                        "new_no": line.new_no,
                    }
                    for line in hunk.lines
                ],
            }
            for hunk in hunks
        ],
        "truncated": truncated,
        "empty": not patch.strip(),
    }


def compiled_prompt_diff(
    store: VaultStore,
    vault_root: str | Path,
    topic: str,
    *,
    branch: str | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
    history_id: str | None = None,
) -> dict[str, Any]:
    """Diff vault ``query.md`` against the full compiled runtime program.

    When ``branch`` is set (open compile candidate), the compiled artifact is read from
    that branch tip and ``query.md`` from the default branch — mirroring pre-promote review.
    When ``base_ref``/``head_ref`` (or ``history_id``) are set, both sides are read at
    those commits — for archived compile runs after branch delete.
    Otherwise both sides come from HEAD (active promoted compile on the live vault).
    """
    cleaned_topic = validated_topic(topic)
    vcs = VaultVcs(vault_root)
    artifact_rel = compiled_artifact_path(cleaned_topic)
    compile_state = read_compile_state(store, cleaned_topic)

    cleaned_base = base_ref.strip() if base_ref else None
    cleaned_head = head_ref.strip() if head_ref else None
    cleaned_history = history_id.strip() if history_id else None
    cleaned_branch = branch.strip() if branch else None

    history_entry = find_compile_history(
        compile_state,
        branch=cleaned_branch,
        history_id=cleaned_history,
    )

    query_ref: str | None
    compiled_ref: str
    source = "compiled"
    if cleaned_base and cleaned_head:
        query_ref = cleaned_base
        compiled_ref = cleaned_head
        source = "refs"
    elif cleaned_history or (cleaned_branch and not vcs.branch_exists(cleaned_branch)):
        resolved = _resolve_from_history(
            vcs,
            compile_state,
            branch=cleaned_branch,
            history_id=cleaned_history,
        )
        if resolved is None:
            raise KnoticaError(
                code=ErrorCode.INVALID_CURSOR,
                message="No preserved SHAs for this compile run — can't rebuild diff.",
                fix=(
                    "Re-run compile, or recover parents from a merge commit on the "
                    "default branch (`git log --merges --grep='Merge branch'`)."
                ),
            )
        query_ref, compiled_ref, source = resolved
    elif cleaned_branch:
        if not vcs.branch_exists(cleaned_branch):
            raise KnoticaError(
                code=ErrorCode.INVALID_CURSOR,
                message=f"Compile branch {cleaned_branch!r} does not exist locally.",
                fix="Re-run compile or fetch the branch before comparing prompts.",
            )
        query_ref = vcs.default_branch()
        compiled_ref = cleaned_branch
    else:
        query_ref = None
        compiled_ref = "HEAD"

    if query_ref is not None:
        path_at_base = resolve_query_path_at(
            vcs,
            cleaned_topic,
            compiled_ref,
            query_ref,
        )
        if path_at_base is None:
            raise KnoticaError(
                code=ErrorCode.PAGE_NOT_FOUND,
                message=f"No query.md prompt found for topic {cleaned_topic!r} at {query_ref}.",
                fix="Ensure `.knotica/prompts/query.md` exists at the vault root or under the topic.",
            )
        vault_path = path_at_base
        vault_body = vcs.read_file_at(query_ref, vault_path) or ""
    else:
        resolved = resolve_prompt(store, "query", cleaned_topic)
        vault_path = resolved.source_path or root_prompt_path("query")
        vault_body = resolved.body

    artifact = _load_compiled_at(vcs, cleaned_topic, compiled_ref)
    if artifact is None and compiled_ref == "HEAD":
        artifact = _load_compiled_from_store(store, cleaned_topic)
    if artifact is None:
        raise KnoticaError(
            code=ErrorCode.PAGE_NOT_FOUND,
            message=(f"No compiled query artifact for topic {cleaned_topic!r} at {artifact_rel}."),
            fix="Run compile and promote, or pass an open compile branch name.",
        )

    compiled_body = format_compiled_program(artifact)
    compiled_display = f"{artifact_rel} (runtime program)"
    vault_compare = vault_body.strip()
    patch = _text_unified_diff(
        vault_compare,
        compiled_body,
        fromfile=f"a/{vault_path}",
        tofile=f"b/{compiled_display}",
    )
    hunks, truncated = _parse_unified_patch(patch)

    metadata = _compiled_diff_metadata(
        vcs=vcs,
        compile_state=compile_state,
        history_entry=history_entry,
        branch=cleaned_branch,
        history_id=cleaned_history,
        query_ref=query_ref,
        compiled_ref=compiled_ref,
        source=source,
        artifact=artifact,
        artifact_path=artifact_rel,
    )

    return {
        "schema_version": _SCHEMA_VERSION,
        "topic": cleaned_topic,
        "path": f"{vault_path} ↔ {compiled_display}",
        "base_ref": metadata.get("base_sha") or query_ref or vault_path,
        "head_ref": metadata.get("head_sha") or compiled_ref,
        "source": source,
        "comparison": "vault_query_md_vs_compiled_program",
        "patch": patch,
        "hunks": [
            {
                "header": hunk.header,
                "lines": [
                    {
                        "type": line.type,
                        "text": line.text,
                        "old_no": line.old_no,
                        "new_no": line.new_no,
                    }
                    for line in hunk.lines
                ],
            }
            for hunk in hunks
        ],
        "truncated": truncated,
        "empty": not patch.strip(),
        **metadata,
    }


def _compiled_diff_metadata(
    *,
    vcs: VaultVcs,
    compile_state: Any,
    history_entry: Any,
    branch: str | None,
    history_id: str | None,
    query_ref: str | None,
    compiled_ref: str,
    source: str,
    artifact: CompiledArtifact,
    artifact_path: str,
) -> dict[str, Any]:
    """Attach compile-state SHAs and artifact stats for dashboard display."""
    entry = history_entry
    if entry is None and history_id:
        entry = find_compile_history(compile_state, history_id=history_id)

    base_sha = entry.base_sha if entry is not None else None
    head_sha = entry.head_sha if entry is not None else None
    merge_sha = entry.merge_sha if entry is not None else None
    resolved_branch = entry.branch if entry is not None else branch
    resolved_history_id = entry.history_id if entry is not None else history_id

    if source == "refs" and query_ref and compiled_ref:
        base_sha = base_sha or query_ref
        head_sha = head_sha or compiled_ref
    elif source in {"history", "merge_commit"} and query_ref and compiled_ref:
        base_sha = base_sha or query_ref
        head_sha = head_sha or compiled_ref
    elif branch and head_sha is None and compiled_ref != "HEAD":
        try:
            head_sha = vcs.ref_sha(compiled_ref)
        except GitError:
            head_sha = None
        if base_sha is None:
            try:
                base_sha = vcs.ref_sha(vcs.default_branch())
            except GitError:
                base_sha = None
    elif compiled_ref == "HEAD" and head_sha is None:
        try:
            head_sha = vcs.ref_sha("HEAD")
        except GitError:
            head_sha = None

    payload: dict[str, Any] = {
        "artifact_path": artifact_path,
        "demo_count": len(artifact.demos),
    }
    if base_sha:
        payload["base_sha"] = base_sha
    if head_sha:
        payload["head_sha"] = head_sha
    if merge_sha:
        payload["merge_sha"] = merge_sha
    if resolved_branch:
        payload["branch"] = resolved_branch
    if resolved_history_id:
        payload["history_id"] = resolved_history_id
    return payload


def _load_compiled_from_store(store: VaultStore, topic: str) -> CompiledArtifact | None:
    path = compiled_artifact_path(topic)
    if not store.exists(path):
        return None
    try:
        data = json.loads(store.read_text(path))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    artifact = CompiledArtifact.from_dict(data)
    return artifact if artifact.optimized_instructions.strip() else None


def _load_compiled_at(vcs: VaultVcs, topic: str, ref: str) -> CompiledArtifact | None:
    path = compiled_artifact_path(topic)
    raw = vcs.read_file_at(ref, path)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    artifact = CompiledArtifact.from_dict(data)
    return artifact if artifact.optimized_instructions.strip() else None


def _text_unified_diff(
    before: str,
    after: str,
    *,
    fromfile: str,
    tofile: str,
) -> str:
    """Build a unified diff string from two text bodies (line-oriented)."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    if not before_lines:
        before_lines = [""]
    if not after_lines:
        after_lines = [""]
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
    )
    return "\n".join(diff)


def _resolve_from_history(
    vcs: VaultVcs,
    compile_state: Any,
    *,
    branch: str | None,
    history_id: str | None,
) -> tuple[str, str, str] | None:
    """Resolve base/head SHAs from compile-state history or merge commits."""
    entry = find_compile_history(
        compile_state,
        branch=branch,
        history_id=history_id,
    )
    if entry is not None and entry.base_sha and entry.head_sha:
        return entry.base_sha, entry.head_sha, "history"

    branch_name = entry.branch if entry is not None else branch
    if not branch_name:
        return None

    merge_sha = entry.merge_sha if entry is not None else None
    if not merge_sha:
        merge_sha = vcs.find_merge_commit_for_branch(branch_name)
    if merge_sha:
        parents = vcs.merge_parents(merge_sha)
        if parents is not None:
            return parents[0], parents[1], "merge_commit"

    if entry is not None:
        if entry.base_sha and entry.head_sha:
            return entry.base_sha, entry.head_sha, "history"
        if entry.head_sha:
            try:
                default = vcs.default_branch()
                return vcs.ref_sha(default), entry.head_sha, "history"
            except GitError:
                return None

    return None


def resolve_query_path_at(
    vcs: VaultVcs,
    topic: str,
    head_ref: str,
    base_ref: str,
) -> str | None:
    """Pick the vault-relative ``query.md`` path to diff (override preferred when present)."""
    root = root_prompt_path("query")
    override = override_prompt_path("query", topic)
    for path in (override, root):
        if vcs.file_exists_at(head_ref, path) or vcs.file_exists_at(base_ref, path):
            return path
    return None


def _previous_ref_for_query(vcs: VaultVcs, topic: str) -> str:
    """Best-effort parent ref for HEAD when no candidate branch is supplied."""
    for ref in ("HEAD", vcs.default_branch()):
        path = resolve_query_path_at(vcs, topic, ref, vcs.default_branch())
        if path is None:
            continue
        shas = vcs.path_commit_shas(path, limit=2)
        if len(shas) >= 2:
            return shas[1]
        if len(shas) == 1:
            parent = vcs._run(  # noqa: SLF001 — rev-parse parent is read-only
                ["rev-parse", f"{shas[0]}^"],
                check=False,
                optional_locks=False,
            )
            if parent.returncode == 0 and parent.stdout.strip():
                return parent.stdout.strip()
    return "HEAD~1"


def _parse_unified_patch(patch: str) -> tuple[list[DiffHunk], bool]:
    if not patch.strip():
        return [], False
    hunks: list[DiffHunk] = []
    current_header: str | None = None
    current_lines: list[DiffLine] = []
    old_no = 0
    new_no = 0
    total_lines = 0
    truncated = False

    for raw in patch.splitlines():
        if raw.startswith("@@"):
            if current_header is not None:
                hunks.append(DiffHunk(header=current_header, lines=tuple(current_lines)))
                current_lines = []
            current_header = raw
            match = _HUNK_HEADER.match(raw)
            if match:
                old_no = int(match.group(1))
                new_no = int(match.group(3))
            continue
        if current_header is None:
            continue
        if total_lines >= _MAX_HUNK_LINES:
            truncated = True
            break
        if not raw:
            line_type: LineType = "context"
            text = ""
        elif raw[0] == "+":
            line_type = "add"
            text = raw[1:]
        elif raw[0] == "-":
            line_type = "del"
            text = raw[1:]
        elif raw[0] == " ":
            line_type = "context"
            text = raw[1:]
        elif raw.startswith("\\"):
            continue
        else:
            line_type = "context"
            text = raw

        old_line_no: int | None
        new_line_no: int | None
        if line_type == "add":
            old_line_no = None
            new_line_no = new_no
            new_no += 1
        elif line_type == "del":
            old_line_no = old_no
            new_line_no = None
            old_no += 1
        else:
            old_line_no = old_no
            new_line_no = new_no
            old_no += 1
            new_no += 1

        current_lines.append(
            DiffLine(type=line_type, text=text, old_no=old_line_no, new_no=new_line_no)
        )
        total_lines += 1

    if current_header is not None and current_lines:
        hunks.append(DiffHunk(header=current_header, lines=tuple(current_lines)))
    elif current_header is not None and not current_lines:
        hunks.append(DiffHunk(header=current_header, lines=()))

    if truncated and hunks:
        note = DiffLine(
            type="context",
            text="… diff truncated — open the branch in git for the full file",
            old_no=None,
            new_no=None,
        )
        last = hunks[-1]
        hunks[-1] = DiffHunk(header=last.header, lines=(*last.lines, note))

    return hunks, truncated
