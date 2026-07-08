"""Markdown/JSON report generation and artifact persistence."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from knotica.guillotine.models import (
    ArtifactPaths,
    EvidenceGraph,
    GuillotineReport,
    GuillotineResult,
    Passage,
    PassageRole,
    Patch,
    Verdict,
    VERDICT_THRESHOLDS,
)
from knotica.guillotine.score import verdict_threshold_for
from knotica.guillotine.search import claim_slug
from knotica.store import VaultStore

_REPORTS_DIR = "reports/guillotine"

# Obsidian callout kinds used in human-readable reports.
_CALLOUT_VERDICT: dict[Verdict, str] = {
    Verdict.KEEP: "success",
    Verdict.QUALIFY: "tip",
    Verdict.DEMOTE: "warning",
    Verdict.DISPUTE: "warning",
    Verdict.RETRACT: "danger",
    Verdict.QUARANTINE_SOURCE: "danger",
    Verdict.DELETE_UNSUPPORTED_SYNTHESIS: "danger",
}


def write_artifacts(
    store: VaultStore,
    result: GuillotineResult,
    diff_text: str,
    *,
    dry_run: bool,
    commit_sha: str | None = None,
) -> tuple[ArtifactPaths, GuillotineReport]:
    today = date.today().isoformat()
    slug = claim_slug(result.claim)
    base_name = f"{today}-{slug}"
    report_path = f"{_REPORTS_DIR}/{base_name}.md"
    diff_path = f"{_REPORTS_DIR}/{base_name}.diff"
    json_path = f"{_REPORTS_DIR}/{base_name}.json"

    report_md = render_report_markdown(result, diff_path, dry_run=dry_run, commit_sha=commit_sha)
    json_payload = render_report_json(result, report_path, diff_path, json_path, dry_run=dry_run)

    store.write_text_atomic(report_path, report_md)
    store.write_text_atomic(diff_path, diff_text)
    store.write_text_atomic(
        json_path, json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n"
    )

    artifacts = ArtifactPaths(report_path=report_path, diff_path=diff_path, json_path=json_path)
    final = GuillotineReport(
        claim=result.claim,
        normalized_claim=result.normalized_claim,
        topic=result.topic,
        recommendation=result.recommendation,
        risk_score=result.risk_score,
        summary=result.summary,
        passages=tuple(result.passages),
        evidence=result.evidence,
        patches=tuple(result.patches),
        artifacts=artifacts,
        applied=not dry_run,
        commit_sha=commit_sha,
        status="dry-run" if dry_run else "applied",
    )
    return artifacts, final


def build_report(
    result: GuillotineResult,
    artifacts: ArtifactPaths,
    *,
    dry_run: bool,
    commit_sha: str | None = None,
) -> GuillotineReport:
    """Assemble a :class:`GuillotineReport` from a result and artifact paths."""
    return GuillotineReport(
        claim=result.claim,
        normalized_claim=result.normalized_claim,
        topic=result.topic,
        recommendation=result.recommendation,
        risk_score=result.risk_score,
        summary=result.summary,
        passages=tuple(result.passages),
        evidence=result.evidence,
        patches=tuple(result.patches),
        artifacts=artifacts,
        applied=not dry_run,
        commit_sha=commit_sha,
        status="dry-run" if dry_run else "applied",
    )


def artifact_paths_for(result: GuillotineResult) -> ArtifactPaths:
    """Compute vault-relative artifact paths without writing them."""
    today = date.today().isoformat()
    slug = claim_slug(result.claim)
    base_name = f"{today}-{slug}"
    return ArtifactPaths(
        report_path=f"{_REPORTS_DIR}/{base_name}.md",
        diff_path=f"{_REPORTS_DIR}/{base_name}.diff",
        json_path=f"{_REPORTS_DIR}/{base_name}.json",
    )


def render_report_markdown(
    result: GuillotineResult,
    diff_path: str,
    *,
    dry_run: bool,
    commit_sha: str | None,
    generated_at: datetime | None = None,
) -> str:
    """Render the human-readable guillotine trial report."""
    timestamp = generated_at or datetime.now(UTC)
    generated_date = timestamp.date().isoformat()
    generated_datetime = timestamp.isoformat(timespec="seconds")
    status = "dry-run" if dry_run else "applied"
    verdict_label = _verdict_label(result.recommendation)
    pages = sorted({passage.path for passage in result.passages})

    lines = [
        "---",
        "type: guillotine-report",
        f'claim: "{_yaml_escape(result.claim)}"',
        f"topic: {result.topic}",
        f"date: {generated_date}",
        f"datetime: {generated_datetime}",
        f"verdict: {result.recommendation.value.lower()}",
        f"risk_score: {result.risk_score}",
        f"status: {status}",
        "---",
        "",
        "# Memory Guillotine Report",
        "",
        _callout(
            "abstract",
            "Report metadata",
            "\n".join(
                [
                    f"**Generated:** {generated_datetime}",
                    f"**Topic:** `{result.topic}`",
                    f"**Status:** {status}",
                ]
            ),
        ),
        "",
        _at_a_glance_callout(result, verdict_label, dry_run),
        "",
        "---",
        "",
        "## Claim",
        "",
        _callout("question", "Claim on trial", result.claim),
        "",
        "---",
        "",
        "## Verdict",
        "",
        _callout(
            _verdict_callout_kind(result.recommendation),
            f"Recommended: {verdict_label}",
            result.summary,
        ),
        "",
        "---",
        "",
        "## Risk Score",
        "",
        _risk_score_section(result),
        "",
        "---",
        "",
        "## Claim Inventory",
        "",
        _callout("note", "How to read this table", _claim_inventory_intro()),
        "",
        _claim_inventory_table(result),
        "",
        "---",
        "",
        "## Synthesis Graph",
        "",
        _synthesis_graph_section(result),
        "",
        "---",
        "",
        "## Proposed Changes",
        "",
        _proposed_changes_section(result.patches),
        "",
        _callout("info", "Unified diff", f"Full patch file: `{diff_path}`"),
        "",
        "---",
        "",
        "## Rollback",
        "",
    ]
    if dry_run:
        lines.append(_callout("success", "Dry run", "Not applied; no rollback needed."))
    elif commit_sha:
        lines.append(
            _callout(
                "warning",
                "Revert command",
                f"If applied as a commit, revert with:\n\n`git revert {commit_sha}`",
            )
        )
    else:
        lines.append(_callout("warning", "No commit SHA", "Applied without a recorded commit SHA."))

    lines.extend(
        [
            "",
            "---",
            "",
            "## Receipt",
            "",
            _receipt_callout(result, pages, dry_run, commit_sha, timestamp),
        ]
    )
    return "\n".join(lines) + "\n"


def render_report_json(
    result: GuillotineResult,
    report_path: str,
    diff_path: str,
    json_path: str,
    *,
    dry_run: bool,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build the JSON sidecar payload."""
    timestamp = generated_at or datetime.now(UTC)
    return {
        "claim": result.claim,
        "normalized_claim": result.normalized_claim,
        "topic": result.topic,
        "recommendation": result.recommendation.value,
        "risk_score": result.risk_score,
        "risk_score_raw": result.risk_score_raw,
        "risk_score_clamped": result.risk_score_raw != result.risk_score,
        "verdict_threshold": verdict_threshold_for(result.risk_score).label,
        "score_factors": [
            {"delta": factor.delta, "description": factor.description}
            for factor in result.score_factors
        ],
        "summary": result.summary,
        "status": "dry-run" if dry_run else "applied",
        "date": timestamp.date().isoformat(),
        "generated_at": timestamp.isoformat(timespec="seconds"),
        "passages": [
            {
                "path": passage.path,
                "line_start": passage.line_start,
                "line_end": passage.line_end,
                "kind": _passage_kind(passage),
                "page_link": _page_wikilink(passage.path),
                "line_range": _line_label(passage.line_start, passage.line_end),
                "text": passage.text,
                "role": passage.role.value,
                "strength": passage.strength,
                "modality": passage.modality,
                "risk": passage.risk if not passage.is_source else None,
                "reason": passage.reason,
                "suggested_action": passage.suggested_action if not passage.is_source else None,
                "planned_change": (
                    None if passage.is_source else _planned_change_label(passage, result.patches)
                ),
                "in_diff": (
                    False
                    if passage.is_source
                    else _matching_patch(passage, result.patches) is not None
                ),
                "scoring_signal": (
                    _source_scoring_signal(passage, result.evidence) if passage.is_source else None
                ),
                "is_source": passage.is_source,
            }
            for passage in result.passages
        ],
        "evidence": {
            "supporting_sources": list(result.evidence.supporting_sources),
            "contradicting_sources": list(result.evidence.contradicting_sources),
            "qualified_sources": list(result.evidence.qualified_sources),
            "interested_sources": list(result.evidence.interested_sources),
            "uncited_mentions": list(result.evidence.uncited_mentions),
        },
        "raw_source_signals": [
            {
                "path": passage.path,
                "line_start": passage.line_start,
                "line_end": passage.line_end,
                "role": passage.role.value,
                "scoring_signal": _source_scoring_signal(passage, result.evidence),
            }
            for passage in result.passages
            if passage.is_source
        ],  # deprecated: prefer passages[].scoring_signal when is_source
        "patches": [
            {
                "path": patch.path,
                "action": patch.action,
                "line_start": patch.line_start,
                "line_end": patch.line_end,
                "before": patch.before,
                "after": patch.after,
                "rationale": patch.rationale,
            }
            for patch in result.patches
        ],
        "artifacts": {
            "report_path": report_path,
            "diff_path": diff_path,
            "json_path": json_path,
        },
        "score_breakdown": result.score_breakdown,
    }


