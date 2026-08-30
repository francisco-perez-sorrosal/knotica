---
id: dec-106
title: The six-phase process lifecycle contract, engraved as a processMeta registry
status: accepted
category: architectural
date: 2026-08-30
summary: Every user-triggered process declares why/what/preview/progress/outcome/next once in dashboard/src/lanes/processMeta.ts, enforced by a census closed over the ToolClient surface and validated against core/process_model.py.
tags: [dashboard, interface, registry, census, lifecycle, navigation, attention]
made_by: agent
agent_type: interface-designer
branch: main
pipeline_tier: standard
affected_files:
  - dashboard/src/lanes/processMeta.ts
  - dashboard/src/lanes/__tests__/processMeta.test.ts
  - dashboard/src/lanes/ProcessBrief.tsx
  - dashboard/src/lanes/ProcessOutcome.tsx
  - dashboard/src/lanes/stageFocus.ts
  - dashboard/src/paneRouting.ts
  - dashboard/src/lanes/home/attentionMeta.ts
  - dashboard/src/lanes/home/attentionRows.ts
  - src/knotica/core/status.py
dissent: "One more always-loaded registry is one more thing to keep true; if the six fields are not all load-bearing, the registry becomes ceremony the census merely makes mandatory."
---

# The six-phase process lifecycle contract, engraved as a processMeta registry

## Context

The user's directive: *"the user needs to be aware of every process that he needs to approve to trigger, why it's necessary, what is going to happen, what is happening while it's being executed, and what was done after it has finished, and what's gonna be next and why if there's a follow-up — engrave that in all the knotica processes."*

An audit of all 37 user-triggerable processes across the six lanes (`LIFECYCLE_DESIGN.md` §2) found the six phases distributed as: Surface 30/33 satisfied, Progress 30/33 (solved by the in-flight polish round), Preview 9/33, Justify 3/33, Outcome 5/33 with 17 more "succeeding" via a silent list re-read, and **Next 0/33** — not one process names a reachable follow-up destination.

