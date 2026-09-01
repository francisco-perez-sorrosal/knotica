## v0.4.0 (2026-09-01)

### Feat

- **mcp**: every parameter grounded from one declaration, and success says what's next

### Fix

- **state**: dec-120 cites only tracked paths — .git/hooks/ never exists in a clone, so the ADR gate failed only in CI
- **gates**: the citation discipline becomes real — both dead layers re-armed, every pointer resolved

### Refactor

- **evals**: the instrument's fear becomes a checked property, then harness and golden become packages
- **core**: the loop's fourth pass — observe and gap-redirect get their own modules
- **core**: the record families get their own modules, and GapRecord keeps what it does not understand

## v0.3.0 (2026-08-31)

### Feat

- **ux**: Home parity across surfaces, a gap-dismiss affordance, process docs caught up
- **home**: surface the two conditions the attention inbox could not see
- **dashboard**: make every follow-up a destination the user can reach
- **chrome**: engrave the lifecycle on the chrome trio and the CLI migrate
- **improve**: engrave the lifecycle on the seven remaining Improve processes
- **handoff**: engrave the lifecycle on the two ingest handoffs
- **fill**: engrave the lifecycle on discovery and the four triage verbs
- **answer**: engrave the lifecycle on Answer's four verbs and the probe
- **tend**: engrave the lifecycle on the five mutating tend processes
- **dashboard**: render the lifecycle brief and outcome on the eval pair
- **dashboard**: declare the process lifecycle contract as a registry
- **dashboard**: give Ingest's you-controls a real, informative flow
- **dashboard**: let CopyBlock carry a payload and a visible label
- **dashboard**: finish the busy-affordance census across every lane
- **dashboard**: put the busy affordance on the shared action primitives
- **dashboard**: add the house busy affordance
- **dashboard**: the scorer switch shows its outcome as changed state
- **dashboard**: the scorer switch confirms when it takes effect
- **dashboard**: Heal's arena variants describe what each one tries
- **dashboard**: rank and rationale the Home attention queue
- **core**: each arena variant records what it changed
- **core**: group dataset roles by producer, not consumer
- **dashboard**: the Instrument file roles show what they are and how they compose
- **dashboard**: triage the Fill approve queue by priority, densely
- **core**: a reported gap cannot create a topic
- **dashboard**: switch the arena scorer where the abort is reported
- **loop**: the cadence action reads and writes arena_scorer
- **dashboard**: the heal view explains each variant, the abort, and the next step
- **dashboard**: rebuild the Instrument interior on the stage-body grammar
- **dashboard**: rebuild the Observe interior on the stage-body grammar
- **dashboard**: append the stage-body grammar CSS, delete dead arena-lane rules
- **dashboard**: add the stage-body grammar's four presentation primitives
- **core**: derive improve stage states from vault evidence
- **dashboard**: render the unknown stage state honestly
- **dashboard**: give the improve rail a focus axis, and stop calling healthy states failures
- **dashboard**: loop strip above every railed lane, rail restyled
- **dashboard**: Home lane-card grid + attention table + drift popover
- **dashboard**: restructure chrome into context row, pill nav, creation drawer
- **dashboard**: add the overlay and shared presentation primitives
- **dashboard**: add the 26-glyph inline icon set
- **dashboard**: adopt neutral-ramp token spec and mono voice

### Fix

- **gates,ci**: the surface gate reads fences and frontmatter, and dashboard.yml joins the hardened five
- **core,hooks**: honest rails, honest records, and the gate's one flake made deterministic
- **dashboard**: uniform spend grammar, contrast as a gate, dead shell deleted, safe reads
- **gates**: the partition and the ADR body become checked properties, and a dead clause leaves
- **dashboard,cli**: outcomes render where their controls live, and every send confirms
- **mcp**: every fix text names a call the client can make, and the schema says what the prose knew
- **fill**: the queue's contracts become true — reopen re-sources, drains don't clobber, the gate outranks both writers
- **loop**: the instrument guard reaches all four baseline consumers, and spend gets its gate
- **loop,fill,improve**: honest instruments, a guarded gate, a self-healing queue
- **fill**: discovery stops re-proposing what the vault already holds
- **fill**: un-strand approved suggestions — dismiss cascades, refusals name the exit
- **dashboard**: stop erasing a button's accessible name while it is busy
- **fill**: branch the fill command on session state, not next.actor
- **dashboard**: a decided suggestion transforms in place, never vanishes
- **cli**: the watcher reads the arena scorer from config, every tick
- **dashboard**: sweep round-2's orphaned stage-interior CSS
- **dashboard**: neutral-tone text uses --muted to clear the AA floor
- **dashboard**: stop the TermHint body inheriting uppercase from its host label
- **dashboard**: let the HTTP mount recover when the server comes back
- **dashboard**: clear the AA contrast floor for light-theme semantic tones
- **dashboard**: default the HTTP mount's MCP endpoint to same-origin
- **dashboard**: size the runner-liveness readout as a chip, not a numeric display
- **template**: the seeded prompts name the lane-dispatcher forms
- **dashboard**: re-point every client call at its lane dispatcher

