---
id: dec-031
title: Added-and-failing golden ids classify through the gap cascade
status: accepted
category: behavioral
date: 2026-07-19
summary: Ids in held_out_delta.ids_added whose current scores fail a named floor enter the four-way cascade like per-id regressors, so newly frozen questions can surface genuine gaps.
tags: [gap-classifier, eval, loop, gapfill]
made_by: agent
agent_type: orchestrator
branch: worktree-hackathon-loop-ideas
pipeline_tier: lightweight
re_affirms: dec-024
affected_files: [src/knotica/core/gap_classifier.py]
---

## Context

dec-024's cascade reads regressed ids exclusively from `held_out_delta.per_id` — the
prior∩current intersection. A newly frozen golden question lands in `ids_added` with no prior
score, so the "freeze a question about missing content" gap-manufacture path can never classify:
the loop sees the scalar regression but the classifier is blind to the id causing it.

## Decision

`regressed_ids` extends to include ids from `held_out_delta.ids_added` whose CURRENT per-example
scores fail a named deterministic floor (`ADDED_ID_FAILING_FLOOR`: qa_accuracy or quality below
0.5). Such ids enter the unchanged four-way cascade with an empty-delta evidence context; an added
id that scores well never classifies (a healthy new question is not a gap). This re-affirms
dec-024's cascade and precedence untouched — only the eligibility set grows.

## Considered Options

Classify all added ids unconditionally (rejected: healthy new questions would spam records);
require operator opt-in per freeze (rejected: defeats autonomy); leave as-is and document the
limitation (rejected: the natural "test the wiki on something it lacks" path silently no-ops).

## Consequences

Positive: freezing a probing question becomes a legitimate, honest gap-discovery instrument.
Negative: the floor constant is a judgment value; a borderline-scoring added id (0.5–0.7) stays
invisible until it regresses generation-over-generation — acceptable, the per-id path catches it
next generation.
