/**
 * Fill-lane wire shapes: the gap-fill workflow.
 *
 * diagnose -> discover -> approve -> ingest, plus the handoff stage's
 * `fill(action="session_status")` read contract. The gap queue lives here
 * rather than in Answer even though `gap_report` is dispatched from Answer's
 * `react` stage -- Fill owns the queue those reports land in. Re-exported
 * verbatim from `src/types.ts`, so `import type { X } from "../../types"`
 * still resolves.
 */

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
  /** Candidates dropped because the vault already stores their source (by
   *  canonical URL against `sources/<topic>/` provenance) — the honest reason
   *  a drain can stage little while having found plenty. */
  candidates_already_in_vault?: number;
  /** Pre-existing open queue records this drain closed: sources already in the
   *  vault, plus per-gap duplicates (archive editions of one page) collapsed
   *  to their best record. The queue shrinking is an outcome, not a glitch. */
  stale_suggestions_closed?: number;
}

/**
 * `review_gap`'s result — the human dismiss/reopen transition over the gap
 * queue. A dismiss also closes the gap's still-open suggestions in the same
 * commit; their ids come back so the outcome can say how many went with it.
 */
export interface ReviewGapResult {
  gap_id: string;
  topic: string;
  decision: "dismiss" | "reopen";
  from_status: GapStatus;
  to_status: GapStatus;
  reason: string | null;
  decided_at: string;
  question: string;
  changed: boolean;
  commit_sha: string;
  cascaded_suggestion_ids: string[];
}

/** ``gap_report``'s result -- the flat Tier-1 tool Answer's ``react`` stage calls for "Report gap". */
export interface GapReportResult {
  topic: string;
  gap_id: string;
  qa_id: string;
  question: string;
  fault_class: GapFaultClass;
  status: GapStatus;
  origin: GapOrigin;
  reason: string;
  reference_pages: string[];
  written: boolean;
  duplicate: boolean;
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

/**
 * The verbs `suggestions_review` accepts, matching the server's own `_ACTIONS`
 * tuple. `withdraw` returns an *approved* suggestion to `pending` while
 * asserting no ingest happened — the undo the approve queue offers on a row it
 * has just decided, and the reason this union is not merely the three triage
 * verbs.
 */
export type SuggestionAction =
  "approve" | "reject" | "defer" | "mark_ingested" | "withdraw";

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
// The handoff stage (`INTERFACE_DESIGN.md §3`) --
// `fill(action="session_status")`'s read contract.
// ---------------------------------------------------------------------------

export type SessionState =
  | "not_started"
  | "waiting_on_client"
  | "client_wrote"
  | "rework_in_flight"
  | "submitted"
  | "merged"
  | "refused"
  | "blocked"
  | "swept";

export type SessionNextActor = "you" | "claude" | "system" | "none";

/**
 * The gate's verdict on a session's candidate, as ``session_status`` reports
 * it -- a narrower shape than the stored ``GateOutcome`` (no ``ref``/
 * ``regressed_questions``).
 */
export interface SessionGateOutcome {
  verdict: GateOutcomeVerdict;
  scalar: number;
  baseline_scalar: number;
  /** Present on ``refused`` only. */
  reason?: string;
}
/** ``fill(action="session_status")``'s wire contract -- the handoff stage's one read. */
export interface SessionStatus {
  suggestion_id: string;
  stage: string;
  stage_index: number;
  state: SessionState;
  source_present: boolean;
  pages_present: string[];
  index_synced: boolean;
  gate_eligible: boolean;
  gate_eligible_reason: string;
  restored_from: string | null;
  gate_outcome: SessionGateOutcome | null;
  next: { actor: SessionNextActor; do: string };
}
