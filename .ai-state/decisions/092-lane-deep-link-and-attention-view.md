---
id: dec-092
title: Lane deep-linking via open_dashboard, and Home reads a new wiki_status attention view
status: accepted
category: architectural
date: 2026-08-10
summary: open_dashboard gains `lane` and `focus`, its `topic` default becomes vault-wide, and unknown values degrade rather than error; the cross-topic Home inbox reads a new `wiki_status view="attention"` under three hard budget rules — no lint walk, no note-anchor resolution, and a 10-second visibility-paused poll — moving the cross-topic attention projection out of the CLI-only nudge into a shared one.
tags:
  - mcp
  - dashboard
  - deep-linking
  - status
  - swimlanes
  - performance-budget
made_by: agent
agent_type: interface-designer
branch: worktree-process-swimlanes
pipeline_tier: full
dissent: The attention view inherits gather_wiki_status's single-topic signature that it must ignore, and it drops note drift — a signal the user already sees at every session start — so the cheap projection is bought by making the status tool's contract less coherent and Home less complete than the CLI nudge it supersedes.
affected_files:
  - src/knotica/mcp_server/app_ui.py
  - src/knotica/core/status.py
  - src/knotica/cli/status.py
  - dashboard/src/App.tsx
  - dashboard/src/toolClient.ts
---

## Context

Two gaps block the lane redesign's navigation layer.

**Deep-linking.** `open_dashboard` accepts only `topic` and `vault` (`app_ui.py:53`), and its `topic`
default is the hardcoded literal `"agentic-systems"`. In the MCP-App bridge mount the dashboard is
loaded as raw HTML into a host-controlled sandbox origin — there is no knotica URL, so `?pane=`,
`?topic=`, `?vault=` never reach the app; the bridge's only channel is `app.ontoolinput`, fed by
`open_dashboard`'s arguments. **Claude therefore cannot open a specific pane today, and could not open a
specific lane after the redesign.** Over HTTP a lane deep-link is one allowlist entry; in the bridge it
requires a new tool argument and a matching reader. The tool's description is also stale — it advertises
"Vault, Ask, Loop, Arena, Ingest, **Golden**", two panes short and one rename behind — and that string is
what a client LLM reads when deciding to open the dashboard.

**Home.** Home is a cross-topic attention inbox spanning every topic in the active vault. The per-topic
counts it needs already arrive with every vault-wide `wiki_status` call every 2 s and are discarded for
all but the selected topic — free. But `gate`, `loop` and `compile` are **structurally null** whenever
the scope is not exactly one topic (`core/status.py:405-432`, `:385-389`): a whole-vault read returns
`{state:"unknown"}` and `{alive:false, …}` unconditionally. So the two highest-value attention signals —
"topic B is compiling", "topic A's baseline is unreachable" — cannot be served by the existing call. And
`view="summary"` is **already over budget for its own 2 s poll**: `_notes_summary` calls `list_notes`,
whose anchor resolution spawns one `git show` subprocess per unique (sha, path) anchor per topic
(`core/vcs.py:581-594`), and it additionally runs a full-vault `lint_vault` walk once per call,
unconditionally.

A shipped precedent exists for the *content*: `cli/status.py::_render_nudge` already computes an
attention line (pending suggestions | refused-awaiting-rework | compile-ready | notes drifted) by summing
over `payload["topics"]`, consumed by the session-start hook.

## Decision

**1. `open_dashboard(topic="", vault="", lane="", focus="")`.**

- `topic` defaults to `""` = vault-wide. A hardcoded topic name as a tool default silently retargets a
  vault that has never had that topic; `wiki_status` already accepts `topic=""` for vault scope.
- `lane` ∈ `home|learn|answer|improve|fill|tend`; omitted → `home`, the "what needs me" surface.
- `focus` is **one free-form string** — a stage id (`gate`) or an object id (`s_1a2b3c4d`) — not N typed
  parameters of the same type.
- **Unknown `lane` or `focus` degrades, never errors**: unknown lane → `home`, unknown focus → the lane's
  own watermark. A navigation tool is not a place to fail a call the model can trivially get slightly
  wrong.
- The bridge's `ontoolinput` parser reads `lane`/`focus` alongside topic/vault; HTTP adds `?lane=` and
  `?focus=` and keeps `?pane=` as a bookmark alias map (`vault→home`, `ask→answer`, `loop→improve`,
  `arena→improve&focus=heal`, `datasets|golden→improve&focus=instrument`, `ingest→learn`,
  `sources→fill`, `notes→tend&focus=drift`) — the existing `golden→datasets` alias is the precedent.
- The description is rewritten in lane vocabulary and states what the tool does **not** do (it opens a
  view; it performs no vault operation and mutates nothing).

**2. Home reads `wiki_status view="attention"`, not a new tool.** `VALID_STATUS_VIEWS` is a declared
extension point whose existing non-default member (`scope`) exists precisely to be a cheaper projection
of the same aggregation; a new tool would re-implement vault resolution, topic enumeration, the
`schema_version` envelope, the `NOT_CONFIGURED` contract and `TopicNotFoundError` for no new seam; and a
36th registration moves the client LLM's routing distribution at the exact moment the rename is trying
to shrink the surface.