def render_cli_summary(report: GuillotineReport) -> str:
    """Short terminal summary for the guillotine command."""
    role_counts = _role_counts(report.passages)
    pages = {passage.path for passage in report.passages}
    wiki_passages = [passage for passage in report.passages if not passage.is_source]
    source_passages = [passage for passage in report.passages if passage.is_source]
    synth_asserts = [p for p in wiki_passages if p.role == PassageRole.ASSERTS]
    synth_counter = [
        p
        for p in wiki_passages
        if p.role in {PassageRole.REFUTES, PassageRole.CONTRADICTS, PassageRole.QUALIFIES}
    ]
    supporting_quotes = [
        passage for passage in source_passages if passage.path in report.evidence.supporting_sources
    ]
    locations = len(report.passages)
    lines = [
        "CLAIM TRIAL",
        "",
        "Claim:",
        f"  {report.claim}",
        "",
        "Topic:",
        f"  {report.topic}",
        "",
        "Found:",
        f"  {locations} affected location(s) across {len(pages)} file(s).",
        "",
        "Claim role analysis:",
    ]
    for role, count in sorted(role_counts.items()):
        if count:
            lines.append(f"  - {count} {role.lower().replace('_', ' ')}")
    lines.extend(
        [
            "",
            "Synthesis (wiki pages):",
            f"  - {len(synth_asserts)} assertion(s)",
            f"  - {len(synth_counter)} refutation(s) or qualification(s)",
            "",
            "Raw sources (read-only):",
            f"  - {len(source_passages)} passage(s) in sources/",
            f"  - {len(supporting_quotes)} supporting quote(s)",
            "",
            "Risk:",
            f"  {report.risk_score}/100",
            "",
            "Recommended verdict:",
            f"  {_verdict_label(report.recommendation)}",
            "",
            "Generated:",
            f"  {report.artifacts.report_path}",
            f"  {report.artifacts.diff_path}",
        ]
    )
    if report.status == "dry-run":
        lines.extend(["", "No files were modified."])
    else:
        lines.extend(["", f"Applied commit: {report.commit_sha or 'n/a'}"])
    return "\n".join(lines)


