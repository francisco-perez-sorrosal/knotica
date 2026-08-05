# Knotica

AI-maintained, compounding knowledge wiki (Karpathy's llm-wiki pattern) living in an Obsidian vault, with per-topic self-improving agents: DSPy optimizes operation prompts (inner loop), SIA evolves schemas/structure (outer loop). No model weights are ever modified — the system's "weights" are its schemas and prompts.

## Canonical design

**`docs/PRE_PLAN.md` is the authoritative design document.** Read it before any architectural or implementation work. Key invariants (do not violate without updating the pre-plan first):

- **Client-as-brain**: the MCP server exposes deterministic tools only; the MCP client's LLM does all cognitive work (ingest/query/lint) guided by vault schemas. Server-side LLM access exists only for headless loops (Phase 3a+).
- **Stateless server**: no session state — the vault (git) and `~/.config/knotica/config.toml` are the only state, resolved per tool call. Topic is always an explicit tool argument. (The loop *daemon* also keeps gitignored runtime markers under `.knotica/locks/`; why that does not widen this is scoped once in [`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) § Settled design decisions.)
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

Phases 0–4 ship locally: vault template, core/MCP/plugin, eval harness, DSPy compile, dashboard
MCP App, the autonomous loop layer, and the gap-fill pipeline (P1–P4: diagnose → discover →
approve → gated ingest). Phase 5 (remote/Railway) stays gated on local smoothness.

What each phase delivered, the 2026-07-21 consolidation, and the gap-fill spine's mechanics:
[`docs/PRE_PLAN.md`](docs/PRE_PLAN.md) § Phases & execution. Developer architecture guide:
[`docs/architecture.md`](docs/architecture.md). End-user Desktop install:
[`docs/CLAUDE_DESKTOP.md`](docs/CLAUDE_DESKTOP.md).

## Agent Pipeline

This project follows Praxion's tier-driven agent pipeline (Direct → Lightweight → Standard → Full, plus exploratory Spike) under the **Understand, Plan, Verify** methodology. Ephemeral pipeline artifacts live in `.ai-work/<task-slug>/` (deleted after use); permanent decisions and design docs live in `.ai-state/` (committed to git).

When Praxion's assistant tooling is active, its agent coordination protocol rule and `software-planning` skill carry the full agent roster, delegation checklists, and pipeline-branch handling. Always include expected deliverables when delegating to an agent.

Human-readable process overview: [Praxion documentation](https://github.com/francisco-perez-sorrosal/praxion#readme).

## Behavioral Contract

Four non-negotiable behaviors for any agent (including Claude itself) writing, planning, or reviewing code:

- **Surface Assumptions** — state your interpretation up front and surface gap-filling assumptions as you make them; a plausible default never *feels* like ambiguity. Pause when one is load-bearing and hard to reverse.
- **Register Objection** — when a request violates scope, structure, or evidence, state the conflict with a reason before complying or declining.
- **Stay Surgical** — touch only what the change requires; if scope grew, stop and re-scope instead of expanding silently.
- **Simplicity First** — prefer the smallest solution that meets the behavior; every line, file, or dependency must earn its place.

Self-test: did I state my assumptions, flag conflicts with reasons, stay in scope, and pick the simplest path?

## Compaction Guidance

When this conversation compacts, always preserve: the active pipeline stage and task slug, the current WIP step number and status, acceptance criteria from the systems plan, and the list of files modified in the current step. The Praxion `PreCompact` hook snapshots in-flight pipeline documents to `.ai-work/PIPELINE_STATE.md` (one consolidated snapshot at the `.ai-work/` root, with a per-task-slug section for each active pipeline) — re-read that file after compaction to restore orientation.

## Praxion Process

Apply Praxion's tier-driven pipeline for non-trivial work. Use the tier selector from `rules/swe/swe-agent-coordination-protocol.md`: Direct (single-file fix/typo) or Lightweight (2–3 files) may skip the full pipeline; Standard or Full tier work requires researcher → systems-architect → implementation-planner → implementer + test-engineer → verifier.

**Rule-inheritance corollary.** When delegating to any subagent — Praxion-native or host-native (Explore, Plan, general-purpose) — carry the behavioral contract into every delegation prompt. Host-native subagents do not load CLAUDE.md; the orchestrator is the only delivery path.

**Orchestrator obligation.** Every delegation prompt must name the task slug, expected deliverables, and the behavioral contract (Surface Assumptions · Register Objection · Stay Surgical · Simplicity First).

## Working in this project

This `CLAUDE.md` is the **index**; `docs/` and the skills it points to are the **library** — read the index, follow the links the task needs. When I correct you, propose a durable rule for review (a `CLAUDE.md` or rule edit, or a skill note) so the correction outlasts this session.

### Verification

`make verify` is the canonical chain, in order: topology check, ADR health, mypy, the full suite,
ruff. Run it before every commit and fix at each step before moving on — the two leading checks
are cheap and a failure there makes everything below it untrustworthy.

For the inner loop, `make test-groups` lists the test groups and `make test-group GROUP=<id>` runs
one — seconds against the full suite's ~5 minutes. Membership is derived from
[`.ai-state/TEST_TOPOLOGY.md`](.ai-state/TEST_TOPOLOGY.md), never restated elsewhere.

### Frequent operations

You'll most often be asked to:

- Add or change an MCP tool on one of the seven action-parameterized dispatchers (`loop`, `branches`, `compile`, `datasets`, `arena`, `golden`, `vault_health`)
- Work on the autonomous loop layer — gating, arena races, baseline policy, branch namespaces
- Extend the gap-fill pipeline (diagnosis → discovery → suggestion queue → candidate-gated ingest)
- Adjust vault-facing behavior via `VaultStore` — never hardcode vault paths
- Update the eval harness, DSPy compile artifacts, or golden datasets
