# Knotica

AI-maintained, compounding knowledge wiki (Karpathy's llm-wiki pattern) living in an Obsidian vault, with per-topic self-improving agents: DSPy optimizes operation prompts (inner loop), SIA evolves schemas/structure (outer loop). No model weights are ever modified — the system's "weights" are its schemas and prompts.

## Canonical design

**`docs/PRE_PLAN.md` is the authoritative design document.** Read it before any architectural or implementation work. Key invariants (do not violate without updating the pre-plan first):

- **Client-as-brain**: the MCP server exposes deterministic tools only; the MCP client's LLM does all cognitive work (ingest/query/lint) guided by vault schemas. Server-side LLM access exists only for headless loops (Phase 3a+).
- **Stateless server**: no session state — the vault (git) and `~/.config/knotica/config.toml` are the only state, resolved per tool call. Topic is always an explicit tool argument.
- **The vault is data, this repo is code**: the wiki lives at `~/dev/data/knotica` (separate private git repo). Never hardcode vault paths; all vault access goes through the `VaultStore` abstraction.
- **One git commit per mutating vault operation** (audit trail + rollback); mutating ops are flock-guarded.
- **Loops always work on a git clone, never the live vault**; results return as branches for human review.
- **Single source of truth for prompts**: operation prompts live in the vault (`.knotica/prompts/`, root defaults + earned topic overrides) and are simultaneously the MCP-prompt UX surface and the DSPy/SIA-evolvable substrate.

## Project conventions

- Python 3.12+, **uv-managed** (`uv sync`, `uv run`); src layout under `src/knotica/`.
- Dual-role repo: Python package + Claude plugin marketplace (`.claude-plugin/`, `commands/`, `hooks/`, `skills/`, `.mcp.json`).
- MCP server built on FastMCP; CLI entry point `knotica` (subcommands: `init`, `mcp`, `doctor`, `status`, `migrate`, `eval`, `datasets`, `compile`, `loop`, `gapfill`, `service`).
- `knotica loop --topic <t>` is the autonomous self-improvement watcher: observes default-branch content changes (eval on a clone, 4 parallel scoring threads by default; debounced — holds during active ingests and until HEAD is stable), gates `loop/c/*` candidates, heals regressions via the arena, and heartbeats to `.knotica/locks/`. Gate baseline policy is per-topic (`latest` tracks reality, `best` ratchets a high-water mark; instrument changes auto-refreeze); drive it via the `loop action=baseline_policy`/`loop action=rebaseline` dispatcher actions, the dashboard toggle, or CLI flags. Merged `loop/r/*` audit pointers auto-prune beyond the newest 5.
- Tests with pytest in `tests/`; run via `uv run pytest`.
- Build/tooling output to `/dev/null` or `tmp/` — never commit artifacts.

## Current status

Phases 0–4 are implemented locally (vault template, core/MCP/plugin, eval harness, DSPy compile,
dashboard MCP App, autonomous loop layer, gap-fill classifier, discovery, suggestion queue, and source-candidate gate).
**Consolidation (2026-07-21):** loop internals refactored for growth (`core/branch_namespaces`,
`core/best_effort`, unified arena-race core, `build_loop_runner` factory; vault mutation lock
widened to the full git-mutation span with crash self-heal and retryable `LOCK_BUSY`); tool
surface consolidated 49→30 via seven action-parameterized dispatchers (`loop`, `branches`,
`compile`, `datasets`, `arena`, `golden`, `vault_health`) plus mis-selection telemetry; the
26 deprecated flat-tool aliases the consolidation initially kept for a migration window were
removed outright (`dec-050` partially supersedes `dec-045` — no external MCP
consumers exist to migrate); conversational-routing layer added
(symptom-based `wiki-maintenance` skill, slimmed server instructions pointing at `read_protocol`,
read/offer guards on every mutating tool, cheap `wiki_status(view="scope")` check, SessionStart
topic-seed + needs-attention nudge); `discover_on_regression` defaults on when a discovery key is
present (offline installs unchanged); loop service lifecycle via `knotica service
install|uninstall|status` (launchd-verified, systemd untested; one supervised process, topics
resolved from config each cycle); decision-envelope fields unified across the three human gates. 
The **gap-fill pipeline** (P1–P4) diagnoses regressions into four fault classes, discovers ranked 
sources for genuine knowledge gaps, surfaces a human-approval queue, and gates ingests onto isolated worktree candidate branches: `knotica gapfill discover` 
on-demand + optional loop-side batch, MCP tool `gap_report` for conversational gaps, `suggestions_read`/`suggestions_review`, 
dashboard **SourcesPane**, and P4 source-ingest tools (`source_ingest_open`/`source_ingest_submit`). Gaps have three origins: `measured` (loop regressions), `reported` (client-as-brain via `gap_report`), 
and `retracted` (guillotine disputes). The loop watches the default branch, auto-freezes the first 
observation as the gate baseline, gates `loop/c/*` candidates (distinguishing source from prompt candidates by branch name), and heals prompt regressions via 
the arena — source-candidate pass auto-marks suggestions ingested and triggers a page-subset dataset upgrade; refuse quarantines to `loop/x/*` with bounded per-question diff, never arena. State surfaced through `wiki_status` (runner liveness, per-question eval progress, 
LLM availability, suggestion counts, refused-awaiting-rework). Guillotine refactored to verdict + risk report + triage score 
+ gap filing only; content rewriting flows through the gap→suggestion→approved-ingest path where 
the client-as-brain writes grounded prose and drives the candidate-scoped ingest protocol. Trainset cold-start is data-driven (`knotica datasets bootstrap-train` 
/ `datasets action=bootstrap_train` dispatcher action: QA synthesized from the topic's own pages, `source: seed_train`; 
curated examples displace seeds in compile demo selection). No demo content remains in code: no 
hardcoded questions/prompt appendices, no fabricated offline compile scores (typed error without 
credentials), MIPRO fallbacks recorded on the artifact (`optimizer`/`fallback_reason`). Coherence-audited 
spine with cross-spine integration test. End-user Desktop install walkthrough: `docs/CLAUDE_DESKTOP.md`; 
developer architecture guide: `docs/architecture.md`. See `docs/PRE_PLAN.md` § Phases & execution. 
Remote (Railway) remains gated on local smoothness.
