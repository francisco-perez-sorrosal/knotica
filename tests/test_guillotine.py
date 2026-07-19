"""Unit and integration tests for Memory Guillotine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from knotica.core.page import validate_frontmatter, parse_page
from knotica.core.operations.guillotine import apply_guillotine, persist_guillotine_artifacts
from knotica.guillotine.paths import reports_dir
from knotica.guillotine.models import PassageRole, Verdict
from knotica.guillotine.report import artifact_paths_for, render_cli_summary
from knotica.guillotine.runner import ClaimNotFoundError, run_guillotine
from knotica.guillotine.search import (
    claim_slug,
    expand_search_terms,
    find_candidate_mentions,
    normalize_claim,
    resolve_search_scope,
)
from knotica.store import LocalFSStore
from support.vault import git_commit_count, git_head_sha, parse_knotica_commit, run_git

DEMO_CLAIM = "Open-source agents are inherently unsafe for serious users."
GUILLOTINE_REPORTS = reports_dir("agentic-systems")
REFUTE_PAGE = """---
type: concept
topic: agentic-systems
confidence: medium
sources: []
status: active
tags: []
created: 2026-07-07
updated: 2026-07-07
---

# Open Agent Ecosystem

The claim that open-source agents are inherently unsafe is too broad. Risk depends on sandboxing.
"""

ASSERT_PAGE = """---
type: concept
topic: agentic-systems
confidence: high
sources: []
status: active
tags: []
created: 2026-07-07
updated: 2026-07-07
---

# Agent Safety

Open-source agents are inherently unsafe for serious users.
"""

SOURCE_PAGE = """---
type: source
topic: agentic-systems
citation_key: vendor-report-2026
origin_url: https://example.com/vendor-report
source_type: html
created: 2026-07-07
updated: 2026-07-07
confidence: medium
sources: []
status: active
tags: []
---

# Vendor Report

"Open-source agents are inherently unsafe for serious users," the report claims.
"""

MENTION_PAGE = """---
type: concept
topic: agentic-systems
confidence: medium
sources: []
status: active
tags: []
created: 2026-07-07
updated: 2026-07-07
---

# User Context