Three places already implement pieces of the contract well: `core/gapfill_session.py::SessionNextAction{actor, do}` (dec-091's anti-dead-end guarantee), `attentionMeta.ts::ATTENTION_KIND_META{why, unlocks}`, and `HealStage.tsx:216-233`'s aborted-race card (server reason verbatim + a `NEXT STEP` microlabel). The contract is therefore not novel — it is *exemplary* where it should be *exhaustive*.

The word that decides the design is **engrave**. A per-surface convention decays at the first new stage; this repo has three working precedents for the alternative (`laneMeta.ts`, `stageMeta.ts`, `attentionMeta.ts`: a typed record over a closed union, presentation copy the wire does not carry, plus a runtime census that fails when the union and the record drift).

## Decision

Adopt the **six-phase process lifecycle contract** — Surface → Justify → Preview → Progress → Outcome → Next — and engrave it structurally rather than by convention:

1. **A new component, `dashboard/src/lanes/processMeta.ts`**, declares `ProcessId` (a closed union over every user-triggered process) and `PROCESS_META: Record<ProcessId, ProcessMeta>`. A *process* is an action that spends, mutates the vault, or hands work to another agent — reads and navigation are not processes. Each row carries `why`, `willDo`, `previewMode`, `progressMode`, `outcomeMode`, optional `outcomeFallback`, and `next`. **`next` is a discriminated union with no null member**: `terminal` is an answer, absence is unrepresentable.
2. **Two new compositions**, `ProcessBrief` (phases 2–3, built on `TermHint`/`InfoPopover`) and `ProcessOutcome` (phases 5–6, built on the existing `NEXT STEP` microlabel pattern), plus a `ProcessAction` convenience wrapper for the simple single-button case. Phase 4 gets **no new component** — the registry's `progressMode` selects and asserts against the already-shipped `Spinner`/`aria-busy` pass.
3. **A six-group census** (`processMeta.test.ts`) closed over the `ToolClient` method surface — the dashboard's only route to the server — so registry completeness holds *by construction*, not by diligence. G5 validates every `next` anchor against `LANES`/`LANE_STAGES` from the generated `processModel.ts`, making `core/process_model.py` the authority for follow-up destinations as it already is for stage copy. G4 machine-checks the previously-prose house rule that billed actions are two-phase.
4. **A navigation contract**: `onOpenLane(lane)` widens to `openAnchor({lane, stage})`, owned by `App.tsx`, with a one-shot focus request consumed only where `initialFocus` is already consulted — preserving `stageFocus.ts`'s "focus is never stolen" invariant. This also closes td-065 (`?focus=` currently discarded by `resolveLaneFocus`).
5. **Two additive `view="attention"` fields** — `gaps.open_total` and `arena.stage` — closing the two Surface holes (open gaps awaiting discovery; an aborted race needing a config decision). Cost: +2 small file reads per topic, no git, no lint, no anchor resolution — inside dec-092's stated budget doctrine. Derivation stays client-side in `attentionRows.ts`, as every other row kind already is.

## Considered Options

### A. Registry + composition + census (chosen)

- **Pro**: completeness is enforced, not remembered; the `next` type makes a dead end unwritable; matches three existing precedents so it costs no new concept; the census reuses `toolNameRegistryCensus.test.ts`'s prototype-walk and `crossLaneLinkCensus.test.ts`'s source-scan machinery verbatim.
- **Con**: 33 rows of copy to write and keep true; a `clientMethod` field couples the registry to the client surface (mitigated: it is `keyof ToolClient`, so a rename is a compile error before the census runs).

### B. A per-surface convention documented in `dashboard/CLAUDE.md`

- **Pro**: zero new modules, zero test infrastructure, lands immediately.
- **Con**: nothing fails when it is skipped. The audit's own evidence is the refutation — the two-phase billing rule *is* already a documented convention and is honoured; the "explain why" habit has no such document and is honoured 3 times in 33. Conventions hold where a test or a type backs them and decay where neither does.

### C. Server-side lifecycle metadata (extend the lane dispatchers to return why/next per action)

- **Pro**: one source of truth for MCP clients and the dashboard alike; a CLI surface would inherit it.
- **Con**: violates the stateless-server invariant's spirit — this is presentation copy, and the vault is the only durable state. It would also make every lifecycle copy edit a server change plus an artifact rebuild, and `attentionMeta.ts`'s docblock already establishes the house position that dashboard-owned rationale copy stays dashboard-owned. Rejected for the same reason `laneMeta`'s blurb/icon/shape were adopted dashboard-local.

### D. `data-testid`-based census

- **Pro**: directly enumerates controls rather than proxying through client methods.
- **Con**: `testId` is optional throughout this codebase (`ArmedButton`'s prop is `?`), so a control disables the check by omission. An enforcement mechanism a developer switches off by not typing something is not enforcement.

## Consequences

**Positive**

- Phase 6 becomes possible at all: 0/33 → every process names a destination validated against the process model.
- The "silent re-render as outcome" failure — 17 of 33 processes today — becomes unrepresentable: `outcomeMode: "refresh"` requires an `outcomeFallback` sentence.
- The billed-actions-two-phase rule moves from prose to a test assertion.
- td-064's exemption becomes legible: it appears in exactly two greppable rows, machine-required to state the cost, instead of being distributed across two components.
- td-065 closes as a by-product of the navigation contract.
- G1's `UNWIRED_CLIENT_METHODS` fixture surfaces four `ToolClient` methods (`loopSetBaseline`, `loopRebaseline`, `loopBaselinePolicy`, `baselineProbe`) with zero non-test call sites — invisible surface made reviewable.

**Negative**

- One more registry to keep true, and a census that will fail on legitimate additions until a row is written. That friction is the point, but it is friction.
- G3 (raw-trigger interdiction) is a *file*-level proxy, not a *control*-level one: a file that legitimately renders one process could add a second raw trigger and pass. G2 catches the never-wired case; the residual is "wired twice in one file, once correctly" — a two-step mistake, visible in review. Closing it fully needs a JSX AST walk and a parser dependency the dashboard does not carry.
- `§5.1`'s `gaps_awaiting_discovery` predicate (`open_total > 0 && suggestions.total === 0`) is deliberately conservative and has a known false negative: three open gaps plus one stale suggestion hides two of them. Chosen over a clever join because dec-092's "a wrong answer is worse than an absent one" applies.
- Cross-lane navigation reopens, in sharpened form. See Disconfirmation.

## Disconfirmation

**Falsifier.** Six months on, count the registry's edit history. If rows are added only when the census forces them and their `why`/`willDo` read as compliance boilerplate — near-duplicates, mechanism-describing rather than cause-naming (G7 catches literal duplicates, not tonal ones) — then the registry became ceremony and the census merely made ceremony mandatory. The concrete signal: a `why` that could be written without reading the server code. A second falsifier: if `next` anchors are overwhelmingly same-lane, CH-1's cross-lane argument was overstated and the navigation contract's cost was not repaid.

**Steelmanned runner-up.** Option B deserves more credit than the audit gives it. The four phases with the worst coverage — Justify, Preview, Outcome, Next — are *copywriting*, and no test can assert a sentence is a good one. G4 checks that `why` is non-empty and ends in a period; it cannot check that it names a cause. So the census guarantees presence, not quality, and presence-without-quality is exactly the boilerplate the falsifier describes. A convention plus a periodic human review pass would produce the same *quality* ceiling at a fraction of the structural cost, and would not couple lifecycle copy to the `ToolClient` prototype. The counter — and the reason A is chosen — is that presence is the binding constraint today: 29 of 33 processes have no Next at all, so the marginal value of guaranteeing presence is very high right now, and the quality ceiling is a problem to have *after* the void is filled.

**Reversal trigger.** Revisit if (a) `PROCESS_META` exceeds ~50 rows, at which point per-lane splitting or an inheritance/default mechanism should be considered before the file passes the 400-line target; (b) a second client (a TUI, a second web surface) needs the same lifecycle copy, which would move the registry's home to the server and re-open option C on new evidence; (c) the census requires more than one suppression in a year — a suppression is the signal that the chokepoint assumption (the `ToolClient` surface is the only route to the server) has stopped being true.
