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
/** `aborted` is refused-before-scoring: the scorer's scalars could not be ranked
 * against the gate baseline, so no variant was measured. Distinct from
 * `reverted`, which means the race ran and nobody won. */
export type ArenaStage =
  "idle" | "racing" | "promoting" | "completed" | "reverted" | "aborted";
export type PaneId =
  | "vault"
  | "ask"
  | "loop"
  | "arena"
  | "datasets"
  | "golden"
  | "ingest"
  | "sources"
  | "notes"
  | "improve"
  | "tend";

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

/** One rail row's already-derived state (`core/process_model.py::derive_stages`,
 * `core/status_lanes.py::lanes_block`) -- the server is the one source of
 * position truth; the client renders this verbatim, never re-derives it. */
export type LaneRailStageState = "pending" | "active" | "complete" | "blocked";

export interface LaneRailStageStatus {
  id: string;
  state: LaneRailStageState;
  reason: string | null;
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
    suggestions?: SuggestionStatusSummary;
    gaps?: GapStatusSummary;
    notes?: NotesStatusSummary;
    /** Every non-Home lane's rail, server-derived and total (Step 48). Optional
     *  for backward compat with a `wiki_status` payload predating the lanes
     *  block -- absent means "render every stage pending," never a crash. */
    lanes?: Record<
      "learn" | "answer" | "improve" | "fill" | "tend",
      LaneRailStageStatus[]
    >;
  }>;
  totals: {
    topics: number;
    pages: number;
    curated: number;
    lint_violations: number;
    /** Vault-wide roll-up of the per-topic note counts. Absent on a server
     *  whose wiki_status predates the notes layer. */
    notes?: NotesStatusSummary;
  };
  last_lint: string | null;
  unpushed: number | null;
  gate: {
    state: GateState;
    baseline: number | null;
    last_scalar: number | null;
  };
  llm: LlmAvailability;
  loop: {
    runner: LoopRunnerLiveness;
    stage: LoopStage | null;
    candidate_branch?: string | null;
    last_decision?: string | null;
    arena_race_id?: string | null;
    arena_stage?: ArenaStage | null;
    /** Why the arena reached that stage — "reverted" is a normal terminal
     * state, so the stage word alone cannot tell a healthy race from one the
     * baseline made unwinnable. */
    arena_message?: string | null;
    baseline_frozen?: boolean;
    baseline_scalar?: number | null;
    /** Non-null when the baseline sits above the default branch's own measured
     * scalar — a state in which nothing can pass the gate, so every refusal's
     * diff blames the candidate for a shortfall the bar created. */
    baseline_unreachable?: {
      baseline: number;
      last_scalar: number;
      generation?: number | null;
      message: string;
      fix: string;
    } | null;
    /** Gate policy: "latest" tracks reality; "best" is a high-water mark. */
    baseline_policy?: "latest" | "best";
    pending_candidates?: LoopPendingCandidate[];
    metrics_hint?: {
      last_scalar: number | null;
      last_generation: number | null;
    } | null;
    progress?: LoopProgress | null;
  };
  compile?: CompileStatus | null;
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
  errors: Array<{
    path: string;
    code: string;
    message: string;
    severity: string;
  }>;
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

/** ``wiki_status``'s per-topic gap-fill queue summary — all-zero when empty. */
export interface SuggestionStatusSummary {
  pending: number;
  approved_awaiting_ingest: number;
  deferred: number;
  rejected: number;
  ingested: number;
  newest_proposed_at: string | null;
  /** Approved records whose most recent gate pass was refused — re-workable, not re-submitted. */
  refused_awaiting_rework: number;
}

/** Gap provenance: eval-proven, conversationally reported, or guillotine-weakened. */
export type GapOrigin = "measured" | "reported" | "retracted";

/** ``wiki_status``'s per-topic open-gap summary by origin — all-zero when empty. */
export interface GapStatusSummary {
  measured: number;
  reported: number;
  retracted: number;
  open_total: number;
}

