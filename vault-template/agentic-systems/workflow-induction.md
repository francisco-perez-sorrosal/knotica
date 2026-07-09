---
type: method
topic: agentic-systems
created: "2026-07-03T00:00:00Z"
updated: "2026-07-03T00:00:00Z"
confidence: high
sources: [wang2024awm]
status: active
tags: [demo-sample, workflows, learning-from-experience]
title: Workflow induction
description: "Workflow induction extracts reusable sub-routines from agent task trajectories: an LM-based"
timestamp: "2026-07-03T00:00:00Z"
---

> **Demo sample — delete freely.** This page is part of the template's demo ingest; see
> [[agent-workflow-memory]] for the full list of demo files.

# Workflow induction

## Summary

Workflow induction extracts reusable sub-routines from agent task trajectories: an LM-based
induction module reads past (or self-generated) action sequences and distills the shared
routine — a task description with reasoning-plus-action steps, abstracted away from
instance-specific values. The induced workflows are added to the agent's memory and guide
later tasks.

## Key claims

- Induction abstracts concrete trajectories into reusable workflows by removing
  instance-specific values, so one workflow serves many task variants (wang2024awm §2.2).
- Workflows can be induced offline from annotated training examples or online from the
  agent's own test-time experiences judged successful by an LM — the online path needs no
  annotations (wang2024awm §2.3).
- The sub-routine, abstract format matters: representation ablations show the format
  contributes materially to the gains (wang2024awm §4.1).

## Relations

- [[agent-workflow-memory]] — the paper that introduces this method.
- [[agent-memory]] — induced workflows are the content this memory stores.

## Open questions

- Can rule-based induction match LM-based induction at lower cost (the paper sketches both,
  §Appendix A–B)?
- When should stale or superseded workflows be evicted from memory?
