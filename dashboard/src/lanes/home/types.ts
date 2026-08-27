/**
 * Home-lane wire shapes: the cross-topic attention inbox.
 *
 * Re-exported verbatim from `src/types.ts`, so
 * `import type { X } from "../../types"` still resolves.
 */

import type { PaneId } from "../../types";

// ---------------------------------------------------------------------------
// Home's cross-topic attention inbox (`INTERFACE_DESIGN.md §2.1`, `dec-092`) --
// `wiki_status(view="attention")`'s wire contract.
// ---------------------------------------------------------------------------

/** Recognized `wiki_status` `view` values the dashboard calls today -- mirrors
 * `core/status.py::VALID_STATUS_VIEWS`, minus the two views (`scope`,
 * `process_model`) no client surface calls yet. */
export type StatusView = "summary" | "attention";

export interface AttentionSuggestions {
  pending: number;
  refused_awaiting_rework: number;
}

export interface AttentionTopicRow {
  topic: string;
  suggestions: AttentionSuggestions;
  compile_ready: boolean;
  runner: { alive: boolean };
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
   * unconditionally (`INTERFACE_DESIGN.md §4.2` rule 2). */
  drift: { default_collapsed: boolean; count: number | null };
}

export type AttentionUrgency = "blocked" | "waiting" | "running";

/** One actionable row `deriveAttentionRows` emits -- one per independent
 * signal, not one per topic. `lane` routes the row's `[Open]`/`[Watch]`
 * button to the pane that owns the underlying object. */
export interface AttentionRow {
  topic: string;
  lane: PaneId;
  urgency: AttentionUrgency;
  narration: string;
  action: "Open" | "Watch";
}
