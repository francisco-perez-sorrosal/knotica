"""Tests for OKF frontmatter normalization."""

from knotica.okf.frontmatter import normalize_concept_frontmatter, normalize_type_value
from knotica.okf.datetime_fmt import normalize_timestamp as rfc_normalize


def test_normalize_type_strips_knotica_kind():
    fields = {"type": "Concept", "knotica_kind": "concept"}
    normalized, warnings = normalize_type_value("agentic-systems/agent-memory.md", fields)
    assert normalized == "concept"
    assert "knotica_kind" not in fields
    assert warnings


def test_normalize_type_undo_title_case():
    fields = {"type": "Paper"}
    normalized, _warnings = normalize_type_value("agentic-systems/react.md", fields)
    assert normalized == "paper"


def test_normalize_adds_okf_fields():
    raw = """---
type: concept
topic: agentic-systems
created: 2026-07-03
updated: 2026-07-04
confidence: high
sources: [wang2024awm]
status: active
tags: [memory]
---

# Agent memory

Agent memory is persistent knowledge for agents.
"""
    result = normalize_concept_frontmatter("agentic-systems/agent-memory.md", raw)
    assert result.fields["type"] == "concept"
    assert "knotica_kind" not in result.fields
    assert result.fields.get("title") == "Agent memory"
    assert "timestamp" in result.fields


def test_date_expanded_to_rfc3339():
    normalized, warning = rfc_normalize("2026-07-03")
    assert normalized == "2026-07-03T00:00:00Z"
    assert warning is not None