export type ReputabilityTier =
  "peer_reviewed" | "preprint_known_lab" | "established_org" | "general_web";

export interface SuggestionReputability {
  tier: ReputabilityTier;
  score: number;
  signals: string[];
}

/** Verbatim ``SourceCandidate.to_record()`` payload, denormalized onto the suggestion. */
export interface SuggestionCandidate {
  url: string;
  title: string;
  snippet: string;
  source_provider: string;
  authors: string[] | null;
  venue: string | null;
  published_date: string | null;
  doi: string | null;
  citation_count: number | null;
  is_open_access: boolean | null;
  fwci: number | null;
  provider_score: number | null;
  reputability: SuggestionReputability | null;
  schema_version: number;
}

export type SuggestionStatus =
  "pending" | "approved" | "rejected" | "deferred" | "ingested";

export type GateOutcomeVerdict = "merged" | "refused";

/** One bounded per-question dilution row from a refused gate pass (worst-first, capped). */
export interface GateOutcomeRegressedQuestion {
  qa_id: string;
  question: string;
  baseline_score: number;
  candidate_score: number;
  delta: number;
}

/**
 * The gate's verdict on a suggestion's ingested source candidate, stamped once a
 * ``source`` candidate has been evaluated. Null before gating. This is the
 * record's stored shape (``ref``/``reason``/``regressed_questions``) as returned by
 * ``suggestions_read`` — not the ``source_ingest_submit`` wire envelope, which
 * renames these to ``refused_ref``/``diff_summary``.
 */
export interface GateOutcome {
  verdict: GateOutcomeVerdict;
  scalar: number;
  baseline_scalar: number;
  ref: string;
  /** Present on ``refused`` only. */
  reason?: string;
  /** Present on ``refused`` only. */
  regressed_questions?: GateOutcomeRegressedQuestion[];
}

export interface SuggestionRecord {
  schema_version: number;
  suggestion_id: string;
  topic: string;
  gap_id: string;
  qa_id: string;
  fault_class: string;
  question: string;
  reference_pages: string[];
  rank: number;
  query_text: string;
  candidate: SuggestionCandidate;
  status: SuggestionStatus;
  proposed_at: string;
  decided_at: string | null;
  decided_reason: string | null;
  ingested_at: string | null;
  detected_generation: number;
  /** Provenance carried from the originating gap; null on pre-feature records. */
  gap_origin?: GapOrigin | null;
  /** The gate's verdict on this suggestion's candidate; null until gated. */
  gate_outcome?: GateOutcome | null;
}

export type SuggestionsStatusFilter = SuggestionStatus | "all";

export interface SuggestionsReadResult {
  topic: string;
  status_filter: SuggestionsStatusFilter;
  suggestions: SuggestionRecord[];
  status_counts: Record<SuggestionStatus, number>;
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  skipped_malformed: number;
}

/** Lifecycle of a P1 gap record. P1 writes ``open``; P3/P4 flip it terminal. */
export type GapStatus = "open" | "resolved" | "dismissed";

export type GapsStatusFilter = GapStatus | "all";

/** Why the wiki fell short. Only knowledge-cause verdicts are ever persisted. */
export type GapFaultClass = "genuine_gap" | "dilution";

/**
 * One diagnosed gap, as ``gaps_read`` returns it.
 *
 * This is the queue *upstream* of `SuggestionRecord`: a gap exists from the
 * moment it is filed, and only gains candidate sources once a discovery drain
 * promotes it. Fields that a `reported` or `retracted` gap cannot have are
 * zero by construction, never measured — do not present them as measurements.
 */
