/** Wire shapes returned by the dashboard MCP tools. */

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
export type ArenaStage = "idle" | "racing" | "promoting" | "completed" | "reverted";
export type PaneId = "vault" | "ask" | "loop" | "arena" | "datasets" | "golden" | "ingest";

export type DatasetRole = "trainset" | "held_out" | "seal" | "candidates" | "reviewed";

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

export interface AvailableVault {
  name: string;
  path: string;
  ready: boolean;
  detail: string;
}

/** Whether headless LLM work (Ask/Arena/Compile/live eval) can authenticate. */
export interface LlmAvailability {
  available: boolean;
  mode: "oauth" | "api_key" | null;
  /** Why unavailable: "credentials" = no env token/key; "deps" = anthropic package missing. */
  reason?: "credentials" | "deps" | null;
}

/** Liveness of a ``knotica loop`` watcher process for the scoped topic. */
export interface LoopRunnerLiveness {
  alive: boolean;
  pid: number | null;
  beat_at: string | null;
  interval_seconds: number | null;
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
}

export interface WikiStatus {
  schema_version: number;
  vault: string;
  vault_name: string;
  vault_path: string;
  default_vault: string;
  available_vaults: AvailableVault[];
  compile_ready_threshold: number;
  /** Held-out golden floor for compile-ready / eval (same as EVAL_MIN_GOLDEN). */
  eval_min_golden?: number;
  topics: Array<{
    topic: string;
    pages: number;
    curated: number;
    trainset_n?: number;
    golden_n?: number;
    compile_ready?: boolean;
    to_compile_ready: number;
    compiled?: {
      present: boolean;
      version: string;
      scalar: number | null;
      compiled_at: string;
      optimizer?: "mipro" | "bootstrap" | null;
      fallback_reason?: string | null;
    } | null;
    lint_violations: number;
    last_eval: MetricsRecord | null;
  }>;
  totals: { topics: number; pages: number; curated: number; lint_violations: number };
  last_lint: string | null;
  unpushed: number | null;
  gate: { state: GateState; baseline: number | null; last_scalar: number | null };
  llm: LlmAvailability;
  loop: {
    runner: LoopRunnerLiveness;
    stage: LoopStage | null;
    candidate_branch?: string | null;
    last_decision?: string | null;
    arena_race_id?: string | null;
    arena_stage?: ArenaStage | null;
    baseline_frozen?: boolean;
    baseline_scalar?: number | null;
    /** Gate policy: "latest" tracks reality; "best" is a high-water mark. */
    baseline_policy?: "latest" | "best";
    pending_candidates?: LoopPendingCandidate[];
    metrics_hint?: { last_scalar: number | null; last_generation: number | null } | null;
    progress?: LoopProgress | null;
  };
  compile?: CompileStatus | null;
}

export type CompileStage =
  | "idle"
  | "running"
  | "optimizing"
  | "evaluating"
  | "completed"
  | "failed";

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
  | "default"
  | "compile"
  | "loop_candidate"
  | "loop_result"
  | "arena_variant";

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

export interface QueryAnswer {
  topic: string;
  question: string;
  answer: string;
  citations: string[];
  pages_used: string[];
}

export interface ArenaVariant {
  id: string;
  label: string;
  scalar: number | null;
  status: "pending" | "scored" | "winner" | "lost" | string;
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

export type ActivityWorkflow = "ingest" | "curate";

export interface IngestEvent {
  schema_version: number;
  ts: string;
  run_id: string;
  workflow?: ActivityWorkflow;
  topic: string;
  stage: string;
  status: string;
  title: string;
  detail: string;
  citation_key: string;
  path: string;
  commit_sha: string;
  source: "client" | "server";
  /** True when this stage was reported after a later pipeline step. */
  out_of_order?: boolean;
}

export interface IngestRun {
  run_id: string;
  workflow?: ActivityWorkflow;
  topic: string;
  citation_key: string;
  started_at?: string;
  updated_at?: string;
  current_stage: string;
  current_title: string;
  status: string;
  terminal: boolean;
  stage_index: number;
  event_count: number;
  stages_seen: string[];
}

export interface IngestActivity {
  schema_version: number;
  activity_path: string;
  pipeline_stages: string[];
  curate_pipeline_stages?: string[];
  events: IngestEvent[];
  active_run: IngestRun | null;
  runs: IngestRun[];
  has_more: boolean;
}

export interface DoctorCheck {
  name: string;
  status: "PASS" | "WARN" | "FAIL" | string;
  message: string;
  remediation: string | null;
}

export interface DoctorFixGuidance {
  kind: string;
  summary: string;
  commands: string[];
  note: string;
}

export interface DoctorReport {
  schema_version: number;
  vault: string | null;
  quick: boolean;
  ok: boolean;
  exit_code: number;
  checks: DoctorCheck[];
  summary: { pass: number; warn: number; fail: number };
  /** Present when doctor_run was called with fix=true (CLI ``--fix``). */
  fix_guidance?: DoctorFixGuidance | null;
}

export interface DirtyEntry {
  path: string;
  code: string;
  tracked: boolean;
  untracked: boolean;
}

export interface DoctorRepairResult {
  mode: "dry-run" | "apply" | string;
  dirty_count?: number;
  entries?: DirtyEntry[];
  tracked_paths?: string[];
  untracked_paths?: string[];
  restored?: string[];
  message?: string;
}

export interface LintViolation {
  check: string;
  path: string;
  line: number | null;
  message: string;
  fix: string;
}

export interface VaultLintResult {
  topic: string;
  violations: LintViolation[];
}

export interface OkfCheckResult {
  status: string;
  failed: boolean;
  bundle_root: string;
  concept_files_checked: number;
  reserved_files_checked: number;
  errors: Array<{ path: string; code: string; message: string; severity: string }>;
  notes: string[];
  strict_failures: string[];
}

export interface OkfRepairResult {
  status: string;
  dry_run: boolean;
  mode: string;
  files_changed: string[];
  notes: string[];
  report_path: string | null;
  commit_sha: string | null;
}

export interface LoopOnceResult {
  topic: string;
  acted: boolean;
  branch: string | null;
  sha: string | null;
  decision: string;
  scalar: number | null;
  message: string;
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
  harness_version: string | null;
  baseline_policy: "latest" | "best";
  message: string;
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

export type MetadataNodeKind = "file" | "dir";

export interface MetadataTreeNode {
  name: string;
  path: string;
  kind: MetadataNodeKind;
  exists: boolean;
  size?: number;
  mtime?: string;
  scope?: "topic";
  children?: MetadataTreeNode[];
}

export interface VaultMetadataTree {
  schema_version: number;
  topic: string | null;
  children: MetadataTreeNode[];
}