def _proposed_changes_section(patches: list[Patch]) -> str:
    """Render human-readable before/after edits (strikethrough only for removals)."""
    if not patches:
        return "_No synthesized page edits proposed — verdict is KEEP or no actionable assertions._"

    blocks: list[str] = []
    for index, patch in enumerate(patches, start=1):
        action_label = _patch_action_label(patch)
        line_label = _line_label(patch.line_start, patch.line_end)
        blocks.append(f"### Change {index}: {_page_wikilink(patch.path)} · lines {line_label}")
        blocks.append("")
        blocks.append(_callout("note", f"Action: {action_label}", patch.rationale))
        blocks.append("")
        blocks.append(_render_patch_diff_block(patch))
        if index < len(patches):
            blocks.append("")
            blocks.append("---")
            blocks.append("")
    return "\n".join(blocks)


def _patch_action_label(patch: Patch) -> str:
    if patch.action == "remove" or not patch.after.strip():
        return "Remove claim"
    if patch.action == "replace":
        return "Replace claim"
    if patch.action == "annotate":
        return "Annotate claim"
    if patch.action == "insert":
        return "Insert qualification"
    return patch.action.replace("_", " ").title()


def _render_patch_diff_block(patch: Patch) -> str:
    """Render before and optional after for one patch."""
    is_removal = patch.action == "remove" or not patch.after.strip()
    if is_removal:
        return _callout("danger", "Remove", _strikethrough_body(patch.before))

    current = _callout("quote", "Current text", patch.before.strip())
    replacement = _callout("example", "Proposed replacement", patch.after.strip())
    return f"{current}\n\n{replacement}"


