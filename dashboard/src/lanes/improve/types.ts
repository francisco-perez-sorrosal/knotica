/**
 * Improve-lane wire shapes: the measured, per-topic loop.
 *
 * Everything the observe -> heal -> instrument -> prove -> promote -> gate
 * pipeline reads or writes -- datasets, metrics, compile, the branch
 * scoreboard, the arena, golden review, the loop's own two-phase results, and
 * the prompt diff that explains a promotion. Re-exported verbatim from
 * `src/types.ts`, so `import type { X } from "../../types"` still resolves.
 */

export type GateState = "unknown" | "pass" | "fail";
export type LoopStage =
  | "idle"
  | "evaluating"
  | "racing"
  | "promoting"
  | "passed"
  | "failed"
  | "merging"
  | "reverting";
/** `aborted` is refused-before-scoring: the scorer's scalars could not be ranked
 * against the gate baseline, so no variant was measured. Distinct from
 * `reverted`, which means the race ran and nobody won. */
export type ArenaStage =
  "idle" | "racing" | "promoting" | "completed" | "reverted" | "aborted";
export type DatasetRole =
  "trainset" | "held_out" | "seal" | "candidates" | "reviewed";

export interface DatasetFileRow {
  role: DatasetRole;
  label: string;
  group: "loop_corpora" | "golden_pipeline" | string;
  filename: string;
  path: string;
  purpose: string;
  exists: boolean;
  count: number;
  ready: boolean;
  query_train_n?: number;
  ready_min?: number;
  target_high?: number;
  seal?: {
    exists: boolean;
    ok: boolean;
    path: string;
    sha256?: string;
    version?: string;
    source?: string;
    split?: string;
    size?: number;
    error?: string;
  };
}

export interface DatasetsInventory {
  topic: string;
  floor: number;
  target_high: number;
  compile_ready_min: number;
  eval_min_golden: number;
  files: DatasetFileRow[];
  overlaps: {
    train_held_out: number;
    train_reviewed: number;
    train_candidates: number;
    train_held_out_samples: string[];
    train_reviewed_samples: string[];
  };
  pipeline: {
    candidates_n: number;
    reviewed_n: number;
    held_out_n: number;
    seal_ok: boolean;
    freeze_ready: boolean;
  };
}

export interface DatasetRecords {
  topic: string;
  role: DatasetRole;
  label: string;
  filename: string;
  path: string;
  exists: boolean;
  records: Array<Record<string, unknown>>;
  truncated: boolean;
  total: number;
}

export interface DatasetsBootstrapResult {
  topic: string;
  role: string;
  path: string;
  n_candidates: number;
  filename: string;
}

export interface DatasetsBootstrapTrainResult {
  topic: string;
  appended: number;
  pages_read: number;
  path: string;
  source: string;
  snapshot: string;
}

export interface DatasetsFreezeResult {
  topic: string;
  dataset_path: string;
  manifest_path: string;
  commit_sha: string;
  changed: boolean;
  n_frozen: number;
  below_floor: boolean;
  manifest: {
    sha256: string;
    version: string;
    source: string;
    split: string;
    size: number;
  };
}

export interface MetricsRecord {
  schema_version: number;
  topic: string;
  timestamp: string;
  generation: number;
  harness_version: string;
  scalar: number;
  components: {
    qa_accuracy: number;
    citation_validity: number;
    lint_violations: number;
    token_cost: number;
  };
  n_examples: number;
  corpus_ref: string;
  artifact_ref: string | null;
}
/** Liveness of a ``knotica loop`` watcher process for the scoped topic. */
export interface LoopRunnerLiveness {
  alive: boolean;
  pid: number | null;
  beat_at: string | null;
  interval_seconds: number | null;
}

/** One golden question's outcome, accumulated live as an eval run proceeds. */
export interface ExampleOutcome {
  id: string;
  status: string;
  error_class: string;
  detail: string;
}

/** Live in-flight eval progress; non-null only while an eval is running. */
export interface LoopProgress {
  phase: string;
  current: number;
  total: number;
  detail: string;
  updated_at: string;
  /** Per-question sub-phase: "answering" | "judging" | "". */
  substage: string;
  sub_current: number;
  sub_total: number;
  /** Per-question outcomes accumulated so far this run; absent on older payloads. */
  examples?: ExampleOutcome[];
}
export type CompileStage =
  "idle" | "running" | "optimizing" | "evaluating" | "completed" | "failed";

