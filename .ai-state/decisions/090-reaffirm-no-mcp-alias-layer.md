---
id: dec-090
title: A marketplace channel is not a consumer — dec-050 is re-affirmed, narrowed to aliases that carry schema weight
status: re-affirmation
category: architectural
date: 2026-08-10
re_affirms: dec-050
summary: "The lane rename removes roughly 27 flat tool names, which looks like dec-050's reversal trigger; it is not, because that trigger names an MCP consumer and bit-agora supplies only a channel — so dec-050's no-alias ruling governs this rename unchanged, narrowed to aliases that add a registered schema, which leaves zero-weight dispatcher-action aliases permitted."
tags: [mcp, tool-surface, deprecation, aliases, marketplace, distribution, reversal-trigger]
made_by: agent
agent_type: systems-architect
branch: worktree-process-swimlanes
pipeline_tier: full
affected_files:
  - src/knotica/mcp_server/
  - src/knotica/mcp_server/dispatch_telemetry.py
  - .claude-plugin/plugin.json
  - CONTRIBUTING.md
dissent: "Re-affirming on 'no external consumer observed' rests on an absence this project cannot measure — there is no install telemetry, no issue-tracker signal, and no way to distinguish 'nobody installed it' from 'nobody who installed it has complained yet' — so the evidence for the re-affirmation is exactly as unobservable as the evidence that would supersede it, and this rename is the first one that would actually break someone."
---

## Context

`dec-050` removed all 26 deprecated flat-tool aliases in July 2026, one day after `dec-045` introduced
them: *"knotica has exactly two MCP consumers, Claude Code and Claude Desktop, both configured and
operated by the project's single user."* Its recorded reversal trigger reads:

> knotica gaining any MCP consumer outside `fperezsorrosal`'s own Claude Code / Claude Desktop
> configuration reopens this decision.

The swimlane redesign now performs the rename that trigger was written to guard. Under
`dec-094` and `dec-088` the surface is re-cut into roughly fourteen flat
conversational primitives plus six lane dispatchers, which **removes roughly 27 published flat tool
names** and asks the same question `dec-045` asked and `dec-050` answered: does a migration window get an
alias layer?

Two facts frame it. The plugin now ships through the external `bit-agora` marketplace (project
`CLAUDE.md`; `CONTRIBUTING.md § After the release`) — a distribution channel that did not exist when
`dec-050` was written. And the alias implementation is recoverable verbatim at `git show ab7ac35`:
`DEPRECATED_ALIASES`, `deprecation_suffix(alias)`, `record_deprecated_alias(alias)`, and the thin-wrapper
registration shape.

## Decision

**`dec-050` stands, and governs this rename unchanged: the removed tool names get no alias layer.**
Re-affirmed rather than superseded, on two grounds, and narrowed on one axis.

**Ground 1 — the trigger names a *consumer*; what exists is a *channel*.** `bit-agora` makes an external
install possible; no external install, issue, fork or report is observed. A trigger that fired on the
mere reachability of the condition it names would have fired the day the repository was made public. The
distinction is load-bearing precisely because `dec-050`'s argument turned on the *actual* consumer
population, not the addressable one.

**Ground 2 — `dec-050`'s reasoning applies to this rename on its own terms.** Its objection to aliases
was that every additional registration is an always-loaded schema the model reasons over on every
conversational turn, and that a warn-only alias with no known consumer never converges to removal.
Both hold here with more force, not less: the rename's *primary* justification is getting the surface
below the count at which tool-selection quality degrades, and an alias layer would restore every removed
name as a registration, defeating the change it was meant to soften. A removed tool name fails **loudly**
— MCP returns an unknown-tool error the model sees and can retry against the current `tools/list` — which
is the failure mode `dec-050` accepted as adequate.

**Narrowing.** `dec-050` ruled on 26 flat-tool aliases and had no occasion to consider any other kind. Its
universality is narrowed here to **aliases that add a registered schema**. Dispatcher-**action** aliases
are outside it and are permitted: an action is a free-form `str` parameter, not a schema enum, so an
alias is one tuple entry plus one mapping line at **zero** always-loaded cost, and it can carry a
`deprecation` note in the returned envelope. The same exemption covers the non-MCP surfaces, none of
which the model reads: `argparse` aliases with a `Console.warn()` to stderr, slash-command tombstone
files, and the dashboard's `?pane=` alias map (which already has a precedent in
`App.tsx:51-53`'s `golden → datasets`).

**The trigger is unchanged**: an observed MCP consumer outside `fperezsorrosal`'s own configuration —
an issue, a discussion, a fork calling the tool names, or any install signal. That event reopens the
question, and `dec-045`'s deferred Option B (a lazy-catalog meta-tool) is then the alternative to weigh
against restoring `ab7ac35`.

