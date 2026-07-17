/** Wire shapes returned by the dashboard MCP tools. */

export type GateState = "unknown" | "pass" | "fail";
export type LoopStage = "idle" | "evaluating" | "passed" | "failed" | "merging" | "reverting";
export type PaneId = "loop" | "vault" | "golden" | "ingest";

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
  artifact_ref: string;
}

export interface AvailableVault {
  name: string;
  path: string;
  ready: boolean;
  detail: string;
}

export interface WikiStatus {
  schema_version: number;
  vault: string;
  vault_name: string;
  vault_path: string;
  default_vault: string;
  available_vaults: AvailableVault[];
  compile_ready_threshold: number;
  topics: Array<{
    topic: string;
    pages: number;
    curated: number;
    to_compile_ready: number;
    lint_violations: number;
    last_eval: MetricsRecord | null;
  }>;
  totals: { topics: number; pages: number; curated: number; lint_violations: number };
  last_lint: string | null;
  unpushed: number | null;
  gate: { state: GateState; baseline: number | null; last_scalar: number | null };
  loop: { stage: LoopStage | null; candidate_branch?: string | null; last_decision?: string | null };
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
