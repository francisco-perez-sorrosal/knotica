---
id: dec-083
title: A gate verdict is keyed on its inputs, and a refusal resumes from quarantine
status: accepted
category: architectural
date: 2026-08-08
summary: A stamped gate_outcome is replayed only while the candidate tree, golden manifest, baseline and harness it was computed from are unchanged; a refused ingest re-opens from its quarantine ref instead of starting empty.
tags:
  - gap-fill
  - gate
  - idempotency
  - cache-invalidation
  - source-ingest
  - quarantine
  - provenance
made_by: agent
agent_type: orchestrator
branch: main
pipeline_tier: standard
affected_files:
  - src/knotica/core/gate_inputs.py
  - src/knotica/core/source_gate.py
  - src/knotica/core/source_ingest.py
  - src/knotica/core/branch_namespaces.py
  - src/knotica/mcp_server/tools_source_ingest.py
dissent: A verdict fingerprint is four more things that can be wrong; a simpler "always re-evaluate a refusal" rule needs no new module and cannot go stale.
---

## Context

A live ingest session on the `decision-making` topic hit a dead end that no surface explained. A source candidate was refused — correctly; the SEP entry's §2.4 restatement of prospect theory did dilute retrieval against chunked primary sources. Recovering from that correct refusal was impossible.

Two independent mechanisms closed the loop:

**Re-opening the ingest returned a fresh, empty context.** `open_ingest` decided "resume or create" by asking whether `loop/wip/<topic>/source-<id8>` exists. A refusal renames that branch to `loop/x/...`, so the WIP name is absent while the work is not — six stored source chunks and four written pages, intact on the quarantine ref, invisible to the check. The documented never-restart guarantee held for an *interrupted* ingest and silently broke for a *refused* one. The cost was re-transmitting roughly 20,000 words of verbatim source.

**Every resubmit replayed the stored verdict.** `_submit_payload` returned the recorded `gate_outcome` whenever one existed, keyed on `suggestion_id` alone. By the time the operator resubmitted, the candidate had been rebuilt, the golden set replaced (9 → 21 questions), and the baseline corrected from 0.9548 to 0.6562. The response still quoted `baseline_scalar: 0.9548`, indistinguishable from a fresh evaluation, and short-circuited before the dry-run preflight — so the missing `lint_clean`/`source_present`/`would_evaluate` block was the only visible symptom.

A third symptom was reported as a separate defect: `pending_candidates: []` beside `refused_awaiting_rework: 1`, with `run_once` reporting "no pending loop branches". That is **not** a defect. `loop/wip/` is private by design, and only `publish_ingest` promotes it to `loop/c/` — the guarantee that the gate never evaluates a half-written candidate. The queue was empty because the replay above meant `_apply_payload` never ran, so `publish_ingest` was never called.

## Decision

**Key the replay on the verdict's inputs.** A new `core/gate_inputs.py` fingerprints the four things a verdict is a function of — candidate tree sha, golden manifest sha, baseline scalar, harness version — and the gate stamps it into `gate_outcome.inputs`. A submit replays only while all four still match, and says `cached: true` when it does. A component unknown on exactly one side counts as changed.

**Only a refusal expires.** A `merged` verdict is terminal: the work is on the default branch, the suggestion has advanced to `ingested`, and no candidate ref survives to re-gate. Expiring one would reach `open_ingest` and fail `SUGGESTION_NOT_APPROVED` on an ingest that genuinely finished.

**Resume from the quarantine ref.** When the WIP branch is absent but a quarantine ref resolves, `open_ingest` branches the session from it, reports `state: "resumed"`, and names the origin in `resume.restored_from`. It branches from the ref, never moves or consumes it, so the audit trail survives and a second rework starts from the same place.

**`dry-run` always runs its preflight**, replay or not.

The fingerprint records the ref where the work *comes to rest*, not the tip that was evaluated: on a refusal the quarantine-diff artifact lands after the rename, so stamping the evaluated tip would guarantee a mismatch against the very ref a resume branches from — and the idempotency guard would be gone.

## Considered Options

### Fingerprint the inputs (chosen)

- Replay survives exactly where it is correct — a byte-identical resubmit still costs nothing.
- Four components is four things to keep in sync as the gate evolves.

### Always re-evaluate a refused candidate

- No new module, nothing to go stale.
- Deletes the double-billing guard the cache was built for: an operator resubmitting an untouched candidate pays a full eval to be told the same thing.

### Expire on a TTL

- Trivial.
- Wrong axis entirely. The reported incident's inputs changed within minutes; an unchanged candidate stays valid for weeks. Time is not what a verdict depends on.

## Consequences

**Positive.** The reported dead end is gone in both directions, and a stale verdict is no longer reportable as a fresh one. `gate_inputs` is reusable for any future "is this measurement still current?" question. The `withdraw` transition (added alongside) gives `approved` an exit that does not falsely assert an ingest.

**Negative.** A verdict stamped before this existed carries no fingerprint and is never replayed, so the first resubmit after upgrade re-evaluates. `gate_inputs` imports `evals.golden` on the MCP cold-start path — safe today because that module keeps `dspy` behind `TYPE_CHECKING`, but it is now a constraint that module must keep.

## Disconfirmation

**Falsifier.** An operator resubmitting a genuinely untouched candidate sees a fresh evaluation. That would mean a component reports unstable values across calls — most likely `harness_version`, which folds in the installed `dspy` version.

**Steelmanned runner-up.** "Always re-evaluate a refusal" is the honest minimum: a refused candidate exists to be reworked, so the case where nothing changed is rare, and paying one eval for it buys a rule with no state to corrupt. If fingerprint staleness ever produces a wrong replay, this becomes the right answer.

**Reversal trigger.** A second incident traced to a fingerprint component that was equal but should not have been. At that point the fingerprint is not conservative enough and the simpler rule wins.