Some teams debate whether open-source agents are inherently unsafe for serious users in regulated settings.
"""


@pytest.fixture
def guillotine_vault(template_vault: Path) -> Path:
    """Vault with assertion, refutation, source quote, and neutral mention."""
    topic = template_vault / "agentic-systems"
    topic.mkdir(exist_ok=True)
    (topic / "agent-safety.md").write_text(ASSERT_PAGE, encoding="utf-8")
    (topic / "open-agent-ecosystem.md").write_text(REFUTE_PAGE, encoding="utf-8")
    (topic / "user-owned-context.md").write_text(MENTION_PAGE, encoding="utf-8")
    sources = template_vault / "sources" / "agentic-systems"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "vendor-report-2026.md").write_text(SOURCE_PAGE, encoding="utf-8")
    run_git(template_vault, "add", "-A")
    run_git(template_vault, "commit", "-m", "test: guillotine fixture pages")
    return template_vault


def _persist_dry_run(store: LocalFSStore, vault: Path, result, diff: str) -> dict[str, object]:
    return persist_guillotine_artifacts(
        store, vault, result, diff, summary="guillotine dry-run report"
    )


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


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged["NO_COLOR"] = "1"
    if env:
        merged.update(env)
    return subprocess.run(_cli(*args), capture_output=True, text=True, env=merged, timeout=60)


def test_guillotine_finds_exact_claim(guillotine_vault: Path) -> None:
    dirs = resolve_search_scope(guillotine_vault, "agentic-systems")
    hits = find_candidate_mentions(DEMO_CLAIM, guillotine_vault, dirs)
    paths = {hit.path for hit in hits}
    assert "agentic-systems/agent-safety.md" in paths


def test_guillotine_finds_case_insensitive_claim(guillotine_vault: Path) -> None:
    dirs = resolve_search_scope(guillotine_vault, "agentic-systems")
    hits = find_candidate_mentions(DEMO_CLAIM.upper(), guillotine_vault, dirs)
    assert hits


def test_guillotine_excludes_reports_by_default(guillotine_vault: Path) -> None:
    reports = guillotine_vault / GUILLOTINE_REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "old.md").write_text(DEMO_CLAIM, encoding="utf-8")
    dirs = resolve_search_scope(guillotine_vault, "agentic-systems", include_reports=False)
    hits = find_candidate_mentions(DEMO_CLAIM, guillotine_vault, dirs, include_reports=False)
    assert all(not hit.path.startswith(f"{GUILLOTINE_REPORTS}/") for hit in hits)


def test_guillotine_classifies_assertion(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    roles = {passage.path: passage.role for passage in result.passages}
    assert roles["agentic-systems/agent-safety.md"] == PassageRole.ASSERTS


def test_guillotine_classifies_refutation(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    roles = {passage.path: passage.role for passage in result.passages}
    assert roles["agentic-systems/open-agent-ecosystem.md"] == PassageRole.REFUTES


def test_guillotine_classifies_quote(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    source_roles = [p.role for p in result.passages if p.path.startswith("sources/")]
    assert PassageRole.QUOTES in source_roles


def test_guillotine_preserves_refutation_in_patch(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    patched_paths = {patch.path for patch in result.patches}
    assert "agentic-systems/open-agent-ecosystem.md" not in patched_paths
    assert "sources/agentic-systems/vendor-report-2026.md" not in patched_paths
    assert "agentic-systems/agent-safety.md" in patched_paths
    assert "open-agent-ecosystem" not in diff


def test_guillotine_generates_report(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    envelope = _persist_dry_run(store, guillotine_vault, result, diff)
    assert "error" not in envelope
    assert envelope["report_path"].startswith(f"{GUILLOTINE_REPORTS}/")
    report_path = guillotine_vault / envelope["report_path"]
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "Memory Guillotine Report" in text
    assert DEMO_CLAIM in text


def test_guillotine_generates_diff(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    _persist_dry_run(store, guillotine_vault, result, diff)
    diff_path = guillotine_vault / artifact_paths_for(result).diff_path
    assert diff_path.exists()
    assert "agent-safety.md" in diff_path.read_text(encoding="utf-8")


def test_guillotine_dry_run_does_not_modify_pages(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    before = (guillotine_vault / "agentic-systems/agent-safety.md").read_text(encoding="utf-8")
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    _persist_dry_run(store, guillotine_vault, result, diff)
    after = (guillotine_vault / "agentic-systems/agent-safety.md").read_text(encoding="utf-8")
    assert before == after


def test_guillotine_apply_requires_explicit_flag(
    guillotine_vault: Path, vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KNOTICA_CONFIG", str(vault_config))
    completed = _run_cli(
        "guillotine",
        DEMO_CLAIM,
        "--topic",
        "agentic-systems",
        "--dry-run",
    )
    assert completed.returncode == 0
    assert "DRY RUN" in completed.stderr


def test_guillotine_does_not_modify_sources_by_default(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    source_path = guillotine_vault / "sources/agentic-systems/vendor-report-2026.md"
    before = source_path.read_text(encoding="utf-8")
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    _persist_dry_run(store, guillotine_vault, result, diff)
    assert source_path.read_text(encoding="utf-8") == before


def test_guillotine_preserves_frontmatter_on_apply(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    envelope = apply_guillotine(store, guillotine_vault, result, diff, summary="unsafe claim")
    assert "error" not in envelope
    updated = (guillotine_vault / "agentic-systems/agent-safety.md").read_text(encoding="utf-8")
    assert updated.startswith("---")
    assert "type: concept" in updated


def test_guillotine_json_output_valid(
    guillotine_vault: Path, vault_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KNOTICA_CONFIG", str(vault_config))
    completed = _run_cli(
        "guillotine",
        DEMO_CLAIM,
        "--topic",
        "agentic-systems",
        "--json",
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["claim"] == DEMO_CLAIM
    assert payload["topic"] == "agentic-systems"
    assert "risk_score" in payload


def test_guillotine_risk_score_universal_uncited_claim_high(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    assert result.risk_score >= 46


def test_guillotine_verdict_override(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="keep"
    )
    assert result.recommendation == Verdict.KEEP
    assert result.patches == []


def test_guillotine_topic_scope(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    with pytest.raises(ClaimNotFoundError):
        run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="nonexistent-topic")


def test_guillotine_claim_slug_stable() -> None:
    claim = "Open-source agents are inherently unsafe for serious users."
    assert claim_slug(claim) == claim_slug(claim)
    assert "open" in claim_slug(claim)


def test_guillotine_normalize_and_expand_terms() -> None:
    normalized = normalize_claim("  Hello, World!  ")
    assert normalized == "hello, world"
    terms = expand_search_terms("open-source agents unsafe")
    assert any("open source" in term or "open-source" in term for term in terms)


def test_guillotine_integration_roles_and_single_patch(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    assert any(p.role == PassageRole.ASSERTS for p in result.passages)
    assert any(p.role == PassageRole.REFUTES for p in result.passages)
    assert len(result.patches) >= 1
    assert all(patch.path == "agentic-systems/agent-safety.md" for patch in result.patches)
    assert diff


def test_guillotine_apply_creates_knotica_commit(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    envelope = apply_guillotine(
        store, guillotine_vault, result, diff, summary="unsafe agents claim"
    )
    assert "error" not in envelope
    # A knowledge-weakening verdict files a follow-up gap record in its own
    # commit after the guillotine commit, so HEAD is not necessarily the
    # guillotine op -- assert the guillotine commit exists in recent history
    # (behavior), not that it sits at HEAD (implementation ordering).
    subjects = run_git(guillotine_vault, "log", "-5", "--format=%s").strip().splitlines()
    ops = [parsed["op"] for s in subjects if (parsed := parse_knotica_commit(s)) is not None]
    assert "guillotine" in ops


def test_guillotine_cli_summary_mentions_risk(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    from knotica.guillotine.report import build_report

    report = build_report(result, artifact_paths_for(result), dry_run=True)
    summary = render_cli_summary(report)
    assert "CLAIM TRIAL" in summary
    assert "/100" in summary
    assert "Synthesis (wiki pages):" in summary
    assert "Raw sources (read-only):" in summary


def test_guillotine_report_shows_replacement_diff(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    from knotica.guillotine.report import render_report_markdown

    report_md = render_report_markdown(
        result,
        f"{GUILLOTINE_REPORTS}/test.diff",
        dry_run=True,
        commit_sha=None,
    )
    changes_start = report_md.index("## Proposed Changes")
    changes_section = report_md[changes_start:]
    assert "[!quote] Current text" in changes_section
    assert "[!example] Proposed replacement" in changes_section
    assert "disputed" in changes_section.lower()
    before_replacement = changes_section.split("[!example] Proposed replacement", maxsplit=1)[0]
    assert "~~" not in before_replacement
    assert DEMO_CLAIM in before_replacement


def test_guillotine_report_shows_strikethrough_removal() -> None:
    from knotica.guillotine.models import Patch
    from knotica.guillotine.report import _proposed_changes_section

    removal = Patch(
        path="agentic-systems/example.md",
        action="remove",
        line_start=10,
        line_end=10,
        before="Open-source agents are inherently unsafe for serious users.",
        after="",
        rationale="Unsupported synthesized assertion.",
    )
    section = _proposed_changes_section([removal])
    assert "[!note] Action: Remove claim" in section
    assert "~~Open-source agents are inherently unsafe for serious users.~~" in section
    assert "[!danger] Remove" in section
    assert "[!example] Proposed replacement" not in section


def test_guillotine_report_includes_datetime_and_plain_line_labels(guillotine_vault: Path) -> None:
    from datetime import UTC, datetime

    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    from knotica.guillotine.report import render_report_markdown

    fixed_time = datetime(2026, 7, 7, 19, 38, 0, tzinfo=UTC)
    report_md = render_report_markdown(
        result,
        f"{GUILLOTINE_REPORTS}/test.diff",
        dry_run=True,
        commit_sha=None,
        generated_at=fixed_time,
    )
    assert 'timestamp: "2026-07-07T19:38:00Z"' in report_md
    assert "run_status: dry-run" in report_md
    assert "**Generated:** 2026-07-07T19:38:00+00:00" in report_md
    assert "[[agentic-systems/agent-safety]]" in report_md
    assertion = next(p for p in result.passages if p.path == "agentic-systems/agent-safety.md")
    line_label = (
        str(assertion.line_start)
        if assertion.line_start == assertion.line_end
        else f"{assertion.line_start}–{assertion.line_end}"
    )
    for line in report_md.splitlines():
        if "[[agentic-systems/agent-safety]]" in line and "| `wiki` |" in line:
            assert line_label in line
            assert "#L" not in line
            assert line.count("|") == 8
            break
    else:
        raise AssertionError("expected agent-safety table row")


def test_guillotine_report_explains_risk_score_calculation(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    from knotica.guillotine.report import render_report_markdown

    report_md = render_report_markdown(
        result,
        f"{GUILLOTINE_REPORTS}/test.diff",
        dry_run=True,
        commit_sha=None,
    )
    assert "## Risk Score" in report_md
    assert "Why This Was Flagged" not in report_md
    assert "### Factors applied" in report_md
    assert "### Verdict thresholds" in report_md
    assert "**Factor sum:**" in report_md
    assert "← **matched**" in report_md
    assert "## Claim Inventory" in report_md
    assert "**Role**" in report_md
    assert "`ASSERTS`" in report_md
    assert "`REFUTES`" in report_md
    assert "Local risk" in report_md
    assert "Planned change" in report_md
    assert "Scoring signal" in report_md
    assert result.risk_score_raw != 0 or "**Final score:** 0/100" in report_md
    # Every wiki row whose passage produced a patch is marked as landing in the diff.
    if result.patches:
        assert "(in diff)" in report_md


def test_guillotine_synthesis_graph_excludes_sources(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, _ = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    from knotica.guillotine.report import render_report_markdown

    report_md = render_report_markdown(
        result,
        f"{GUILLOTINE_REPORTS}/test.diff",
        dry_run=True,
        commit_sha=None,
    )
    assert "## Synthesis Graph" in report_md
    assert "## Evidence Graph" not in report_md
    assert "### Raw source signals" not in report_md
    assert "## Claim Inventory" in report_md
    inventory_start = report_md.index("## Claim Inventory")
    inventory_end = report_md.index("## Synthesis Graph")
    inventory_section = report_md[inventory_start:inventory_end]
    assert "[[sources/" in inventory_section
    assert "raw source" in inventory_section
    assert "Scoring signal" in inventory_section
    graph_start = report_md.index("## Synthesis Graph")
    graph_end = report_md.index("## Proposed Changes")
    graph_section = report_md[graph_start:graph_end]
    tree_part = graph_section[graph_section.index("claim:") :]
    assert "[[sources/" not in tree_part
    assert "[[agentic-systems/" in tree_part


def test_guillotine_report_frontmatter_is_okf_compatible(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    envelope = _persist_dry_run(store, guillotine_vault, result, diff)
    report_path = envelope["report_path"]
    text = (guillotine_vault / report_path).read_text(encoding="utf-8")
    frontmatter, error, _body = parse_page(text)
    assert error is None
    assert frontmatter is not None
    assert frontmatter["type"] == "report"
    assert frontmatter["topic"] == "agentic-systems"
    assert frontmatter["status"] == "active"
    assert frontmatter["run_status"] == "dry-run"
    assert validate_frontmatter(frontmatter) == []
    assert "timestamp" in frontmatter
    assert "tags" in frontmatter
    assert "guillotine" in frontmatter["tags"]


def test_guillotine_dry_run_commits_artifacts_and_log(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    envelope = _persist_dry_run(store, guillotine_vault, result, diff)
    assert "error" not in envelope
    assert envelope.get("changed") is True
    subject = run_git(guillotine_vault, "log", "-1", "--format=%s").strip()
    parsed = parse_knotica_commit(subject)
    assert parsed is not None
    assert parsed["op"] == "guillotine"
    log_text = (guillotine_vault / "log.md").read_text(encoding="utf-8")
    assert (
        envelope["report_path"].removesuffix(".md") in log_text
        or envelope["report_path"] in log_text
    )
    newest_section = log_text.split("##", maxsplit=2)[1]
    assert ".diff" not in newest_section
    assert ".json" not in newest_section


def test_guillotine_upserts_reports_index_entry(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    envelope = _persist_dry_run(store, guillotine_vault, result, diff)
    assert "error" not in envelope
    index_text = (guillotine_vault / "index.md").read_text(encoding="utf-8")
    report_stem = envelope["report_path"].removesuffix(".md")
    assert f"[[{report_stem}]]" in index_text
    assert "Guillotine dry-run" in index_text


def test_guillotine_repeat_dry_run_dedupes_log_entry(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems")
    _persist_dry_run(store, guillotine_vault, result, diff)
    _persist_dry_run(store, guillotine_vault, result, diff)
    log_text = (guillotine_vault / "log.md").read_text(encoding="utf-8")
    report_stem = artifact_paths_for(result).report_path.removesuffix(".md")
    assert log_text.count(f"[[{report_stem}]]") == 1


# ---------------------------------------------------------------------------
# Applying a knowledge-weakening verdict files a retraction gap
#
# Import-only reuse of ``core.gap_classifier.gaps_path``/``core.records`` to
# read ``gaps.jsonl`` back -- these tests characterize the observable side
# effect of ``apply_guillotine``, never a private accessor.
# ---------------------------------------------------------------------------


def _filed_gaps(store: LocalFSStore) -> list:
    from knotica.core.gap_classifier import gaps_path
    from knotica.core.records import parse_gaps_jsonl

    path = gaps_path("agentic-systems")
    if not store.exists(path):
        return []
    return parse_gaps_jsonl(store.read_text(path))


def test_applying_a_retract_verdict_files_exactly_one_open_retracted_gap_naming_the_verdict(
    guillotine_vault: Path,
) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="retract"
    )

    apply_guillotine(store, guillotine_vault, result, diff, summary="retract unsafe claim")

    gaps = _filed_gaps(store)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.origin == "retracted"
    assert gap.status == "open"
    assert gap.fault_class == "genuine_gap"
    assert result.recommendation.value in (gap.reported_reason or ""), (
        "the filed gap's reported_reason must name the verdict that caused the retraction"
    )


def test_applying_a_dispute_verdict_files_exactly_one_open_retracted_gap(
    guillotine_vault: Path,
) -> None:
    # DISPUTE is the other knowledge-weakening verdict alongside RETRACT/DEMOTE
    # -- representative coverage of that equivalence class.
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="dispute"
    )

    apply_guillotine(store, guillotine_vault, result, diff, summary="dispute unsafe claim")

    gaps = _filed_gaps(store)
    assert len(gaps) == 1
    assert gaps[0].origin == "retracted"


def test_applying_a_qualify_verdict_files_no_gap(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="qualify"
    )

    envelope = apply_guillotine(store, guillotine_vault, result, diff, summary="qualify claim")

    assert "error" not in envelope
    assert _filed_gaps(store) == [], "QUALIFY is advisory, not knowledge-weakening -- no gap"


def test_applying_a_keep_verdict_files_no_gap(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="keep"
    )

    apply_guillotine(store, guillotine_vault, result, diff, summary="keep claim")

    assert _filed_gaps(store) == []


def test_the_filed_gap_commit_is_separate_from_the_guillotine_artifact_commit(
    guillotine_vault: Path,
) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="retract"
    )
    before_count = git_commit_count(guillotine_vault)
    before_sha = git_head_sha(guillotine_vault)

    apply_guillotine(store, guillotine_vault, result, diff, summary="retract unsafe claim")

    after_count = git_commit_count(guillotine_vault)
    assert after_count == before_count + 2, (
        "the guillotine artifact/patch commit and the gap-file commit must land as two "
        "distinct commits, never bundled together"
    )
    subjects = (
        run_git(guillotine_vault, "log", f"{before_sha}..HEAD", "--format=%s").strip().splitlines()
    )
    ops = [parse_knotica_commit(subject) for subject in subjects if parse_knotica_commit(subject)]
    assert {parsed["op"] for parsed in ops if parsed} & {"guillotine"}, (
        "one of the two new commits must still be the guillotine op"
    )


def test_repeat_application_of_the_same_retract_result_dedupes_the_filed_gap(
    guillotine_vault: Path,
) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="retract"
    )

    apply_guillotine(store, guillotine_vault, result, diff, summary="retract unsafe claim")
    apply_guillotine(store, guillotine_vault, result, diff, summary="retract unsafe claim")

    gaps = _filed_gaps(store)
    assert len(gaps) == 1, (
        "re-applying the identical retraction verdict must not spam the gap queue -- the "
        "same (topic, claim) must derive the same deterministic qa_id and dedup on it"
    )


def test_a_gap_write_failure_does_not_fail_the_guillotine_apply(
    guillotine_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gap-filing side effect is isolated from the primary guillotine
    operation -- an injected failure in the gap write must not prevent the
    artifact/patch commit from succeeding, and must not silently fabricate a
    gap record either."""
    from knotica.core.gap_classifier import gaps_path
    from knotica.core.transaction import VaultTransaction

    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="retract"
    )
    target_path = gaps_path("agentic-systems")
    original_write = VaultTransaction.write
    before = (guillotine_vault / "agentic-systems/agent-safety.md").read_text(encoding="utf-8")

    def _raising_write(self: VaultTransaction, path, content: str) -> None:  # type: ignore[no-untyped-def]
        if str(path) == target_path:
            raise RuntimeError("injected gap-write failure")
        return original_write(self, path, content)

    monkeypatch.setattr(VaultTransaction, "write", _raising_write)

    envelope = apply_guillotine(store, guillotine_vault, result, diff, summary="retract claim")

    assert "error" not in envelope, (
        "a failure isolated to the gap-filing side effect must not fail the primary "
        "guillotine apply"
    )
    assert envelope.get("commit_sha"), "the guillotine artifact/patch commit must still land"
    updated = (guillotine_vault / "agentic-systems/agent-safety.md").read_text(encoding="utf-8")
    assert updated != before, "the primary page patch must still be applied"
    assert not store.exists(target_path), (
        "the injected failure must leave gaps.jsonl genuinely absent -- never a silently "
        "fabricated success"
    )


def test_dry_run_persist_files_no_gap_even_for_a_retract_verdict(guillotine_vault: Path) -> None:
    store = LocalFSStore(guillotine_vault)
    result, diff = run_guillotine(
        store, guillotine_vault, DEMO_CLAIM, topic="agentic-systems", verdict="retract"
    )

    _persist_dry_run(store, guillotine_vault, result, diff)

    assert _filed_gaps(store) == [], "a dry-run trial must never file a gap -- nothing was applied"