**Three hard budget rules — the decision is as much these as the view itself:**

- **No lint walk.** `attention` is not "summary minus drift". It must also drop `view="summary"`'s
  unconditional full-vault `lint_vault` pass and report the **last-lint date and its staleness** instead.
  Missing this makes `attention` no cheaper than `summary`.
- **No note-anchor resolution.** Drift is a Tend stage with an explicit run control, and Home carries a
  **default-collapsed** drift row that makes the expensive call only on expansion — progressive
  disclosure applied to cost, not just to attention.
- **Poll at 10 s and pause on `visibilitychange`.** Every Home signal is human-paced; the 2 s cadence
  exists for in-flight progress, which is a lane concern.

**3. Cross-topic runner liveness must not inherit `_gate_and_loop`'s stub**, which returns
`{alive:false}` unconditionally at multi-topic scope and would make Home report "runner: off" for every
topic — actively wrong, and worse than absent. `service/manager.py::status()` already computes per-topic
liveness for all watched topics with no MCP surface; `attention` uses that projection, or the same
underlying `read_runner_liveness` per topic. Two producers of one signal are resolved here, not later.

**4. One projection, two renderings.** `knotica status --nudge` and Home's dashboard rows render the same
items with the same field names; `--json` emits them verbatim. The CLI projection **exits 0
unconditionally** — an inbox with items is not an error, emptiness is signalled by empty stdout, and a
nonzero code would break the session-start hook.

## Considered Options

### A. A new dedicated attention tool

Pros: a clean signature with no `topic` argument to ignore; independently evolvable.
Cons: re-implements five existing contracts for no new seam, and adds a registration while the rename is
reducing them. Rejected.

### B. `wiki_status view="attention"` (**chosen**)

Pros: an existing declared extension point used exactly as `scope` established; its inputs are a superset
of `scope` and a subset-plus-delta of `summary`, computed inside the loop `_topic_statuses` already runs;
zero change to the tool-routing distribution. Cons: inherits a `topic` argument the view must ignore.

### C. Counts-only Home from the existing vault-wide call

Pros: **free today** — the per-topic counts already arrive every 2 s and are thrown away.
Cons: cannot say "topic B is compiling" or "topic A's baseline is unreachable", the two highest-value
signals, because `_gate_and_loop` stubs them at multi-topic scope. Home degrades to a slightly prettier
version of the existing CLI nudge.

### D. N topic-scoped `wiki_status` calls, one per topic

Pros: no new server surface at all; every rich signal available.
Cons: at 5 topics and the current cadence this is 6 calls every 2 s, five of them each running a lint
pass and several git reads. This is the single largest cost item the redesign could incur. Rejected.

## Consequences

**Positive**

- Claude can open a specific lane in Claude Desktop for the first time — the redesign's premise ("Claude
  sends you to the Fill lane") stops being HTTP-only.
- The cross-topic attention projection moves from a CLI-only renderer into a shared one, so the dashboard
  and the session-start hook cannot disagree.
- Home's marginal cost (~4–6 vault-wide git subprocesses plus ~4 small JSON reads per topic) is **cheaper
  than the note-drift resolution the summary view already performs at 2 s**.
- The budget rules make an existing latent cost problem explicit rather than inheriting it silently.

**Negative**

- `gather_wiki_status` gains a view whose semantics ignore its `topic` parameter — a real wart in an
  otherwise coherent contract.
- Home is **less complete than the CLI nudge it supersedes**: drift is present at session start and
  collapsed on Home.
- Three views now share one aggregation function with three different cost profiles; a future change to
  `_topic_statuses` can silently blow the attention budget with no gate to catch it.
- `open_dashboard`'s degrade-never-error rule means a typo'd lane is invisible to the caller — deliberate,
  but it forecloses ever using an unknown lane as a signal.

## Disconfirmation

**Falsifier.** A measurement showing `view="attention"` at 10 s costs more than `view="summary"` at 2 s on
a realistic vault — which would mean the branch-tip scans and per-topic JSON reads dominate rather than
the lint walk and anchor resolution, and the whole "cheaper projection" premise is inverted. This is
measurable before implementing: time `list_branch_tips` × 4 against one `lint_vault(store, "")` plus
`_notes_summary` on the author's vault.

**Steelmanned runner-up.** Option C is genuinely defensible: the counts are free, they already drive two
tab badges, and the shipped `_render_nudge` proves that pending-suggestions + refused-awaiting-rework +
compile-ready + drifted is enough to make a user act. "Topic B is compiling" is *interesting* but not
*actionable* — it belongs to the Running class, which the design itself marks informational. A
counts-only Home ships with **zero** server change, zero new budget risk, and no new view to keep honest;
the rich signals could be added later, driven by a felt gap rather than an anticipated one.

**Reversal trigger.** Revisit if (a) the falsifying measurement above holds, (b) `_topic_statuses` grows
and the attention view's cost silently converges on summary's — which argues for a separate code path
rather than a shared one, or (c) users report that a Home without drift misses the thing they most need,
in which case either drift gets a cheap proxy or the collapsed-by-default row becomes expanded-by-default
with an accepted cost.