def _strikethrough_body(text: str) -> str:
    """Strike through each non-empty line for removal previews."""
    struck: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            struck.append("")
            continue
        struck.append(f"~~{stripped}~~")
    return "\n".join(struck)


def _risk_score_section(result: GuillotineResult) -> str:
    """Explain the single overall triage score, its factors, and the verdict band."""
    threshold = verdict_threshold_for(result.risk_score)
    headline = "\n".join(
        [
            f"**Final score:** {result.risk_score}/100",
            f"**Recommended verdict:** {_verdict_label(result.recommendation)}",
            f"**Score band matched:** {threshold.label} → typically {_verdict_label(threshold.verdict)}",
        ]
    )
    lines = [
        _callout(
            "tip",
            "Triage score (0–100)",
            "Transparent score for user review — not objective truth. Sum the factors below, "
            "clamp to 0–100, then map to a verdict band.",
        ),
        "",
        _callout("tip", "Result", headline),
        "",
    ]
    if result.recommendation != threshold.verdict:
        lines.append(
            _callout(
                "warning",
                "Verdict override",
                f"Band default would be {_verdict_label(threshold.verdict)}, but the "
                f"recommendation is **{_verdict_label(result.recommendation)}** based on "
                f"passage roles and evidence shape.",
            )
        )
        lines.append("")
    if result.risk_score_raw != result.risk_score:
        lines.append(
            _callout(
                "note",
                "Clamped score",
                f"Raw factor sum: {result.risk_score_raw:+d} → clamped to {result.risk_score}/100.",
            )
        )
        lines.append("")

    lines.extend(["### Factors applied", ""])
    if result.score_factors:
        lines.append("| Adjustment | Reason |")
        lines.append("|---:|---|")
        running = 0
        for factor in result.score_factors:
            running += factor.delta
            lines.append(f"| {factor.delta:+d} | {factor.description} |")
        lines.append("")
        lines.append(f"**Factor sum:** {running:+d}")
        if running != result.risk_score_raw:
            lines.append(f"**After clamp:** {result.risk_score}/100")
    else:
        lines.append("_No risk factors triggered — score remains 0._")

    lines.extend(["", "### Verdict thresholds", ""])
    lines.append("| Score range | Typical verdict | This claim |")
    lines.append("|---|---|---|")
    for index, band in enumerate(VERDICT_THRESHOLDS):
        lower = 0 if index == 0 else VERDICT_THRESHOLDS[index - 1].max_score + 1
        in_band = lower <= result.risk_score <= band.max_score
        marker = "← **matched**" if in_band else ""
        lines.append(f"| {band.label} | {_verdict_label(band.verdict)} | {marker} |")
    return "\n".join(lines)


def _claim_inventory_intro() -> str:
    return "\n".join(
        [
            "Every passage found in scope — **wiki synthesis** and **raw sources** in one table.",
            "",
            "- **Role** — How this passage relates to the claim on trial:",
            "  - `ASSERTS` — States the claim as fact (or close paraphrase) without enough caveat.",
            "  - `QUALIFIES` — Hedges, scopes, or attributes the claim (e.g. “some sources argue…”).",
            "  - `REFUTES` — Explicitly argues the claim is wrong, unsupported, or too broad.",
            "  - `CONTRADICTS` — Asserts something incompatible with the claim.",
            "  - `QUOTES` — Quotes the claim without endorsing it (common in `sources/`).",
            "  - `MENTIONS` — Names the claim without clearly asserting or refuting it.",
            "  - `DEPENDS_ON` — Treats the claim as a premise for further reasoning.",
            "  - `IRRELEVANT` — Keyword overlap only; not clearly about this claim.",
            "- **Local risk** — How strongly this *single mention* reads as problematic "
            "(none / low / medium / high), based on its role and wording. Wiki rows only; "
            "not the overall 0–100 triage score above.",
            "- **Planned change** — The edit actually applied to this wiki passage under the "
            "current verdict. Rows marked **(in diff)** appear in **Proposed Changes** and the "
            "`.diff`; `no change` means the passage was inspected but left as-is (at most one "
            "edit is made per page, and only assertions are rewritten). `—` for raw sources, "
            "which are never edited.",
            "- **Scoring signal** — How a raw `sources/` passage fed the overall risk "
            "calculation (e.g. supporting quote, vendor-interest signal). Empty (—) for "
            "wiki rows.",
        ]
    )


