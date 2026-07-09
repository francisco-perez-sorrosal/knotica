---
type: paper
topic: agentic-systems
created: "2026-07-03T00:00:00Z"
updated: "2026-07-03T00:00:00Z"
confidence: high
sources: [wang2024awm]
status: active
tags: [demo-sample, web-agents, memory, workflows]
title: "Agent Workflow Memory (Wang et al., 2024)"
description: "Agent Workflow Memory (AWM) makes language-model web agents learn from their own experience:"
timestamp: "2026-07-03T00:00:00Z"
---

> **Demo sample — delete freely.** This page is part of the template's demo ingest. Deleting
> it, [[workflow-induction]], [[agent-memory]], and `sources/agentic-systems/wang2024awm.md`
> (plus their index entries) leaves a clean vault.

# Agent Workflow Memory (Wang et al., 2024)

## Summary

Agent Workflow Memory (AWM) makes language-model web agents learn from their own experience:
it induces reusable *workflows* — common sub-routines abstracted from past task trajectories —
and stores them in the agent's memory to guide future generations. It works both offline (from
training examples) and online (from the agent's own judged-successful test-time experiences,
with no annotations).

## Key claims

- AWM induces commonly reused routines ("workflows") from agent experiences and selectively
  provides them to the agent to guide subsequent generations (wang2024awm §Abstract).
- On two major web-navigation benchmarks — Mind2Web and WebArena, together covering 1000+
  tasks from 200+ domains — AWM improves the baseline by 24.6% (Mind2Web) and 51.1% (WebArena)
  relative success rate, while reducing the steps needed to solve WebArena tasks
  (wang2024awm §Abstract).
- Online AWM generalizes robustly: in cross-task, cross-website, and cross-domain evaluations
  it surpasses baselines by 8.9–14.0 absolute points, with the margin widening as the
  train-test distribution gap grows (wang2024awm §3.2.2).
- A workflow is a task description plus a sequence of steps (reasoning + action), abstracted
  from concrete examples by stripping instance-specific values (wang2024awm §2.2).

## Relations

- [[workflow-induction]] — the method this paper introduces for extracting workflows from
  trajectories.
- [[agent-memory]] — AWM is an instance of augmenting an agent with an external, growing
  memory.

## Open questions

- How well does workflow induction transfer beyond web navigation (e.g. to tool-use or
  coding agents)?
- What is the memory-quality failure mode — do low-quality induced workflows compound
  errors in the online setting?
