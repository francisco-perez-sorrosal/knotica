/**
 * Wire shapes returned by the dashboard MCP tools.
 *
 * Only the genuinely cross-cutting shapes are declared here: the pane
 * identifier, the vault/LLM availability pair every lane reads, the
 * server-derived rail row, and `WikiStatus` -- the one payload that spans
 * every lane at once. Each lane's own payload types live beside that lane in
 * `lanes/<lane>/types.ts`.
 *
 * The re-exports at the foot of this file are the barrel: every type that
 * moved out is still importable from `"./types"`, so no import site had to
 * change when the split landed. They are `export type` (never `export *`), so
 * the whole barrel erases at transpile time and adds nothing to the bundle.
 */

import type {
  ArenaStage,
  BaselineUnreachable,
  CompileStatus,
  GateState,
  LoopPendingCandidate,
  LoopProgress,
  LoopRunnerLiveness,
  LoopStage,
  MetricsRecord,
} from "./lanes/improve/types";
import type {
  GapStatusSummary,
  SuggestionStatusSummary,
} from "./lanes/fill/types";
import type { NotesStatusSummary } from "./lanes/tend/types";

/**
 * The panes the dashboard can show — six process lanes and nothing else.
 * Every tool-shaped pane this set once carried (`vault`, `loop`, `arena`,
 * `datasets`, `golden`, `notes`, then `ask`, `ingest`, `sources`) dissolved
 * into the lane that owns its work; their `?pane=` keys live on as inbound
 * aliases in `paneRouting.ts` but are no longer destinations.
 *
 * `home` is listed first because it is the pane a bare URL opens
 * (`paneRouting.ts`'s `DEFAULT_PANE`) and the first tab in the nav.
 */
export type PaneId = "home" | "improve" | "tend" | "learn" | "answer" | "fill";

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

/** One rail row's already-derived state (`core/process_model.py::derive_stages`,
 * `core/status_lanes.py::lanes_block`) -- the server is the one source of
 * position truth; the client renders this verbatim, never re-derives it.
 *
 * Four of the five are positions. `unknown` is not: it is the honest absence
 * of one, declared by an adapter that found no evidence either way. `pending`
 * claims "not reached yet"; `unknown` claims nothing, and the UI must render
 * the difference rather than collapsing it. */
export type LaneRailStageState =
  | "pending"
  | "active"
  | "complete"
  | "blocked"
  | "unknown";

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
    /** Every non-Home lane's rail, server-derived and total. Optional
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
    /** Non-null when the baseline sits above the default branch's own measured
     * scalar — a state in which nothing can pass the gate, so every refusal's
     * diff blames the candidate for a shortfall the bar created. Lives under
     * `gate` in both `wiki_status` views; `loop.baseline_unreachable` is the
     * deprecated mirror kept for one release. */
    baseline_unreachable?: BaselineUnreachable | null;
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
    /** @deprecated Mirror of `gate.baseline_unreachable`, kept for one release
     * so a server that predates the move still renders. Read `gate`'s first. */
    baseline_unreachable?: BaselineUnreachable | null;
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

// ---------------------------------------------------------------------------
// Barrel: every per-lane type, re-exported from its new home so that
// `import type { X } from "./types"` keeps resolving for every consumer.
// ---------------------------------------------------------------------------

export type {
  GateState,
  LoopStage,
  ArenaStage,
  DatasetRole,
  DatasetFileRow,
  DatasetsInventory,
  DatasetRecords,
  DatasetsBootstrapResult,
  DatasetsBootstrapTrainResult,
  DatasetsFreezeResult,
  MetricsRecord,
  LoopRunnerLiveness,
  ExampleOutcome,
  LoopProgress,
  CompileStage,
  CompileHistoryEntry,
  CompileStatus,
  CompileRunResult,
  CompilePromoteResult,
  ScoreboardEntryKind,
  ScoreboardEntry,
  BaselineMeta,
  BranchScoreboard,
  BranchDeleteResult,
  ArenaVariant,
  ArenaStatus,
  ArenaHistory,
  MetricsWindow,
  GoldenCandidate,
  GoldenPageInfo,
  GoldenReview,
  GoldenSaveResult,
  LoopHoldPreview,
  LoopOnceResult,
  LoopPendingCandidate,
  LoopSetBaselineResult,
  LoopBaselinePolicyResult,
  LoopRebaselineResult,
  LoopCadenceConfig,
  LoopCadencePreview,
  LoopCadenceResult,
  LoopRunEvalResult,
  BaselineProbeResult,
  BaselineUnreachable,
  PromptDiffLineType,
  PromptDiffLine,
  PromptDiffHunk,
  PromptDiffResult,
  PromptDiffMode,
} from "./lanes/improve/types";
export type {
  DoctorCheck,
  DoctorFixGuidance,
  DoctorReport,
  DirtyEntry,
  DoctorRepairResult,
  LintViolation,
  VaultLintResult,
  OkfCheckResult,
  OkfRepairResult,
  MetadataNodeKind,
  MetadataTreeNode,
  VaultMetadataTree,
  NoteIntent,
  NoteIntentFilter,
  AnchorFidelity,
  AnchorStatus,
  AnchorStatusFilter,
  AnchorProjectionStatus,
  NoteAnchor,
  NoteRecord,
  NoteReadResult,
  NotesListResult,
  NotesStatusSummary,
  NoteCaptureAlternative,
  NoteCaptureResult,
  NoteDriftAlternative,
  NoteDrift,
  NoteDriftItem,
  NotesDriftResult,
  NoteAction,
  NoteDecisionEnvelope,
  NoteAnchorActionResult,
  NoteArchiveActionResult,
  NotePromoteTrainsetResult,
  NotePromoteGapResult,
  PromoteTarget,
  NotePromoteActionResult,
} from "./lanes/tend/types";
export type {
  ActivityWorkflow,
  IngestEvent,
  IngestRun,
  IngestActivity,
} from "./lanes/learn/types";
export type { QueryAnswer } from "./lanes/answer/types";
export type {
  SuggestionStatusSummary,
  GapOrigin,
  GapStatusSummary,
  ReputabilityTier,
  SuggestionReputability,
  SuggestionCandidate,
  SuggestionStatus,
  GateOutcomeVerdict,
  GateOutcomeRegressedQuestion,
  GateOutcome,
  SuggestionRecord,
  SuggestionsStatusFilter,
  SuggestionsReadResult,
  GapStatus,
  GapsStatusFilter,
  GapFaultClass,
  GapRecord,
  GapfillDiscoverResult,
  GapReportResult,
  GapsReadResult,
  ReviewGapResult,
  SuggestionAction,
  SuggestionReviewResult,
  SessionState,
  SessionNextActor,
  SessionGateOutcome,
  SessionStatus,
} from "./lanes/fill/types";
export type {
  StatusView,
  AttentionSuggestions,
  AttentionTopicRow,
  AttentionStatus,
  AttentionUrgency,
  AttentionKind,
  AttentionRow,
} from "./lanes/home/types";