def _claim_inventory_table(result: GuillotineResult) -> str:
    passages = sorted(
        result.passages,
        key=lambda passage: (passage.is_source, passage.path, passage.line_start),
    )
    if not passages:
        return "_No passages found._"
    rows = [
        "| Kind | File | Lines | Role | Local risk | Planned change | Scoring signal |",
        "|---|---|---|---:|---|---|---|",
    ]
    for passage in passages:
        rows.append(
            f"| `{_passage_kind(passage)}` | {_page_wikilink(passage.path)} | "
            f"{_line_label(passage.line_start, passage.line_end)} | `{passage.role.value}` | "
            f"{_inventory_risk_cell(passage)} | "
            f"{_planned_change_cell(passage, result.patches)} | "
            f"{_inventory_cell(_source_scoring_signal(passage, result.evidence) if passage.is_source else None)} |"
        )
    return "\n".join(rows)


def _passage_kind(passage: Passage) -> str:
    return "raw source" if passage.is_source else "wiki"


def _inventory_cell(value: str | None) -> str:
    if value is None or value == "":
        return "—"
    return value


def _inventory_risk_cell(passage: Passage) -> str:
    if passage.is_source:
        return "—"
    if passage.risk == "high":
        return "**high**"
    if passage.risk == "medium":
        return "**medium**"
    return passage.risk


def _planned_change_cell(passage: Passage, patches: list[Patch]) -> str:
    """Markdown cell for the planned edit, tied to the diff (not the role-based hint)."""
    if passage.is_source:
        return "—"
    if _matching_patch(passage, patches) is not None:
        return f"**{_planned_change_label(passage, patches)}**"
    return _planned_change_label(passage, patches)


def _planned_change_label(passage: Passage, patches: list[Patch]) -> str:
    """Plain-text planned change for a wiki passage (shared by markdown and JSON)."""
    patch = _matching_patch(passage, patches)
    if patch is not None:
        return f"{_patch_short_verb(patch)} (in diff)"
    if passage.suggested_action == "keep":
        return "keep as-is"
    return "no change"


def _matching_patch(passage: Passage, patches: list[Patch]) -> Patch | None:
    """Find the patch (if any) whose line range overlaps this passage's window."""
    for patch in patches:
        if patch.path != passage.path:
            continue
        if patch.line_start <= passage.line_end and patch.line_end >= passage.line_start:
            return patch
    return None


def _patch_short_verb(patch: Patch) -> str:
    """Short verb for the inventory (aligns with :func:`_patch_action_label`)."""
    if patch.action == "remove" or not patch.after.strip():
        return "remove"
    return patch.action


def _page_wikilink(vault_path: str) -> str:
    """Obsidian wikilink to a vault-relative markdown page.

    Uses the unaliased form ``[[topic/page]]`` so table cells never contain ``|``
    (aliased wikilinks would split markdown table columns).
    """
    stem = vault_path.removesuffix(".md")
    return f"[[{stem}]]"


def _line_label(line_start: int, line_end: int) -> str:
    """Plain line range label for tables (no links — Obsidian has no stable line anchors)."""
    if line_start == line_end:
        return str(line_start)
    return f"{line_start}–{line_end}"


def _synthesis_graph_section(result: GuillotineResult) -> str:
    """Wiki-only synthesis map (detail lives in Claim Inventory)."""
    return "\n".join(
        [
            _callout(
                "note",
                "Wiki synthesis only",
                f"How the claim propagates through editable pages in `{result.topic}/`. "
                "Raw `sources/` rows live in **Claim Inventory** above.",
            ),
            "",
            _fenced_text(_synthesis_graph_text(result)),
        ]
    )


