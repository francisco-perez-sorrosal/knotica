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
- MCP server built on FastMCP; CLI entry point `knotica` (subcommands: `init`, `mcp`, `doctor`, `status`, `migrate`, `eval`, `datasets`, `compile`, `loop`).
- `knotica loop --topic <t>` is the autonomous self-improvement watcher: observes default-branch content changes (eval on a clone, 4 parallel scoring threads by default; debounced — holds during active ingests and until HEAD is stable), gates `loop/c/*` candidates, heals regressions via the arena, and heartbeats to `.knotica/locks/`. Gate baseline policy is per-topic (`latest` tracks reality, `best` ratchets a high-water mark; instrument changes auto-refreeze); drive it via the `loop_baseline_policy`/`loop_rebaseline` tools, the dashboard toggle, or CLI flags. Merged `loop/r/*` audit pointers auto-prune beyond the newest 5.
- Tests with pytest in `tests/`; run via `uv run pytest`.
- Build/tooling output to `/dev/null` or `tmp/` — never commit artifacts.

## Current status

Phases 0–3a are implemented locally (vault template, core/MCP/plugin, eval harness, DSPy compile,
dashboard MCP App) **plus the autonomous loop layer**: `knotica loop` watches the default branch,
auto-freezes the first observation as the gate baseline, gates `loop/c/*` candidates, and heals
prompt regressions via the arena — all state surfaced through `wiki_status` (runner liveness,
per-question eval progress, LLM availability). Trainset cold-start is data-driven
(`knotica datasets bootstrap-train` / `datasets_bootstrap_train` tool: QA synthesized from the
topic's own pages, `source: seed_train`; curated examples displace seeds in compile demo
selection). No demo content remains in code: no hardcoded questions/prompt appendices, no
fabricated offline compile scores (typed error without credentials), MIPRO fallbacks recorded on
the artifact (`optimizer`/`fallback_reason`). End-user Desktop install walkthrough:
`docs/CLAUDE_DESKTOP.md`; developer architecture guide: `docs/architecture.md`.
See `docs/PRE_PLAN.md` § Phases & execution. Remote (Railway) remains gated on local smoothness.