export interface GapRecord {
  gap_id: string;
  topic: string;
  qa_id: string;
  fault_class: GapFaultClass;
  status: GapStatus;
  detected_at: string;
  question: string;
  reference_pages: string[];
  reference_pages_exist: boolean;
  origin: GapOrigin;
  /** Prose supplied by the reporter; only ever set on ``origin: "reported"``. */
  reported_reason?: string | null;
  /** Constant zero on reported/retracted gaps — no eval generation backs them. */
  detected_generation: number;
}

/**
 * `gapfill_discover`'s two phases share one wire shape.
 *
 * Phase 1 (preview, free) carries `confirm_nonce` + the quote; phase 2 (the
 * drain, billed) carries the counts. Discriminate on `confirm_nonce` — its
 * presence means nothing has been spent yet.
 */
export interface GapfillDiscoverResult {
  action: "gapfill_discover";
  topic: string;
  provider_configured: boolean;
  // phase 1 — preview
  open_gaps?: number;
  would_drain?: number;
  max_gaps?: number | null;
  estimated_cost?: string;
  confirm_nonce?: string;
  ttl?: number;
  // phase 2 — executed
  gaps_considered?: number;
  gaps_drained?: number;
  suggestions_staged?: number;
}

export interface GapsReadResult {
  topic: string;
  status_filter: GapsStatusFilter;
  gaps: GapRecord[];
  status_counts: Record<GapStatus, number>;
  origin_counts: Record<GapOrigin, number>;
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  skipped_malformed: number;
}

export type SuggestionAction = "approve" | "reject" | "defer" | "mark_ingested";

export interface SuggestionReviewResult {
  mode: "dry-run" | "apply";
  topic: string;
  suggestion_id: string;
  action: SuggestionAction;
  from_status: SuggestionStatus;
  to_status: SuggestionStatus;
  // dry-run fields
  would_commit?: boolean;
  reason_required?: boolean;
  candidate_title?: string;
  preview?: string;
  // apply fields
  committed?: boolean;
  commit?: string | null;
  decided_at?: string | null;
  ingested_at?: string | null;
}

// ---------------------------------------------------------------------------
// Personal notes (marginalia) — the read-only `notes` dispatcher.
// ---------------------------------------------------------------------------

/** Why the note was written. Filterable on the list action. */
export type NoteIntent = "reflection" | "dispute" | "gap" | "question";
export type NoteIntentFilter = NoteIntent | "all";

/**
 * How precisely an anchor located its target. The resolver ladder produces only
 * these three — there is no `block`/`section` rung, so none is declared.
 */
export type AnchorFidelity = "span" | "page" | "topic";

/**
 * A note's resolved-anchor bucket — the filterable, countable set. `unanchored`
 * is not drift: the anchor never pointed at a page. `shifted` is not drift: the
 * anchor healed itself at a new offset with the verbatim text intact. `fuzzy`
 * and `orphaned` are drift: `fuzzy` found only a paraphrase, `orphaned` found
 * nothing at all.
 */
export type AnchorStatus =
  "exact" | "unanchored" | "shifted" | "fuzzy" | "orphaned";
export type AnchorStatusFilter = AnchorStatus | "all";

/**
 * A single anchor's projection status. Adds `anchor-invalid` — a record that
 * never located anything at all (unreadable claimed page, ambiguous quote).
 * It is a data-integrity problem, not drift, so it is excluded from the
 * note-level bucket entirely rather than folded into `orphaned`.
 */
export type AnchorProjectionStatus = AnchorStatus | "anchor-invalid";

/** One anchor as recorded, plus how it resolves against the vault right now. */
export interface NoteAnchor {
  index: number;
  /** Vault-relative page path; "" for a topic-fidelity anchor. */
  page: string;
  heading: string;
  /** What the anchor bullet recorded. */
  fidelity: string;
  status: AnchorProjectionStatus;
  /** What it resolves to today; null exactly when status is `anchor-invalid`. */
  resolved_fidelity: AnchorFidelity | null;
  /** The passage originally pinned. */
  quote: string;
  /** Commit sha the pin was taken against. */
  pinned_at: string;
}