## Considered Options

### Option 1 — Re-affirm `dec-050`, narrowed to schema-bearing aliases (chosen)

Matches the trigger's wording and applies its reasoning to the case it was written for. Costs nothing,
preserves the rename's main benefit, and leaves the recovery path documented and intact.

### Option 2 — Supersede `dec-050`; treat the marketplace channel as having fired the trigger, and alias every removed tool name

Rejected. It pays `dec-050`'s exact cost to protect an unobserved population, and it directly defeats the
rename's purpose: restoring 27 names as registrations puts the surface back above the count the
consolidation exists to get under. It also re-enters the never-converging state `dec-050`'s Option 3 was
rejected for — an alias with no known consumer has no end condition.

### Option 3 — Alias the removed names for exactly one release, then delete

`dec-045`'s original ruling, replayed. Rejected for the reason `dec-050` gave and this rename sharpens: a
release cycle means nothing to a consumer that does not exist, and one release of doubled schema weight
is a real cost paid against a hypothetical.

### Option 4 — Widen the trigger to fire on the channel rather than on a consumer

Rejected as an over-correction. The channel is permanent, so a channel-worded trigger is permanently
fired and stops discriminating between the case that matters and the case that does not.

### Option 5 — Adopt install telemetry so the trigger becomes measurable

Not chosen and deliberately not rejected on the merits: out of scope for this pipeline, and it carries
its own privacy question. Named so a future reader sees it was considered rather than missed.

## Consequences

**Positive.** The rename delivers its main benefit — a surface below the tool count at which selection
quality degrades — instead of cancelling it with an alias layer. The properties `dec-045` and `dec-003`
were both written to protect are preserved. `dec-050`'s reversal condition becomes something a future
reader can check, rather than a condition whose ambiguity (channel or consumer?) had to be re-litigated
once already. The zero-weight surfaces get real deprecation affordances, which `dec-050` never prohibited
and never considered.

**Negative.** This is the first change that would actually break an external consumer if one exists, and
the re-affirmation rests on an unobserved absence: no install telemetry, no issue-tracker signal. A
third-party user who installed the plugin and silently worked around a vanished tool name would leave no
trace at all. The recovery path is documented but untested since `cdbfcc7` removed it, and `ab7ac35`
restores against a 7-dispatcher surface that is now 9 — a revival is a port, not a revert. And the
narrowing means the deprecation story now differs by surface, which a reader must hold in their head.

## Disconfirmation

- **Falsifier:** any evidence of an MCP consumer outside `fperezsorrosal`'s own configuration — a
  third-party issue, a fork calling the tool names, a marketplace install signal, a support request.
  That falsifies the ground this re-affirmation stands on, and `dec-050` should then be superseded rather
  than re-affirmed a second time.
- **Steelmanned runner-up:** Option 2. Publishing through a marketplace is a deliberate act of inviting
  consumers, and a decision that waits for the *first complaint* has already broken the first user it
  acquired. The asymmetry is real: a standing alias layer costs a bounded, known amount continuously,
  while breaking an unknown user costs an unbounded amount once, as a bad first impression on a project
  whose entire distribution strategy is a public marketplace. Option 2 loses here only because the
  alias layer would cancel the rename's own justification — which is a reason about *this* rename, not a
  reason about aliases.
- **Reversal trigger:** unchanged from `dec-050` — knotica gaining any observed MCP consumer outside
  `fperezsorrosal`'s own Claude Code / Claude Desktop configuration.

## Prior Decision

Re-affirms **`dec-050`**, which stays `accepted` and is not modified by this record. Its ruling — remove
the aliases, carry no migration window — is found still correct, and correct *for this rename* rather
than merely correct in the abstract: the trigger it named has not fired, because a distribution channel
is not a consumer, and its schema-weight reasoning applies with more force to a rename whose purpose is
to shrink the surface.

The narrowing is the only substantive addition. `dec-050` ruled on 26 flat-tool aliases; its universality
is narrowed to aliases that add a registered schema, leaving dispatcher-action aliases, `argparse`
aliases, slash tombstones and the `?pane=` map permitted. `dec-045`'s two-tier dispatcher topology is
untouched — `dec-094` re-cuts it along lanes and does not revisit it.

> **Finalize obligation.** `scripts/check_adr_health.py` requires `re_affirms`/`re_affirmed_by`
> reciprocity among finalized records, and separately rejects a finalized record naming a
> `dec-draft-<hash>` id. `.ai-state/decisions/050-remove-deprecated-tool-aliases.md` is therefore left
> untouched while this record is a draft, and the reciprocal `re_affirmed_by: [<this record's dec-NNN>]`
> **must** be appended to it in the finalize commit, or `make verify` fails from that point on.
