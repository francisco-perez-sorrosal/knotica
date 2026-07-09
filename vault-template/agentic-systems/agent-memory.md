---
type: concept
topic: agentic-systems
created: "2026-07-03T00:00:00Z"
updated: "2026-07-03T00:00:00Z"
confidence: medium
sources: [wang2024awm]
status: active
tags: [demo-sample, memory, agents]
title: Agent memory
description: "Agent memory is the idea of giving a language-model agent a persistent, growing store of"
timestamp: "2026-07-03T00:00:00Z"
---

> **Demo sample — delete freely.** This page is part of the template's demo ingest; see
> [[agent-workflow-memory]] for the full list of demo files.

# Agent memory

## Summary

Agent memory is the idea of giving a language-model agent a persistent, growing store of
knowledge distilled from past experience — so the agent improves over time without changing
model weights. What is stored (trajectories, workflows, facts) and how it is selected back
into context are the core design axes.

## Key claims

- Agents that solve each task in isolation cannot learn from past successes and failures;
  an experience-derived memory is one remedy (wang2024awm §1).
- Storing *abstracted* routines rather than raw trajectories improves reuse across task
  variants — abstraction is what makes the memory transferable (wang2024awm §2.2, §4.1).
- Memory quality gates online learning: only experiences judged successful are distilled
  into memory in AWM's online setting (wang2024awm §2.3).

## Relations

- [[agent-workflow-memory]] — a concrete paper instantiating this concept for web agents.
- [[workflow-induction]] — one mechanism for filling an agent memory.

## Open questions

- This page synthesizes a broad concept from a single source — ingest a survey or a second
  memory paper (e.g. episodic-memory or reflection-based approaches) to raise confidence.