export interface CompileHistoryEntry {
  history_id: string;
  branch: string;
  head_sha?: string | null;
  base_sha?: string | null;
  merge_sha?: string | null;
  scalar_before?: number | null;
  scalar_after?: number | null;
  promoted?: boolean;
  branch_deleted?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface CompileStatus {
  schema_version: number;
  topic: string;
  stage: CompileStage | string;
  branch: string | null;
  message: string | null;
  trial: number;
  trial_total: number;
  scalar_before: number | null;
  scalar_after: number | null;
  error?: string | null;
  updated_at?: string;
  history?: CompileHistoryEntry[];
}

export interface CompileRunResult {
  topic: string;
  branch: string | null;
  stage: string;
  message: string;
  scalar_before: number | null;
  scalar_after: number | null;
  train_n: number;
  golden_n: number;
}

export interface CompilePromoteResult {
  mode: "dry-run" | "apply" | string;
  merged: boolean;
  branch: string;
  into?: string;
  candidate_branch?: string | null;
  current_branch?: string | null;
  commit_sha: string | null;
  message: string;
}

export type ScoreboardEntryKind =
  "default" | "compile" | "loop_candidate" | "loop_result" | "arena_variant";

export interface ScoreboardEntry {
  kind: ScoreboardEntryKind;
  name: string;
  sha: string | null;
  scalar: number | null;
  baseline: number | null;
  delta: number | null;
  delta_before?: number | null;
  beats_baseline?: boolean | null;
  status: string;
  created?: string | null;
  note?: string | null;
  promotable: boolean;
  slot?: "open" | "history" | "archived" | null;
  deletable?: boolean;
  base_sha?: string | null;
  head_sha?: string | null;
  merge_sha?: string | null;
  history_id?: string | null;
  diff_available?: boolean;
  branch_deleted?: boolean;
}

export interface BaselineMeta {
  scope: "topic";
  source: string;
  path: string;
  frozen: boolean;
  last_metrics_scalar: number | null;
}

export interface BranchScoreboard {
  schema_version: number;
  topic: string;
  baseline: number | null;
  baseline_meta: BaselineMeta;
  default_branch: string;
  open_compile_branch: string | null;
  entries: ScoreboardEntry[];
}

export interface BranchDeleteResult {
  mode: "dry-run" | "apply";
  deleted: boolean;
  topic: string;
  branch: string;
  compile_state_cleared?: boolean;
  message: string;
}
export interface ArenaVariant {
  id: string;
  label: string;
  scalar: number | null;
  status: "pending" | "scored" | "winner" | "lost" | string;
  /**
   * Provenance of this variant's scalar — the server carries it per variant
   * (`core/arena.py::ArenaVariant`) so a bare number stays interpretable:
   * what measured it, and against how many questions. Absent on races
   * recorded before provenance existed.
   */
  scorer_id?: string | null;
  n_examples?: number | null;
}

export interface ArenaStatus {
  schema_version: number;
  topic: string;
  race_id: string | null;
  stage: ArenaStage;
  baseline_scalar: number | null;
  variants: ArenaVariant[];
  winner_id: string | null;
  winner_scalar: number | null;
  candidate_branch: string | null;
  message: string | null;
  /** Race-level scalar provenance — same contract as the per-variant pair. */
  scorer_id?: string | null;
  n_examples?: number | null;
  updated_at?: string;
}

export interface ArenaHistory {
  topic: string;
  races: Array<Record<string, unknown>>;
  limit: number;
}

export interface MetricsWindow {
  topic: string;
  records: MetricsRecord[];
  has_more: boolean;
  next_before_generation: number | null;
  skipped_malformed: number;
}
export interface GoldenCandidate {
  question: string;
  reference_answer: string;
  citations: string[];
  pages_used: string[];
  support?: Array<{
    page?: string;
    quote?: string;
    verified?: boolean;
    line_start?: number;
    line_end?: number;
    current?: {
      char_start: number;
      char_end: number;
      line_start: number;
      line_end: number;
    };
  }>;
  /** Client-only keep/discard flag. */
  _kept?: boolean;
}

export interface GoldenPageInfo {
  exists: boolean;
  relative: string;
  obsidian_uri: string;
}

export interface GoldenReview {
  topic: string;
  vault_name: string;
  vault_path: string;
  candidates: GoldenCandidate[];
  pages: Record<string, GoldenPageInfo>;
  citation_links: Record<string, string>;
  source_keys: string[];
  qa_questions: string[];
  floor: number;
  target_high: number;
  resumed: boolean;
  loaded_from: string;
  reviewed_path: string;
}

export interface GoldenSaveResult {
  written: string;
  count: number;
  commit_sha?: string | null;
}
/** What a confirmed tick would decline on — read-only, quoted by the preview leg. */
export interface LoopHoldPreview {
  held: boolean;
  reasons: string[];
  cadence_remaining_seconds: number | null;
}

/**
 * `loop action=run_once`'s two phases share one wire shape.
 *
 * Phase 1 (preview, free) carries `confirm_nonce` + the quote and the holds
 * that would decline the tick; phase 2 (the tick, billed) carries the gate
 * outcome. Discriminate on `confirm_nonce` — its presence means nothing has
 * been spent yet.
 */
export interface LoopOnceResult {
  action: "run_once";
  topic: string;
  // phase 1 — preview
  estimated_cost?: string;
  holds?: LoopHoldPreview;
  confirm_nonce?: string;
  ttl?: number;
  // phase 2 — executed
  billed?: boolean;
  acted?: boolean;
  branch?: string | null;
  sha?: string | null;
  decision?: string;
  scalar?: number | null;
  message?: string;
}

export interface LoopPendingCandidate {
  branch: string;
  sha: string;
  pending: boolean;
}

export interface LoopSetBaselineResult {
  topic: string;
  baseline_scalar: number;
  harness_version: string | null;
  stage: string;
  message: string;
}

export interface LoopBaselinePolicyResult {
  topic: string;
  baseline_policy: "latest" | "best";
  baseline_scalar: number | null;
  message: string;
}

export interface LoopRebaselineResult {
  topic: string;
  baseline_scalar: number;
  /** The bar before this call; null when the topic had no baseline yet. */
  previous_scalar: number | null;
  /** False when the selected record was already the baseline — a real outcome,
   * not a failure, and indistinguishable from one without this flag. */
  changed: boolean;
  harness_version: string | null;
  baseline_policy: "latest" | "best";
  message: string;
}

export interface LoopCadenceConfig {
  topic: string;
  eval_min_interval_hours: number;
  eval_window: string;
  eval_num_threads: number;
  /** `"heuristic"` (free, not gate-comparable) or `"eval"` (billed per variant). */
  arena_scorer: string;
}

/** Discriminated by ``confirm_nonce`` presence: preview (phase 1) vs executed (phase 2). */
export interface LoopRunEvalResult {
  action: "run_eval";
  topic: string;
  worker: string;
  judge: string;
  num_threads: number;
  estimated_cost?: string;
  confirm_nonce?: string;
  ttl?: number;
  billed?: boolean;
  acted?: boolean;
  decision?: string;
  scalar?: number | null;
  message?: string;
}

export interface BaselineProbeResult {
  topic: string;
  scalar: number;
  harness_version: string;
  runner_mode: string;
  n_examples: number;
  corpus_ref: string;
  generation: number;
  persisted: boolean;
  record: MetricsRecord;
}
export type PromptDiffLineType = "context" | "add" | "del";

export interface PromptDiffLine {
  type: PromptDiffLineType;
  text: string;
  old_no: number | null;
  new_no: number | null;
}

export interface PromptDiffHunk {
  header: string;
  lines: PromptDiffLine[];
}

export interface PromptDiffResult {
  schema_version: number;
  topic: string;
  path: string;
  base_ref: string;
  head_ref: string;
  patch: string;
  hunks: PromptDiffHunk[];
  truncated?: boolean;
  empty?: boolean;
  source?: string;
  comparison?: string;
  base_sha?: string | null;
  head_sha?: string | null;
  merge_sha?: string | null;
  branch?: string | null;
  history_id?: string | null;
  demo_count?: number;
  artifact_path?: string | null;
}

export type PromptDiffMode = "git" | "compiled";