export interface NoteRecord {
  note_id: string;
  /** Vault-relative path to the note file, for opening it by hand. */
  path: string;
  intent: NoteIntent;
  created: string;
  updated: string;
  /** The note's own lifecycle field from frontmatter (defaults to "active"). */
  note_status: string;
  /** Resolved-anchor bucket; null for a note with no anchors at all. */
  status: AnchorStatus | null;
  tags: string[];
  /** The note's text. */
  note: string;
  anchors: NoteAnchor[];
  /** Anchor bullets the grammar could not parse -- data, not corruption. */
  skipped_anchor_count: number;
}

/** `notes action=read` — one note in full, with its owning topic echoed back. */
export interface NoteReadResult extends NoteRecord {
  topic: string;
}

/** `notes action=list` — one filtered, sorted, paginated page of notes. */
export interface NotesListResult {
  topic: string;
  intent_filter: NoteIntentFilter;
  status_filter: AnchorStatusFilter;
  notes: NoteRecord[];
  intent_counts: Record<NoteIntent, number>;
  /** Anchorless notes are in no bucket, so these can sum to less than total_count. */
  status_counts: Record<AnchorStatus, number>;
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  skipped_malformed: number;
}

/** ``wiki_status``'s per-topic notes summary; absent on servers that predate it. */
export interface NotesStatusSummary {
  total: number;
  drifted: number;
}

// ---------------------------------------------------------------------------
// Personal notes -- the drift review queue and the four mutating actions
// (``reanchor``/``detach``/``promote``/``archive``). The read-only shapes
// above (``NoteAnchor``, ``NoteRecord``, ``NotesListResult``) are shared.
// ---------------------------------------------------------------------------

/**
 * ``notes action=drift``'s candidate placement -- unlike ``note_capture``'s
 * own ``alternatives`` (a different, unrelated shape: ``{page, heading}``,
 * no ``overlap``, because nothing was scored there), this one carries an
 * ``overlap`` **when one was measured**. It is `null` for a structural
 * guess: the enclosing heading survived, so the section is a real placement,
 * but no passage-level similarity was computed. Render the null case as
 * prose, never as `0%` and never as a percentage.
 */
export interface NoteDriftAlternative {
  page: string;
  heading: string;
  overlap: number | null;
}

/**
 * ``notes action=drift``'s per-item detail. ``overlap`` is `null` whenever
 * the resolver had no measurement to report -- ``anchor-invalid`` (no
 * candidate search ever ran), a deleted-page orphan (no page left to
 * search), and the case that matters most: a **surviving heading whose
 * passage shares no vocabulary with the page at all**, where the ladder
 * supplies `guess_threshold - CLAMP_EPSILON` internally to satisfy its own
 * nullability invariant. That value is a *ceiling*, so surfacing it as a
 * survival percentage showed a deleted passage as the most confident item in
 * the queue. Distinguish "0% survived" from "nothing was comparable".
 * ``rewritten_at``/``rewritten_by`` are always strings, `""` (never omitted,
 * never null) when there is no rewrite to attribute -- every
 * ``anchor-invalid`` item is in that shape, since nothing about the page
 * caused its corruption.
 */
export interface NoteDrift {
  anchor_index: number;
  /**
   * Why this anchor drifted, so the review surface can say the true thing.
   *
   * `superseded` means the anchored page was **replaced wholesale**, not
   * edited: page similarity collapsed and no heading survived. That is a
   * different event from a reword and wants a different affordance -- the
   * ladder's best guess points into content unrelated to the anchored passage,
   * so the server sends `alternatives: []` and the UI must not invite a
   * re-anchor onto an arbitrary span. Phase 3 measured one such event supplying
   * 85% of all observed orphaning, indistinguishable here from a reword.
   *
   * Optional for forward/backward compatibility: a dashboard talking to a
   * server that predates the classifier sees it absent and falls back to
   * `rewritten`, which is the pre-existing behaviour.
   */
  cause?: "rewritten" | "superseded";
  /** Always populated, orphans included -- the historical text is never withheld. */
  pinned_quote: string;
  /** The current text at the resolved placement; "" when nothing is confidently placed. */
  live_quote: string;
  overlap: number | null;
  alternatives: NoteDriftAlternative[];
  rewritten_at: string;
  rewritten_by: string;
}

