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
- MCP server built on FastMCP; CLI entry point `knotica` (subcommands: `init`, `mcp`, `doctor`, `status`, `migrate`; later `eval`, `compile`).
- Tests with pytest in `tests/`; run via `uv run pytest`.
- Build/tooling output to `/dev/null` or `tmp/` — never commit artifacts.

## Current status

Pre-implementation. Phases 0–1 (vault template + core/MCP/plugin) run as a Standard-tier pipeline; see `docs/PRE_PLAN.md` § Phases & execution. Phases 0–3 are local-only; remote (Railway) is gated on local smoothness.
