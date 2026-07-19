"""Passage localization and unified diff rendering for guillotine reports.

The guillotine is verdict + report + triage-scoring + gap-filing only. It never
synthesizes replacement prose: a weakening verdict localizes the contested
synthesized passage and marks it for mechanical removal, rendered as
strikethrough evidence in the report and as a deletion in the ``.diff``. The
diff is evidence display for human review — it is never applied to wiki pages.
Re-grounding of a weakened claim flows through the retracted-gap → discovery →
approved-ingest path, where the client authors grounded text.
"""

from __future__ import annotations

import difflib

from knotica.guillotine.models import Passage, PassageRole, Patch, Verdict


def propose_patches(
    claim: str,
    passages: list[Passage],
    verdict: Verdict,
    file_contents: dict[str, str],
) -> list[Patch]:
    """Localize contested synthesized passages and mark them for mechanical removal.

    No replacement wording is generated. Each returned patch marks a contested
    assertion span for removal (``action="remove"``, empty ``after``); the report
    renders it as strikethrough evidence for human review. Re-grounding is handled
    downstream by the retracted-gap queue, not by this tool.
    """
    if verdict == Verdict.KEEP:
        return []
    patches: list[Patch] = []
    seen_paths: set[str] = set()
    for passage in passages:
        if passage.is_source:
            continue
        if passage.role not in {PassageRole.ASSERTS, PassageRole.DEPENDS_ON}:
            continue
        if passage.suggested_action == "keep":
            continue
        if passage.path in seen_paths:
            continue
        line_start, line_end, before = _narrow_patch_range(
            file_contents[passage.path], passage.line_start, passage.line_end, claim
        )
        if not before.strip():
            continue
        patches.append(
            Patch(
                path=passage.path,
                action="remove",
                line_start=line_start,
                line_end=line_end,
                before=before,
                after="",
                rationale=_patch_rationale(passage.role, verdict),
            )
        )
        seen_paths.add(passage.path)
    return patches


def _narrow_patch_range(
    content: str, window_start: int, window_end: int, claim: str
) -> tuple[int, int, str]:
    """Shrink a context window to the lines that actually carry the claim."""
    from knotica.guillotine.search import expand_search_terms, normalize_claim

    lines = content.splitlines()
    terms = expand_search_terms(claim)
    normalized_claim = normalize_claim(claim)
    matching: list[int] = []
    for line_no in range(window_start, min(window_end, len(lines)) + 1):
        line_text = lines[line_no - 1]
        normalized_line = normalize_claim(line_text)
        if normalized_claim in normalized_line:
            matching.append(line_no)
            continue
        if any(term.lower() in line_text.lower() for term in terms if len(term) > 12):
            matching.append(line_no)
    if not matching:
        return window_start, window_end, _extract_target_lines(content, window_start, window_end)
    start = matching[0]
    end = matching[-1]
    # Extend only while the expanded block still substantially overlaps the claim.
    while end < len(lines):
        block = "\n".join(lines[start - 1 : end + 1])
        if _claim_overlap(claim, block) >= 0.95:
            end += 1
            continue
        if lines[end - 1].rstrip().endswith((",", ";")) and _claim_overlap(claim, block) >= 0.6:
            end += 1
            continue
        break
    return start, end, _extract_target_lines(content, start, end)


def _claim_overlap(claim: str, text: str) -> float:
    from knotica.guillotine.search import normalize_claim

    claim_tokens = {token for token in normalize_claim(claim).split() if len(token) > 2}
    text_tokens = {token for token in normalize_claim(text).split() if len(token) > 2}
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & text_tokens) / len(claim_tokens)


def render_diff(patches: list[Patch], file_contents: dict[str, str]) -> str:
    """Render a unified diff for all proposed patches."""
    chunks: list[str] = []
    for patch in patches:
        original_lines = file_contents[patch.path].splitlines(keepends=True)
        updated_lines = _apply_patch_to_lines(original_lines, patch)
        diff = difflib.unified_diff(
            original_lines,
            updated_lines,
            fromfile=f"a/{patch.path}",
            tofile=f"b/{patch.path}",
            lineterm="",
        )
        chunk = "\n".join(diff)
        if chunk:
            chunks.append(chunk)
    return "\n".join(chunks).rstrip() + ("\n" if chunks else "")


def _extract_target_lines(content: str, line_start: int, line_end: int) -> str:
    lines = content.splitlines()
    return "\n".join(lines[line_start - 1 : line_end])


def _patch_rationale(role: PassageRole, verdict: Verdict) -> str:
    return (
        f"Mark {role.value} passage for removal per verdict {verdict.value}; "
        "re-grounding flows through the retracted-gap queue, not in-tool rewriting."
    )


def _apply_patch_to_lines(lines: list[str], patch: Patch) -> list[str]:
    body_lines = [line.rstrip("\n") for line in lines]
    if not body_lines and patch.before:
        body_lines = [""]
    start = patch.line_start - 1
    end = patch.line_end
    replacement = patch.after.splitlines()
    new_body = body_lines[:start] + replacement + body_lines[end:]
    # Preserve trailing newline behavior of the original file.
    result = "\n".join(new_body)
    if lines and lines[-1].endswith("\n"):
        result += "\n"
    return [result] if not result else _lines_with_endings(result, lines)


def _lines_with_endings(text: str, original_lines: list[str]) -> list[str]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [""]
    if original_lines and not original_lines[-1].endswith("\n") and lines[-1].endswith("\n"):
        lines[-1] = lines[-1].rstrip("\n")
    return lines
