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

The load-bearing decisions are recorded as ADR drafts in
`.ai-state/decisions/drafts/` (committed, and rewritten to `dec-NNN` at
finalize):

- `notes-anchor-model` — bi-partite anchor; no `^block-id` injection in v1
- `notes-storage-folder-family` — `notes/<topic>/` at the vault root
- `notes-eval-bridge` — `curate_example` → trainset; golden deferred
- `notes-tool-decomposition` — one flat capture tool plus a `notes` dispatcher
- `notes-no-fifth-protocol-operation` — no new `read_protocol` operation

**Phase 0 is complete and committed.** Phases 1–4 are described in
`SYSTEMS_PLAN.md` § Sequencing.
