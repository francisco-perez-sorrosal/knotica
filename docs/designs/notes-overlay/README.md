# Notes overlay — design set

The personal-notes (marginalia) layer over KB topics: private, human-authored
reflections anchored to a concrete span in a topic's pages, kept in their own
graph, excluded from every quality measurement, and harvestable as a source of
real human questions for eval datasets.

These are pipeline documents, preserved here because the feature spans four
phases and `.ai-work/` is gitignored — a removed worktree would otherwise take
the design with it.

| File | What it is |
|---|---|
| `SYSTEMS_PLAN.md` | The architecture: bi-partite anchor model, storage/folder-family, loop interaction, eval bridge, phased sequencing, rejected alternatives |
| `INTERFACE_DESIGN.md` | The surfaces: tool decomposition, the one-shot capture handshake, skill routing, `/knotica:note`, dashboard pane mockups, the on-disk note format, error grammar |
| `TASK_BRIEF.md` | Intent, the four locked user decisions, surfaced assumptions, health guards |
| `PHASE_3_4_BRIEF.md` | The handoff for the remaining phases: what Phase 2's verification changed, the free measurement that gates the billed spikes, the two priorities that inverted, open debt, and the traps |

The load-bearing decisions are finalized in `.ai-state/decisions/`:

- `dec-056` — no fifth `read_protocol` operation
- `dec-057` — one flat capture tool plus a `notes` dispatcher
- `dec-058` — bi-partite anchor; no `^block-id` injection in v1
- `dec-059` — `curate_example` → trainset; golden deferred
- `dec-060` — `notes/<topic>/` at the vault root behind a folder family
- `dec-061` — anchor history is append-only, supersession derived per page
- `dec-062` — anchor recovery is bounded by candidate-window geometry, not by
  `guess_threshold`; fix the geometry and leave the threshold

`dec-058` and `dec-060` carry dated amendment notes correcting claims that did
not survive implementation — read those, not only the original text.

**Phases 0, 1 and 2 are complete, verified and merged.** Phase 2 was verified
against all fourteen acceptance criteria after the fact; what that found, and
how it reshapes what remains, is in `PHASE_3_4_BRIEF.md`. Phases 3 and 4 are
described in `SYSTEMS_PLAN.md` § Sequencing and are not started.
