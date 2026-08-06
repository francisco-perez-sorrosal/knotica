# Configuration reference

Every setting knotica reads, where it lives, what it defaults to, and what happens when you leave
it unset. Config lives in exactly one file; credentials never do.

## Contents

- [Where config.toml lives](#where-configtoml-lives)
- [Full schema](#full-schema)
- [Vaults: default and named](#vaults-default-and-named)
- [\[models\]: per-task model selection](#models-per-task-model-selection)
- [\[loop\]: eval cadence](#loop-eval-cadence)
- [\[notes\]: resolution-ladder thresholds](#notes-resolution-ladder-thresholds)
- [\[gapfill\]: the discovery hook](#gapfill-the-discovery-hook)
- [Environment variables](#environment-variables)
- [Security policy: credentials](#security-policy-credentials)

## Where config.toml lives

Default path: `~/.config/knotica/config.toml`. Override it with the `KNOTICA_CONFIG` environment
variable, or pass a path explicitly to any tool that accepts one. Precedence: explicit argument >
`KNOTICA_CONFIG` > default.

The file is **never cached** — every tool call re-reads it fresh, so an edit takes effect
immediately, no restart needed (the stateless-server contract; see
[architecture.md](architecture.md)). Every table below is independently optional: a missing file
or table is not an error, it just resolves to defaults.

All mutation goes through one writer (`core/config_write.py`), additive and atomic — a
same-directory temp file plus `os.replace`, so a torn write can never truncate the file or drop a
sibling vault. Every section round-trips through a tool write (`knotica init`,
`vault action=use/add/create`, a `loop action=cadence` write): top-level keys, `[vaults.<name>]`,
flat tables (`[loop]`, `[models]`, `[notes]`, `[gapfill]`), and nested sub-tables to arbitrary
depth, `[gapfill.search]` included. Hand-edit any table at any time, in any order — a tool write
preserves every section it does not itself own.

## Full schema

```toml
schema_version = 1
default_vault = "main"

[vaults.main]
path = "~/dev/data/knotica"

[loop]
eval_min_interval_hours = 24.0
eval_window = "22:00-02:00"
eval_num_threads = 4

[models]
worker = "claude-haiku-4-5-20251001"
judge = "claude-sonnet-5"
query = "claude-sonnet-5"

[notes]
guess_threshold = 0.75
complete_orphan_threshold = 0.35

[gapfill]
discover_on_regression = true
max_gaps = 5

[gapfill.search]
provider = "youcom"
mailto = "you@example.com"
```

| Table / key | Type | Default | Bad value |
|---|---|---|---|
| `schema_version` | int | `1` | — (stamped by the writer, not user-set) |
| `default_vault` | string | none | missing/empty → vault resolves as unconfigured |
| `[vaults.<name>].path` | string (`~`/`$ENV` expanded at read time) | — | missing entry, missing dir, no `.git`, or no root `SCHEMA.md` → vault resolves as unconfigured or not-ready |
| `[loop].eval_min_interval_hours` | float | `0.0` | non-numeric or negative → rejected, names the fix |
| `[loop].eval_window` | string `"HH:MM-HH:MM"` | none | wrong type or unparseable range → rejected |
| `[loop].eval_num_threads` | int | `4` | non-int, or outside `1`–`8` → rejected |
| `[models].worker` | string | `claude-haiku-4-5-20251001` | non-string silently falls back to default |
| `[models].judge` | string | `claude-sonnet-5` | same |
| `[models].query` | string | `claude-sonnet-5` | same |
| `[notes].guess_threshold` | float `[0.0, 1.0]` | `0.75` | non-numeric or out of range → rejected |
| `[notes].complete_orphan_threshold` | float `[0.0, 1.0]` | `0.35` | same, plus must be strictly less than `guess_threshold` (equal is also rejected) |
| `[gapfill].discover_on_regression` | bool | conditional — see [below](#gapfill-the-discovery-hook) | non-bool → rejected |
| `[gapfill].max_gaps` | positive int | `5` | non-int or `< 1` → rejected |
| `[gapfill.search].provider` | string or list of strings | `"youcom"` | wrong type or unrecognized name → rejected (recognized: `youcom`, `exa`) |
| `[gapfill.search].mailto` | string | none | non-string → rejected |

A rejected value raises a typed, non-fatal error naming the exact key and a fix — never a silent
fallback and never a stack trace. The one exception is `[models]`: a non-string value there falls
back to the packaged default rather than raising, since a model snapshot name is low-stakes to get
wrong.

## Vaults: default and named

Several vaults may be configured under `[vaults.<name>]`; exactly one is active per call.
Resolution order:

1. An explicit vault name — the CLI's `--vault NAME`, or the MCP tools' `vault` parameter — wins
   over everything.
2. Otherwise, `default_vault` from `config.toml`.
3. If neither resolves to a `[vaults.<name>]` entry, the call fails with a not-configured error
   naming the fix.

The resolved path is expanded (`~` and `$ENV` vars) at read time, then checked: it must be a
directory, contain a `.git` repo, and carry a root `SCHEMA.md`. A path that fails any of those
checks resolves as configured-but-not-ready, distinct from not-configured — `knotica doctor`
reports which of the two you have.

Switching the default, or adding a named vault, is a mutation through the `vault` MCP dispatcher
or `knotica init`, not a hand edit. See [reference.md](reference.md) for the full `vault` action
set, and [install.md](install.md) for `knotica init`'s vault-scaffolding flow.

## `[models]`: per-task model selection

Three independent snapshot overrides, each optional:

| Key | Governs | Folds into `harness_version`? |
|---|---|---|
| `worker` | the eval harness's baseline-answer synthesizer | yes |
| `judge` | the eval harness's reference-based grader | yes |
| `query` | the MCP `query` tool's answer synthesis | no |

> [!IMPORTANT]
> Changing `worker` or `judge` rotates `harness_version`, which the loop's own logic treats as an
> instrument change — the next observation **re-freezes the baseline** rather than comparing
> across instruments. Changing `query` rotates nothing and re-freezes nothing: it is excluded from
> the harness fingerprint by design, since it drives a different code path.

**`worker` and `judge` reach every eval.** They resolve inside the one shared `harness_evaluate`
callable, which is what `knotica eval`, the `knotica loop` watcher, the OS-managed daemon, the MCP
`loop action=run_once` and `loop action=run_eval` paths, and the ingest candidate gate all evaluate
through — so an unattended background eval scores on the same instruments as a foreground one.
`query` is separate: it drives answer synthesis rather than eval, and reaches the MCP `query` tool
and nothing else.

One transition consequence on an install that already carries a `[models]` table: the first
loop-driven eval after the background loop began honoring it rotates `harness_version` and
re-freezes the baseline, once. That is the designed instrument-change response the callout above
describes, not a regression. An install with no `[models]` table sees nothing rotate and nothing
re-freeze.

CLI override precedence for `knotica eval`: `--worker-snapshot` / `--judge-snapshot` flags >
`[models]` > packaged default. `query` has no CLI surface at all; it is config-only and reachable
only through the MCP `query` tool.

## `[loop]`: eval cadence

Governs how often the watcher (`knotica loop`, and the OS-managed daemon) is willing to spend on
a fresh eval. None of these three keys apply to the candidate gate (`loop/c/*` branches) — that
path is always eager, cadence never holds it.

| Key | Default | What it actually does |
|---|---|---|
| `eval_min_interval_hours` | `0.0` | Minimum hours since the last eval **started** before the watcher will start another. `0` means no throttle — every eligible tick evaluates. Reaches the watcher, the daemon, and the MCP `run_once` observe leg (`run_eval` bypasses it by forcing the observation). |
| `eval_window` | none | The local-clock window an observation eval is permitted to **start** in, as `"HH:MM-HH:MM"` (midnight wrap supported, e.g. `"22:00-02:00"`). Unset means no window restriction. Reaches the watcher, the daemon, and the MCP `run_once` observe leg (`run_eval` bypasses it by forcing the observation). |
| `eval_num_threads` | `4` | Default `num_threads` for the MCP `loop action=run_eval` billed call only — `run_once` does not read it. Bounded `1`–`8`. |

> [!NOTE]
> `eval_num_threads` does not reach the foreground watcher or the daemon. `knotica loop` uses its
> own `--eval-threads` flag (unset → the harness default of `4`); the daemon has no thread flag at
> all and is pinned to the harness default of `4`. This key only sets the default thread count for
> the MCP `loop action=run_eval` billed action.

**Worked example** — cap the watcher to at most one eval per day:

```toml
[loop]
eval_min_interval_hours = 24.0
```

With this set, a tick that finds new content but started an eval less than 24 hours ago is held
with the reason `"cadence held: <elapsed>h since last eval start < 24h interval"`, and no eval
runs. The next tick after 24 hours have elapsed evaluates normally. Adding `eval_window` composes
with it — both constraints must pass, so an observation eval starts only once the interval has
elapsed **and** the clock sits inside the window.

Read or write this table without hand-editing the file via MCP `loop action=cadence` — called
with no parameters it reads the resolved table; called with any of the three keys it additively
merges them in, leaving every other section of `config.toml` untouched.

## `[notes]`: resolution-ladder thresholds

Governs the confidence bands notes' fuzzy-anchor resolution uses when deciding whether a matched
anchor is exact, a guess worth surfacing, or too weak to offer at all.

| Key | Default | Meaning |
|---|---|---|
| `guess_threshold` | `0.75` | Score at or above which a resolved candidate is reported as a confident guess. |
| `complete_orphan_threshold` | `0.35` | Score below which an orphaned anchor gets no guess offered at all. |

`complete_orphan_threshold` must sit strictly below `guess_threshold` — equal or inverted values
empty the ladder's graded-recovery band and are rejected outright, not silently clamped.

## `[gapfill]`: the discovery hook

Governs the loop's opt-in behavior when an observation regresses to a knowledge cause (a genuine
gap or content dilution) rather than a prompt-fixable fault.

| Key | Default | Meaning |
|---|---|---|
| `discover_on_regression` | conditional — see below | Whether the loop drains open gap records into staged discovery suggestions after a knowledge-cause regression. |
| `max_gaps` | `5` | Maximum gap records drained per regression event (fixed budget, never unbounded). |

`discover_on_regression`'s default is conditional, not a fixed boolean:

- An explicit `false` always wins — stays off no matter what credentials exist.
- An explicit `true` is honored only if a discovery-provider credential resolves (see
  [Environment variables](#environment-variables)); otherwise it fails closed to `false`, logged,
  rather than silently trusting a key that isn't there.
- Left unset, it resolves to `true` if a discovery key is present and valid, `false` otherwise —
  so a keyless install behaves exactly as it did before this key existed.

`[gapfill.search]` names the provider chain (a single provider name or an ordered fallback list;
recognized names are `youcom` and `exa`) and an optional `mailto` for the OpenAlex polite pool.
This table never holds a credential — see the discovery pipeline in
[gap-fill.md](gap-fill.md) for how the chain is used.

## Environment variables

| Variable | Purpose | Absent behavior |
|---|---|---|
| `KNOTICA_CONFIG` | Override the `config.toml` location | Falls back to `~/.config/knotica/config.toml` |
| `KNOTICA_DESKTOP_CONFIG` | Override the Claude Desktop config file location (test/power-user hook) | Falls back to the macOS `claude_desktop_config.json` path |
| `KNOTICA_MCP_FROM` | Override the `--from` source used when launching the MCP server via `uvx`/`uv run` | Falls back to source-checkout auto-detection, then the published package name |
| `KNOTICA_YOUCOM_API_KEY` | Search-provider credential (you.com) | Provider unusable; discovery calls raise a not-configured error naming this variable |
| `KNOTICA_EXA_API_KEY` | Search-provider credential (exa) — the config-key mapping is recognized, though the exa adapter itself is not currently shipped | Same as above |
| `CLAUDE_CODE_OAUTH_TOKEN` | Preferred eval-LLM credential — Claude subscription OAuth, no metered spend | Falls back to `ANTHROPIC_API_KEY` with a loud fallback warning; if both are absent, a not-configured error names both |
| `ANTHROPIC_API_KEY` | Fallback eval-LLM credential — metered API spend | See above |
| `COLUMNS` | Terminal width for `knotica status` rendering (ignored under `--wide`) | Falls back to a packaged default; a non-integer value also falls back |
| `NO_COLOR` | Presence disables ANSI color in CLI output | Color follows TTY detection |
| `TERM` | `TERM=dumb` disables ANSI color | Color follows TTY detection |

A handful of call sites read the whole environment as a block rather than one named variable —
the CLI's color-policy snapshot, and the base environment for every `git` subprocess call (which
may overlay `GIT_OPTIONAL_LOCKS=0`). Neither is a knob you set directly.

There is no environment variable that turns headless on. If a credential is set but `anthropic` and
`dspy` are missing, `vault action=status` says so and tells you to reinstall requesting the `evals`
extra — which is the only way to enable it. See [install](install.md).

## Security policy: credentials

Every credential above — the two search-provider keys and the two LLM credentials — is read from
**the environment only**. Never `config.toml`, never the vault, never a log line, never an error
message.

Two independent `.env` fallback readers exist, each scoped narrowly:

- **Search-provider keys** fall back through `./.env` (current directory), then
  `~/.config/knotica/.env`, first hit wins — read at the moment a discovery call needs the key,
  before any HTTP client is built.
- **The OS-managed daemon** (`knotica service install`) loads `~/.config/knotica/.env` into its
  process environment at startup, for any variable not already set. This is the only path by
  which `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` can come from a file — launchd/systemd
  start the daemon with a near-empty environment, so this bootstrap exists specifically for
  unattended runs. A foreground `knotica loop` or an MCP tool call never consults this file for
  LLM credentials; only the daemon does.

Both readers use the same minimal grammar: blank lines and `#` comments are skipped, one optional
`export ` prefix is stripped, and surrounding quotes are stripped. Values are never logged by
either reader.