def _synthesis_graph_text(result: GuillotineResult) -> str:
    """Render the synthesis map from wiki pages only (not raw sources/)."""
    lines = [f"claim: {result.normalized_claim}"]
    page_passages = [passage for passage in result.passages if not passage.is_source]
    if not page_passages:
        lines.append("  (no synthesized page mentions in scope)")
        return "\n".join(lines)

    for passage in page_passages:
        branch = f"  ├── {passage.role.value.lower()} in: {_page_wikilink(passage.path)}"
        lines.append(branch)

    return "\n".join(lines)


def _source_scoring_signal(passage: Passage, evidence: EvidenceGraph) -> str:
    """Human-readable note on how a raw source passage influenced risk factors."""
    signals: list[str] = []
    if passage.path in evidence.supporting_sources:
        signals.append("supporting quote (feeds source-count risk factors)")
    if passage.path in evidence.interested_sources:
        signals.append("vendor-interest signal (+20)")
    if not signals:
        return "scanned only — no scoring signal"
    return "; ".join(signals)


def _receipt_callout(
    result: GuillotineResult,
    pages: list[str],
    dry_run: bool,
    commit_sha: str | None,
    generated_at: datetime,
) -> str:
    touched = sorted({patch.path for patch in result.patches})
    touched_lines = [f"- `{path}`" for path in touched] if touched else ["- _(none)_"]
    inspected_lines = [f"- `{path}`" for path in pages]
    if dry_run:
        rollback = "- Not applied; no rollback needed."
    elif commit_sha:
        rollback = f"- `git revert {commit_sha}`"
    else:
        rollback = "- No commit SHA recorded."
    body = "\n".join(
        [
            f"**Claim:** {result.claim}",
            f"**Verdict:** `{result.recommendation.value}`",
            f"**Applied:** `{str(not dry_run).lower()}`",
            f"**Datetime:** {generated_at.isoformat(timespec='seconds')}",
            f"**Topic:** `{result.topic}`",
            "",
            "**Files touched**",
            *touched_lines,
            "",
            "**Files inspected**",
            *inspected_lines,
            "",
            "**Rollback**",
            rollback,
        ]
    )
    return _callout("abstract", "Audit receipt", body)


def _role_counts(passages: list[Passage]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for passage in passages:
        counts[passage.role.value] = counts.get(passage.role.value, 0) + 1
    return counts


def _verdict_label(verdict: Verdict) -> str:
    labels = {
        Verdict.KEEP: "KEEP",
        Verdict.QUALIFY: "QUALIFY",
        Verdict.DEMOTE: "DEMOTE",
        Verdict.DISPUTE: "DISPUTE + DEMOTE",
        Verdict.RETRACT: "RETRACT FROM SYNTHESIZED PAGES + MARK AS DISPUTED",
        Verdict.QUARANTINE_SOURCE: "QUARANTINE SOURCE + REVIEW SYNTHESIS",
        Verdict.DELETE_UNSUPPORTED_SYNTHESIS: "QUARANTINE SOURCE + DELETE UNSUPPORTED SYNTHESIS",
    }
    return labels.get(verdict, verdict.value)


def _yaml_escape(text: str) -> str:
    return text.replace('"', '\\"')


def _callout(kind: str, title: str, body: str) -> str:
    """Obsidian-style callout block (renders in Obsidian and many MD viewers)."""
    lines = [f"> [!{kind}] {title}", ">"]
    for line in body.splitlines():
        lines.append(f"> {line}" if line else ">")
    return "\n".join(lines)


def _fenced_text(text: str) -> str:
    return f"```text\n{text}\n```"


def _at_a_glance_callout(result: GuillotineResult, verdict_label: str, dry_run: bool) -> str:
    wiki_count = sum(1 for passage in result.passages if not passage.is_source)
    source_count = sum(1 for passage in result.passages if passage.is_source)
    status = "dry-run" if dry_run else "applied"
    body = "\n".join(
        [
            f"**Risk score:** {result.risk_score}/100",
            f"**Verdict:** {verdict_label}",
            f"**Passages:** {len(result.passages)} total "
            f"({wiki_count} wiki · {source_count} raw source)",
            f"**Patches proposed:** {len(result.patches)}",
            f"**Run mode:** {status}",
        ]
    )
    return _callout("info", "At a glance", body)


def _verdict_callout_kind(verdict: Verdict) -> str:
    return _CALLOUT_VERDICT.get(verdict, "note")
