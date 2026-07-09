"""Tests for OKF log formatting and write-path invariants."""

from __future__ import annotations

from knotica.okf.log_fmt import format_operation_log_entry, prepend_operation_log


def test_format_operation_log_entry_wikilinks_only_markdown_pages() -> None:
    entry = format_operation_log_entry(
        entry_date="2026-07-09",
        op="guillotine",
        topic="agentic-systems",
        title="trial report",
        pages=(
            "agentic-systems/reports/guillotine/report.md",
            "agentic-systems/reports/guillotine/report.diff",
            "agentic-systems/reports/guillotine/report.json",
        ),
    )
    assert "[[agentic-systems/reports/guillotine/report]]" in entry.body
    assert ".diff" not in entry.body
    assert ".json" not in entry.body


def test_prepend_operation_log_replaces_entry_for_same_report_page() -> None:
    existing = """# Directory Update Log

## 2026-07-09
* **Repair**: guillotine · agentic-systems — first run ([[agentic-systems/reports/guillotine/report]])

## 2026-07-08
* **Update**: agentic-systems — older entry ([[agentic-systems/react]])
"""
    updated = prepend_operation_log(
        existing,
        entry_date="2026-07-09",
        op="guillotine",
        topic="agentic-systems",
        title="second run",
        pages=("agentic-systems/reports/guillotine/report.md",),
    )
    assert updated.count("[[agentic-systems/reports/guillotine/report]]") == 1
    assert "second run" in updated
    assert "first run" not in updated
    assert "[[agentic-systems/react]]" in updated