### Refactor

- **core**: lift the per-topic record counts out of the status views
- **dashboard**: give the single crossing point its own module
- **dashboard**: split the process registry by id namespace
- **dashboard**: extract HandoffDispatchPanel and widen renderYouControl
- **dashboard**: rebuild Answer's react body and Fill's ingest/gate on the grammar
- **dashboard**: rebuild the Promote and Prove stages on the grammar
- **dashboard**: rebuild the Heal stage on the stage-body grammar
- **dashboard**: rebuild the Gate stage on the stage-body grammar
- **dashboard**: extract useOverlayDismiss, refactor InfoPopover onto it
- **dashboard**: extract the creation drawer from App.tsx

## v0.2.0 (2026-08-28)

### BREAKING CHANGE

- the dashboard's default landing changes — a bare URL
and a fresh MCP-App mount open on Home; every explicit deep link still
wins; unrecognized values degrade to Home.
- the dashboard tab set changes again — Ingest/Ask/Sources
tabs are replaced by the Learn, Answer and Fill lanes; legacy ?pane=
values redirect.
- the operator-tier flat MCP tools are removed; their
behaviour is reachable as lane-dispatcher actions, in the shape
`<lane> action=<verb>` (a verb owning its own `action` parameter takes it
as `<verb>_action`). There is no alias layer — an old flat name returns an
unknown-tool error. Full mapping:
[docs/reference.md — Operator verbs](docs/reference.md#operator-verbs-lane-actions-only).

### Feat

- M5 — Home. The process-swimlanes redesign is complete
- **dashboard**: Home — the cross-topic attention inbox is the front door
- M4 — Learn, Answer and Fill are drawn; the handoff stage makes client-as-brain visible
- **dashboard**: the last tool-shaped panes dissolve — the process lanes are the surface
- **dashboard**: Learn, Answer and Fill drawn; the handoff stage makes client-as-brain visible
- M3 — Improve and Tend are drawn; the tool-shaped dashboard dissolves
- **dashboard**: the tool-shaped panes dissolve — Improve and Tend are the surface
- **dashboard**: lanes wired into the app; drift joins Tend; the removal net is armed
- **dashboard**: ImproveLane assembles the six stages; the armed-confirm affordance becomes one component
- **dashboard**: the Tend checklist lane and all six Improve stage components
- **dashboard**: the generic lane rail — pure state module and render component
- **dashboard**: M3 entry gates — DOM test environment, characterization net, type-orphan gate
- M2 — the read-side lane projections
- **cli,docs**: the CLI rail and the M2 documentation pass
- **status**: M2's read-side lane projections — attention view, lanes block, Fill session watch, lane deep-links
- the process-swimlanes surface — six lanes declared once, projected onto every entry point
- **cli,docs**: lane-grouped --help with examples first, and the docs speak the nested forms
- **surface**: deprecation affordances, Fill's human gap transition, lane-grouped slash and CLI surfaces, and the collapsed description corpus
- **mcp**: remove the operator flat tools — the surface is 21 registrations
- **mcp**: the additive half of the lane surface — predicates, TS mirror, served declaration, six lane dispatchers
- **core**: declare the process model once — six lanes, stages, LANE_MEMBERSHIP
- **telemetry**: a tested summarizer and fixed thresholds for the baseline window
- **verify**: resolve every published tool name against the registry
- **verify**: gate the published surface against the code that publishes it
- **telemetry**: every registered tool is measured, and the outcome is the real one
- **swimlanes**: M0 steps 1-10 — test scaffolding, the two-phase primitive, gap lifecycle, telemetry sink
- **loop**: make an unreachable baseline visible and escapable
- **evals**: narrate a running eval to the terminal and the pane
- **make**: targets for the whole runtime, and a from-scratch KB guide
- **dashboard**: raise the deadline for LLM calls, add topic creation
- run gap-fill discovery from Claude and the dashboard
- show open gaps in the dashboard, not just suggestions
- let clients read the gap queue, not just write to it

### Fix

- **telemetry**: two defects found reviewing the batch F interceptor
- **adr**: give the tiered-lane draft the reciprocal its re-affirmation needs
- **state**: give the four ADR drafts the timestamp the finalizer requires
- **loop**: a conflicted gate merge aborts instead of stranding the vault
- **evals**: a truncated judge sample no longer discards the eval run
- **loop**: reopen the path back through the gate after a refusal
- **evals**: a probe-set baseline no longer breaks the topic's next eval
- **evals**: log the actionable fix, and correct the thread-count docstrings
- **loop**: discard the eval clone when a loop cycle ends
- cap mcp below 2.0, which removed mcp.server.fastmcp
- make a forced eval possible, visible, and traceable
- let the human freeze a golden set below the floor

### Refactor

- **dashboard**: the hub files split by lane — td-057 resolved, the ratchet exemption list is empty

## v0.1.0 (2026-08-06)

### Feat

- cut releases with commitizen, bumped only in CI
- widen the ADR gate to the defects that walked past it
- render the architecture diagrams and keep them in step
- gate the architecture docs, which were the only ungated pair
- add knotica desktop install/status for maintaining the Desktop entry
- surface the supersession cause in the drift UI and document notes
- make the ADR finalize hooks install themselves
- distinguish a superseded page from a reworded one in the drift queue
- stamp note-derived trainset promotions on the note
- expose families on the search tool
- search notes behind a families selector
- recover an anchor whose quote is not a whole sentence
- add the dashboard drift queue and promote dialog
- type the dashboard client for the five new notes actions
- wire the notes promote and archive actions
- wire the notes reanchor and detach actions
- wire the notes drift review queue
- add the read-only notes reconciliation pass
- surface capture's ambiguous matches as structured alternatives
- add the note promote bridge to trainset and gaps
- add reanchor, detach and archive for notes
- give note anchors an append-only history
- bucket fuzzy anchors as drift across every consumer
- run the notes resolution ladder to rung 9
- add notes fuzzy-matching foundations
- personal notes overlay — Phase 1 (capture and page-level anchoring)
- add the dashboard Notes pane
- report note counts in wiki status and the session nudge
- expose notes over MCP and prove they cannot move a score
- capture a personal note against the page that provoked it
- list and read personal notes with live anchor projections
- resolve a note anchor onto the current page text
- add the personal-note document format
- add vault folder families
- add /knotica:create + prompt-for-params guidance on vault create
- dashboard KB controls — switch, create, credential status
- add vault action=create to scaffold + register a new KB
- mark the default vault in the dashboard switcher
- add /knotica:headless opt-in + document the levers
- add /knotica:use command for KB switching
- surface the active KB in nudge + server instructions
- thread optional vault= through core read/write tools
- add vault dispatcher for KB inspect and switch
- live per-question eval error visibility in dashboard
- configurable eval cadence, per-task model config, billed-trigger gate
- knotica service CLI — install/uninstall/status
- uniform decision envelopes; dashboard refusedCount single-sourced
- loop-watcher service lifecycle module
- SessionStart topic-seed and attention nudge
- read/offer precondition guards on mutating tool descriptions
- symptom-based routing skill + slimmed server instructions
- register operator dispatchers with additive-alias migration
- datasets/arena/golden/vault_health dispatchers (unregistered)
- loop/branches/compile operator dispatchers (unregistered)
- wiki_status view=scope — cheap routing scope-check
- complete INVALID_ARGUMENT sweep across remaining sites
- INVALID_ARGUMENT error code for argument validation
- discover_on_regression defaults from discovery-key presence
- source-candidate gate — kind-by-branch-name, quarantine, no-arena, post-merge grower
- dashboard gate-outcome surfacing — refused badge + per-suggestion refusal note
- refused_awaiting_rework count in wiki_status.suggestions
- MCP source_ingest_open/source_ingest_submit — the client-facing ingest session
- additive candidate arg — worktree-scoped store_source/write_page
- source_ingest session lifecycle — open/resume/publish/abandon on vault worktrees
- worktree-aware VaultTransaction + page-subset grower filters
- P4 foundations — VaultVcs worktree primitives, SuggestionRecord.gate_outcome, SUGGESTION_NOT_APPROVED
- retracted-gap channel, reported_reason, gap counts, origin badge
- added-id gap classification + NL-reported gaps with origin provenance
- dashboard Sources pane + P3 documentation
- suggestion-queue surfaces — MCP tools, wiki_status block, CLI, loop batch
- suggestion queue substrate — SuggestionRecord + core/gapfill leaf
- .env fallback for discovery API keys; close remaining verifier WARNs
- wire gap classifier into the loop's regression path
- you.com adapter, OpenAlex enricher, DiscoveryService facade
- discovery provider protocol, http wrapper, reputability scorer, config
- gap classifier core — GapRecord + ordered four-way fault cascade
- discovery package skeleton — frozen SourceCandidate schema + SEARCH_API_ERROR
- emit manifest schema v2 with per-question ids, traces, held_out_delta
- forward retrieval trace through BaselineProgram.forward
- capture per-question ids and retrieval traces in eval producers
- **dashboard**: loop control plane — panes, policy controls, live progress
- **mcp**: full tool surface for the loop control plane
- **datasets**: page-grounded trainset cold-start
- **evals**: parallel per-question scoring with progress seams
- **loop**: autonomous watcher — observe, gate, heal, policy, debounce
- **core**: Phase 3a engine — compile pipeline, query engine, arena, scoreboard
- expand dashboard MCP app with Vault, Ingest, Golden, and doctor repair
- serve dashboard over stateless MCP HTTP
- add single-file loop dashboard workspace
- add loop runner spine for watch-eval-gate-merge
- persist loop-state and derive wiki_status gate from baseline
- add wiki_status and metrics_read MCP tools for the loop dashboard
- **evals**: cache runner completions in the per-run response cache
- **scripts**: char-precise quote jumps via Advanced URI offset
- **evals**: capture verbatim support quotes with line spans in bootstrap
- **scripts**: citation deep links + provenance quote display in review UI
- **scripts**: verify pages_used via Obsidian deep links in review UI
- **scripts**: local web UI for golden-set staging review
- **evals**: typed envelope for live LLM transport failures
- **evals**: prefer subscription OAuth token, noisy API-key fallback
- **cli**: add knotica eval subcommand
- **evals**: add golden-set bootstrap and freeze
- **evals**: export run_eval package seam
- **evals**: add eval harness orchestrator
- **evals**: add harness config + spend ceilings
- **evals**: export curated package seam
- **evals**: add DSPy program adapter
- **evals**: add golden-set devset builder
- **evals**: add triple-consumer scoring seam
- **evals**: add baseline query runner
- **evals**: add response cache + LLM judge
- **evals**: add citation + scalar scoring core
- **evals**: add LLMClient DI seam
- **evals**: add anthropic+dspy dependency group
- **vcs**: add clone_to for frozen-corpus eval
- add native OKF compatibility, guillotine vault integration, and PDF reflow
- **guillotine**: add Memory Guillotine claim audit, scoring, and reversible patches
- **prompts**: spine section map uses clickable wikilinks to section chunks
- **prompts**: ingest authoring quality — markdown-clean chunks, no hard-wrap pages, clickable source-wikilink citations
- **lint**: source-citation integrity check — pages may cite only stored sources (new doctor 'citations' row)
- **prompts**: section-chunked storage for long papers — store every cited section, never outrun the evidence
- **prompts**: large-paper ingest strategy — plan-first, leaves-before-linkers, resumable per-op checkpoints, concise pages
- **prompts**: ingest step 3 requires full-text extraction + completeness self-check (server instructions echo it)
- **mcp**: read_protocol tool + server instructions so NL requests complete multi-step ops
- **plugin**: SessionStart pre-warm + nudge hooks
- **plugin**: eight /knotica:* command aliases
- **plugin**: wiki-maintenance skill
- **plugin**: manifest + marketplace + .mcp.json
- **cli**: init setup wizard (Step 40)
- **cli**: doctor + status deterministic checks (Step 38)
- **cli**: migrate three-way schema migration (Step 42)
- **cli**: dispatch protocol + mcp + prompt commands (Step 36)
- **mcp**: resources + prompts with lazy bodies (Step 34)
- **mcp**: mutating tools (Step 32)
- **mcp**: MCP server skeleton + read tools (Step 30)
- **operations**: create_topic + curate_example wave-2 operations
- **operations**: write_page + store_source wave-1 operations
- **core**: add VaultTransaction one-writer seam

### Fix

- stop config.toml writes that make the file unparseable
- describe what the guillotine command actually does
- request the evals extra by name instead of hand-listing packages
- resolve discovery credentials through the same chain as the probe
- accept the shared CLI flags in either position
- round-trip nested sub-tables when writing config.toml
- reach every loop knob from the shared factory
- give the write path the error mapper the read path already had
- let a heading end the block before it, not only the one after
- put the calibration log back inside its own schema
- stop charging the vault a commit for an attempt that records nothing
- enforce the single-writer rule instead of approximating it
- gate mixed-stage ADR pointers and backfill resolution provenance
- gate ADR frontmatter validity and cross-reference reciprocity
- close the fail-open in the topology check and cover the runner
- reject an unfinished model response at the LLM boundary
- pace a blocked eval by the error's own retryable contract
- make AC-10 falsifiable and stop reanchor rewriting hand-authored bytes
- unbreak the td-017 ledger row's column count
- bind the family selection into the search cursor
- reject a promotion with no question or answer
- show the promote dialog instead of appending it below the fold
- report no overlap when nothing was comparable
- stop candidate windows extending back to document start
- stop page-less anchors sharing a supersession group
- okf repair stops inventing a topic and committing on every run
- okf repair no longer rewrites or commits uncommitted work
- stop notes leaking into scored and knowledge-base surfaces
- clear the remaining verification warnings
- close the verification findings for the notes layer
- tell an unanchored note apart from an orphaned one
- never let an unreadable page claim break a note capture
- route okf repair through the vault transaction
- lock mypy and make the type gate green in both environments
- reconcile the dashboard topic when the active vault changes
- scaffold bare vaults everywhere — never seed the demo topic
- add mcpServers wrapper to plugin .mcp.json
- replace raw-count search scoring with BM25 ranking
- init --desktop resolves the wrong MCP source when tool-installed
- heal crash remnants on every vault-lock acquire; daemon logging
- daemon bootstraps environment from config-dir .env
- resolve verification findings
- widen vault lock to the full git-mutation span
- retry transient git races beyond index.lock
- list_branch_tips glob must cross path segments
- correct you.com adapter to the live-verified /v1/search wire shape
- raise eval LLM in-process retry budget; close verifier findings
- preserve streamable-HTTP lifespan for dashboard MCP mount
- polish dashboard chart baseline and stage-card CSS
- **evals**: treat filesystem-impossible citations as unresolved
- **evals**: raise default token ceiling to fit a real topic run
- **evals**: schema-enforce baseline runner JSON via structured outputs
- **scripts**: resolve pages_used topic-relative in review UI
- **evals**: restore complete method orphaned from AnthropicClient
- **evals**: surface the eval clone root and wire failure_score
- **tests**: guard eval-group imports so plain pytest collects
- accept vault-relative source paths in read_page
- **search**: exclude hidden paths via explicit rg glob (ripgrep 15+ --no-ignore no longer skips them)
- **plugin**: non-blocking cold-start hook (config nudge via stdlib, pre-warm always backgrounds)
- **mcp**: error envelopes set isError=True (Step 30 review)

### Refactor

- drop the guillotine's unreachable role and dead risk check
- give source identity one declaration instead of three
- finish the topic-identity consolidation
- give topic identity and JSONL reading one declaration each
- model the four undocumented packages in DESIGN.md §3
- declare the eval dependencies once, as the evals extra
- split notes actions on the read/mutate seam
- split the notes dispatcher into router and actions
- reach zero mypy errors under strict
- type-check cli/ under mypy strict
- type-check core/ and most of evals/ under mypy strict
- extract config.toml writer to core.config_write
- extract loop.py's arena-race, factory, and candidate-gate clusters
- dashboard toolClient calls dispatchers directly
- build_loop_runner factory unifies runner construction
- unify arena race-and-resolve choreography
- shared best_effort failure-isolation primitive
- extract branch_namespaces as single source of truth
- guillotine to verdict+report+gap-filing; fair gap prioritization
- **core**: integration checkpoint 2 — template locator + config-atomicity + §6 sweep

### Perf

- cut drift-queue read cost by removing three git redundancies
- read one note without paying for every note in the topic