/** One review-queue member: the note it belongs to, plus that anchor's drift detail. */
export interface NoteDriftItem {
  note: NoteRecord;
  drift: NoteDrift;
}

/**
 * ``notes action=drift`` -- the review queue: every anchor resolving
 * ``fuzzy``, ``orphaned``, or ``anchor-invalid``. ``total_count`` is
 * ``items.length``, the whole queue including ``anchor-invalid``, so
 * pagination stays one contract with ``items``; ``invalid_count`` is a
 * breakdown of how many of those are ``anchor-invalid``, not a disjoint
 * bucket. Deliberately unlike ``NotesStatusSummary.drifted`` (``fuzzy +
 * orphaned`` only, from ``wiki_status``) -- the queue header and that badge
 * disagree by design, not by bug.
 */
export interface NotesDriftResult {
  topic: string;
  items: NoteDriftItem[];
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  invalid_count: number;
}

export type NoteAction = "reanchor" | "detach" | "promote" | "archive";

/**
 * The uniform ``mode=dry-run`` preview every ``notes`` mutating action
 * returns -- the same decision-envelope shape ``suggestions_review`` renders.
 */
export interface NoteDecisionEnvelope {
  mode: "dry-run";
  topic: string;
  note_id: string;
  action: NoteAction;
  decision_id: string;
  summary: string;
  context: Record<string, unknown>;
  options: Array<{ action: string; preview: string; reversible: boolean }>;
  provenance: Record<string, unknown>;
  reason_required: boolean;
}

/** ``notes action=reanchor|detach``'s ``mode=apply`` result -- both append one anchor record. */
export interface NoteAnchorActionResult {
  mode: "apply";
  topic: string;
  action: "reanchor" | "detach";
  committed: boolean;
  note_id: string;
  path: string;
  anchor_index: number;
  /** Opaque, forward-compatible -- "reanchored" or "detached" today. */
  kind: string;
  commit: string;
}

/** ``notes action=archive``'s ``mode=apply`` result -- frontmatter-only, touches no anchor. */
export interface NoteArchiveActionResult {
  mode: "apply";
  topic: string;
  action: "archive";
  committed: boolean;
  note_id: string;
  path: string;
  status: string;
  /** False when this call changed nothing -- see ``duplicate``. */
  written: boolean;
  /** True when the note was already archived; mirrors ``capture``'s own vocabulary. */
  duplicate: boolean;
  commit: string;
}

/** ``promote target=trainset``'s ``mode=apply`` result -- delegates to ``curate_example``. */
export interface NotePromoteTrainsetResult {
  mode: "apply";
  topic: string;
  action: "promote";
  committed: boolean;
  path: string;
  example_count: number;
  appended: boolean;
}

/** ``notes action=promote target=gap``'s ``mode=apply`` result -- delegates to ``report_gap``. */
export interface NotePromoteGapResult {
  mode: "apply";
  topic: string;
  action: "promote";
  committed: boolean;
  gap_id: string;
  qa_id: string;
  question: string;
  fault_class: string;
  status: string;
  origin: GapOrigin;
  reference_pages: string[];
  written: boolean;
}

/** The two ``target``s the dashboard offers; ``golden`` always rejects tool-side (dec-059). */
export type PromoteTarget = "trainset" | "gap";

export type NotePromoteActionResult =
  NotePromoteTrainsetResult | NotePromoteGapResult;
