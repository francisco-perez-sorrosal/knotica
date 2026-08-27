/**
 * Fill's half of `ToolClient`: the gap-fill queue and its handoff.
 *
 * Reads the two queues the lane triages (suggestions, gaps), runs the one
 * billed discovery call, applies a review verdict, and polls the handoff
 * stage's session. `gap_report` is *not* here -- gaps are reported from
 * Answer's `react` stage and only land in this lane's queue.
 */

import { LLM_CALL_TIMEOUT_MS, type ToolCallGroup } from "../../toolClientCore";

import type {
  GapfillDiscoverResult,
  GapsReadResult,
  GapsStatusFilter,
  SessionStatus,
  SuggestionAction,
  SuggestionReviewResult,
  SuggestionsReadResult,
  SuggestionsStatusFilter,
} from "./types";

export interface FillToolCalls {
  suggestionsRead(
    topic: string,
    status?: SuggestionsStatusFilter,
    cursor?: string,
    limit?: number,
    vault?: string,
  ): Promise<SuggestionsReadResult>;
  gapsRead(
    topic: string,
    status?: GapsStatusFilter,
    cursor?: string,
    limit?: number,
    vault?: string,
  ): Promise<GapsReadResult>;
  gapfillDiscover(
    topic: string,
    maxGaps?: number,
    confirm?: string,
    vault?: string,
  ): Promise<GapfillDiscoverResult>;
  suggestionsReview(
    topic: string,
    suggestionId: string,
    action: SuggestionAction,
    mode: "dry-run" | "apply",
    reason?: string,
    vault?: string,
  ): Promise<SuggestionReviewResult>;
  sessionStatus(
    topic: string,
    suggestionId: string,
    vault?: string,
  ): Promise<SessionStatus>;
}

export const fillToolCalls: ToolCallGroup<FillToolCalls> = {
  suggestionsRead(
    topic: string,
    status: SuggestionsStatusFilter = "pending",
    cursor = "",
    limit = 20,
    vault = "",
  ): Promise<SuggestionsReadResult> {
    return this.call("suggestions_read", {
      topic,
      status,
      cursor,
      limit,
      vault,
    });
  },

  gapsRead(
    topic: string,
    status: GapsStatusFilter = "open",
    cursor = "",
    limit = 20,
    vault = "",
  ): Promise<GapsReadResult> {
    return this.call("gaps_read", { topic, status, cursor, limit, vault });
  },

  /** Billed and two-phase: omit `confirm` to preview, pass the returned nonce to run. */
  gapfillDiscover(
    topic: string,
    maxGaps = 0,
    confirm = "",
    vault = "",
  ): Promise<GapfillDiscoverResult> {
    return this.call(
      "gapfill_discover",
      { topic, max_gaps: maxGaps, confirm, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  suggestionsReview(
    topic: string,
    suggestionId: string,
    action: SuggestionAction,
    mode: "dry-run" | "apply" = "dry-run",
    reason = "",
    vault = "",
  ): Promise<SuggestionReviewResult> {
    return this.call("suggestions_review", {
      topic,
      suggestion_id: suggestionId,
      action,
      mode,
      reason,
      vault,
    });
  },

  /** The handoff stage's one read (`INTERFACE_DESIGN.md §3.3`). */
  sessionStatus(
    topic: string,
    suggestionId: string,
    vault = "",
  ): Promise<SessionStatus> {
    return this.call("fill", {
      action: "session_status",
      topic,
      suggestion_id: suggestionId,
      vault,
    });
  },
};
