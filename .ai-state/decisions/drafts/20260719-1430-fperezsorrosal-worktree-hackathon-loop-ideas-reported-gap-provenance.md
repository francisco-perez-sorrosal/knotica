---
id: dec-draft-1b59b7aa
title: NL-reported gaps — origin provenance on GapRecord and a deterministic gap_report tool
status: proposed
category: behavioral
date: 2026-07-19
summary: GapRecord gains an additive origin field (measured|reported); a gap_report MCP tool lets the client-as-brain file candidate gaps from conversation, flowing into the same discovery queue with distinguishable provenance.
tags: [gapfill, mcp, client-as-brain, provenance, desktop-ux]
made_by: agent
agent_type: orchestrator
branch: worktree-hackathon-loop-ideas
pipeline_tier: lightweight
re_affirms: dec-025
affected_files: [src/knotica/core/records.py, src/knotica/core/gapfill.py, src/knotica/mcp_server/tools_suggestions.py]
---

## Context

Gaps are eval-proven only (loop-written on measured regressions). But the transparent-UX goal
wants Claude Desktop to capture gaps exposed conversationally — the wiki answers poorly, the user
confirms the gap. Without provenance, mixing conversational reports into the eval-proven queue
would erode the verifier-driven discipline the autoresearch brief demands.

## Decision

`GapRecord` gains an additive `origin` field, default `"measured"` (schema stays v1, additive-only
evolution). A deterministic `gap_report` MCP tool writes `origin="reported"` records
(fault_class genuine_gap, open, qa_id derived deterministically from the question text, dedup
against open gaps, own VaultTransaction via the existing write path). The tool description is the
NL hook instructing the Desktop client when to call it. `SuggestionRecord` gains additive optional
`gap_origin` so the queue and its consumers can always distinguish eval-proven from reported.
The drain filter is unchanged — reported gaps flow into discovery automatically.

## Considered Options

A separate reported-gaps file (rejected: two queues, two drains, double surface); letting the
client write gaps.jsonl via generic write tools (rejected: schema discipline and dedup belong in
one deterministic tool); LLM-side gap synthesis server-side (rejected: violates client-as-brain).

## Consequences

Positive: the flywheel turns from natural language; provenance keeps the eval-proven signal clean.
Negative: reported gaps carry no per-id eval evidence (their evidence fields are empty/None by
construction — consumers must not assume measured evidence); a chatty client could over-report —
dedup plus human approval on every suggestion bounds the blast radius.
