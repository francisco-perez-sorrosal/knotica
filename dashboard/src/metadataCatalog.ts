/** Tooltip catalog for Knotica vault metadata paths (pattern → title + purpose). */

export interface MetadataCatalogEntry {
  title: string;
  purpose: string;
}

/** Longest-prefix wins; patterns use ``*`` as a path segment wildcard. */
const CATALOG: Array<{ pattern: string; entry: MetadataCatalogEntry }> = [
  {
    pattern: "SCHEMA.md",
    entry: {
      title: "Root constitution",
      purpose:
        "Vault-wide schema overlay — entity types, link rules, and lint constraints inherited by every topic until a topic earns its own SCHEMA.md.",
    },
  },
  {
    pattern: "log.md",
    entry: {
      title: "Vault audit log",
      purpose:
        "Append-only journal of mutating operations (ingest, curate, lint, eval). One git commit per operation; last lint timestamp is read from here.",
    },
  },
  {
    pattern: ".knotica/prompts/query.md",
    entry: {
      title: "Query prompt (root default)",
      purpose:
        "Default operation prompt for answering questions from vault pages. MCP clients and headless evals resolve topic overrides when earned.",
    },
  },
  {
    pattern: ".knotica/prompts/ingest.md",
    entry: {
      title: "Ingest prompt (root default)",
      purpose:
        "Default instructions for turning raw sources into schema-conformant wiki pages during ingest.",
    },
  },
  {
    pattern: ".knotica/prompts/curate.md",
    entry: {
      title: "Curate prompt (root default)",
      purpose:
        "Default rubric for judging Ask answers before examples are appended to the trainset.",
    },
  },
  {
    pattern: ".knotica/prompts/lint.md",
    entry: {
      title: "Lint prompt (root default)",
      purpose:
        "Default guidance for semantic lint fixes (mechanical lint stays deterministic).",
    },
  },
  {
    pattern: ".knotica/prompts/*",
    entry: {
      title: "Operation prompt",
      purpose: "Root default prompt body for a Knotica operation — also the DSPy/SIA evolvable substrate.",
    },
  },
  {
    pattern: ".knotica/locks/vault.lock",
    entry: {
      title: "Vault flock",
      purpose:
        "Process-wide lock file guarding mutating vault operations so concurrent writers serialize safely.",
    },
  },
  {
    pattern: ".knotica/locks/*",
    entry: {
      title: "Lock file",
      purpose: "Coordination artifact under the hidden .knotica locks directory.",
    },
  },
  {
    pattern: ".knotica/ingest-activity.jsonl",
    entry: {
      title: "Ingest activity journal",
      purpose:
        "Append-only ingest run events for the dashboard Ingest pane (gitignored; local telemetry only).",
    },
  },
  {
    pattern: "*/SCHEMA.md",
    entry: {
      title: "Topic schema overlay",
      purpose:
        "Topic-specific schema extensions. Empty at creation; diverges from root only when the topic has earned structural changes.",
    },
  },
  {
    pattern: "*/.knotica/loop-state.json",
    entry: {
      title: "Loop runner state",
      purpose:
        "Gate baseline, coarse loop stage, candidate cursors, and last merge decision — the dashboard Loop/Arena exposure surface.",
    },
  },
  {
    pattern: "*/.knotica/compile-state.json",
    entry: {
      title: "Compile run state",
      purpose:
        "In-flight DSPy compile stage, trial counters, branch tip, and scalar before/after for the Compile pane.",
    },
  },
  {
    pattern: "*/.knotica/arena-state.json",
    entry: {
      title: "Arena race state",
      purpose:
        "Active prompt-variant arena race metadata when the loop gate fails and deterministic variants are compared.",
    },
  },
  {
    pattern: "*/.knotica/arena-history.jsonl",
    entry: {
      title: "Arena history",
      purpose: "Append-only log of completed arena races and promotion outcomes for scoreboard review.",
    },
  },
  {
    pattern: "*/.knotica/metrics.jsonl",
    entry: {
      title: "Eval metrics log",
      purpose:
        "One JSON line per eval generation — scalar, harness version, token cost, and component breakdown for trend charts.",
    },
  },
  {
    pattern: "*/.knotica/eval.toml",
    entry: {
      title: "Eval budget config",
      purpose: "Frozen per-topic token/USD ceilings written at generation 0 for the eval harness.",
    },
  },
  {
    pattern: "*/.knotica/eval-runs/*",
    entry: {
      title: "Eval run manifest",
      purpose:
        "Per-generation reproducibility bundle (manifest.json, corpus snapshot refs) for a single harness run.",
    },
  },
  {
    pattern: "*/.knotica/datasets/qa.jsonl",
    entry: {
      title: "Trainset (qa.jsonl)",
      purpose:
        "Loop corpus — curated Ask examples for the DSPy compile flywheel (good/corrected query-style rows).",
    },
  },
  {
    pattern: "*/.knotica/datasets/golden.jsonl",
    entry: {
      title: "Held-out eval (golden.jsonl)",
      purpose:
        "Loop corpus — frozen exam set; eval scalar and compile post-eval measure only against this split.",
    },
  },
  {
    pattern: "*/.knotica/datasets/MANIFEST.json",
    entry: {
      title: "Held-out seal (MANIFEST.json)",
      purpose:
        "Tamper-evident seal for golden.jsonl (sha256 + split=held_out) written by Freeze.",
    },
  },
  {
    pattern: "*/.knotica/datasets/golden.staging.jsonl",
    entry: {
      title: "Candidates (golden.staging.jsonl)",
      purpose:
        "Golden pipeline — uncommitted bootstrap candidates; review in the Datasets pane before Freeze.",
    },
  },
  {
    pattern: "*/.knotica/datasets/golden.staging.reviewed.jsonl",
    entry: {
      title: "Reviewed (golden.staging.reviewed.jsonl)",
      purpose:
        "Golden pipeline — human-kept candidates; Freeze promotes these into held-out golden.jsonl.",
    },
  },
  {
    pattern: "*/.knotica/datasets/*",
    entry: {
      title: "Topic dataset",
      purpose: "Train or held-out QA artifacts under the topic datasets directory.",
    },
  },
  {
    pattern: "*/.knotica/compiled/query_v1.json",
    entry: {
      title: "Compiled query program",
      purpose:
        "Portable DSPy output — optimized instructions plus few-shot demos consumed by headless query/eval.",
    },
  },
  {
    pattern: "*/.knotica/compiled/MANIFEST.json",
    entry: {
      title: "Compiled artifact manifest",
      purpose: "Metadata for the compiled query program (version, metrics snapshot, train/golden counts).",
    },
  },
  {
    pattern: "*/.knotica/compiled/*",
    entry: {
      title: "Compiled artifact",
      purpose: "DSPy compile output stored under the topic compiled directory.",
    },
  },
  {
    pattern: "*/.knotica/prompts/query.md",
    entry: {
      title: "Query prompt (topic override)",
      purpose:
        "Earned topic override for query — wins over root default for MCP prompts and eval runners.",
    },
  },
  {
    pattern: "*/.knotica/prompts/*",
    entry: {
      title: "Topic operation prompt",
      purpose: "Earned per-topic override for a Knotica operation prompt.",
    },
  },
  {
    pattern: ".knotica/*",
    entry: {
      title: "Vault metadata",
      purpose: "Hidden Knotica substrate at the vault root (prompts, locks, activity).",
    },
  },
  {
    pattern: "*/.knotica/*",
    entry: {
      title: "Topic metadata",
      purpose: "Hidden per-topic Knotica state (loop, compile, metrics, datasets, compiled).",
    },
  },
];

const FALLBACK: MetadataCatalogEntry = {
  title: "Knotica metadata",
  purpose: "Knotica metadata file or directory in the vault substrate.",
};

function normalizePath(path: string): string {
  return path.replace(/^\/+/, "").replace(/\\/g, "/");
}

function patternMatches(pattern: string, path: string): boolean {
  const patParts = pattern.split("/");
  const pathParts = path.split("/");
  if (patParts.length !== pathParts.length) return false;
  return patParts.every((part, index) => part === "*" || part === pathParts[index]);
}

/** Resolve catalog entry for a vault-relative metadata path. */
export function lookupMetadataCatalog(path: string): MetadataCatalogEntry {
  const normalized = normalizePath(path);
  for (const row of CATALOG) {
    if (patternMatches(row.pattern, normalized)) {
      return row.entry;
    }
  }
  return FALLBACK;
}

/** Human-readable byte size for tree labels. */
export function formatMetadataSize(bytes: number | undefined): string | null {
  if (bytes == null || Number.isNaN(bytes)) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
