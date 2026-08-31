/**
 * Home-lane wire shapes: the cross-topic attention inbox.
 *
 * Re-exported verbatim from `src/types.ts`, so
 * `import type { X } from "../../types"` still resolves.
 */

import type { PaneId } from "../../types";

// ---------------------------------------------------------------------------
// Home's cross-topic attention inbox (`dec-092`) --
// `wiki_status(view="attention")`'s wire contract.
// ---------------------------------------------------------------------------

/** Recognized `wiki_status` `view` values the dashboard calls today -- mirrors
 * `core/status.py::VALID_STATUS_VIEWS`, minus the two views (`scope`,
 * `process_model`) no client surface calls yet. */
export type StatusView = "summary" | "attention";

export interface AttentionSuggestions {
  pending: number;
  refused_awaiting_rework: number;
  /** Every suggestion ever recorded for the topic, whatever its status. What
   * lets a client tell "discovery has never run here" apart from "discovery ran
   * and everything it proposed has been dealt with" -- identical through the
   * per-status counts alone. */
  total: number;
}

export interface AttentionTopicRow {
  topic: string;
  suggestions: AttentionSuggestions;
  /** Open gap records, all fault classes. A filed gap with nothing proposed
   * against it is a stalled queue that no other signal reports.
   * `answered_in_vault` counts the open gaps a drain stamped
   * `answered_in_vault_at` — every source it could find for them is already
   * stored, so the fault is retrieval or linking, not acquisition. Optional
   * because a server predating the stamp omits it. */
  gaps: { open_total: number; answered_in_vault?: number };
  compile_ready: boolean;
  runner: { alive: boolean };
  /** The topic's last arena stage, or `null` when no race was ever recorded --
   * "no race we can speak for", never a guessed stage. The server returns the
   * stage word; whether `aborted` needs a human is derived here. */
  arena: { stage: string | null };
  /** `baseline_unreachable` is the server's finding that the frozen gate
   * baseline exceeds what the default branch itself measures -- a bar nothing
   * can clear, so the topic's whole pipeline is jammed. `null` when the bar is
   * reachable (or unknowable: cross-instrument, probe-anchored, unevaluated).
   * Optional so a pre-field server payload still derives every other row. */
  gate?: {
    baseline_unreachable: { baseline: number; last_scalar: number } | null;
  };
}

/** The `view="attention"` payload -- every topic's actionable signals plus
 * vault-level `last_lint` staleness and a drift marker (never a count; see
 * `AttentionRow`'s own docblock for why). */
export interface AttentionStatus {
  schema_version: number;
  vault_name: string;
  topics: AttentionTopicRow[];
  totals: {
    topics: number;
    pending: number;
    refused_awaiting_rework: number;
    compile_ready: number;
    runners_alive: number;
  };
  last_lint: { date: string | null; age_days: number | null; stale: boolean };
  /** Marker, never a count -- resolving drift means resolving every note's
   * anchor, the exact cost the `attention` view exists to avoid paying
   * unconditionally. */
  drift: { default_collapsed: boolean; count: number | null };
}

export type AttentionUrgency = "blocked" | "waiting" | "running";

/** Which of `deriveAttentionRows`'s signal branches produced a row --
 * drives `attentionMeta.ts`'s per-row rationale (why it is queued, what
 * acting on it unfolds). One kind per branch in `rowsForTopic`, never
 * derived from `urgency`/`lane` (four kinds share `waiting`, four share
 * `fill`). */
export type AttentionKind =
  | "refused_rework"
  | "pending_suggestions"
  | "gaps_awaiting_discovery"
  | "gaps_answered_in_vault"
  | "compile_ready"
  | "arena_aborted"
  | "baseline_unreachable"
  | "runner_active";

/** One actionable row `deriveAttentionRows` emits -- one per independent
 * signal, not one per topic. `lane` routes the row's `[Open]`/`[Watch]`
 * button to the pane that owns the underlying object. */
export interface AttentionRow {
  topic: string;
  lane: PaneId;
  urgency: AttentionUrgency;
  kind: AttentionKind;
  narration: string;
  action: "Open" | "Watch";
}
