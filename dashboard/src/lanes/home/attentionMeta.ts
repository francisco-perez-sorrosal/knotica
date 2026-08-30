import type { LaneAnchor } from "../../paneRouting";
import type { AttentionKind } from "../../types";

export interface AttentionKindMeta {
  /** Why this row is queued -- the cause, grounded in server semantics. */
  why: string;
  /** What acting on the row unfolds -- the consequence of resolving it. */
  unlocks: string;
  /**
   * Where the row's `[Open]`/`[Watch]` button lands -- the *stage* that holds
   * the control this row is about, not merely the lane that contains it.
   *
   * This is the registry half of the navigation contract on Home's side (the
   * other half is `PROCESS_META`'s `next` anchors): every anchor is validated
   * against `LANE_STAGES` by `attentionMeta.test.ts`, so a row can never route
   * to a stage `core/process_model.py` does not declare. Two kinds sharing one
   * anchor is correct, not a smell -- they are two different reasons to stand
   * in the same place.
   */
  anchor: LaneAnchor;
}

/**
 * Dashboard-owned rationale copy per `AttentionRow.kind` (the `laneMeta.ts`
 * precedent: presentation-only text the server payload does not carry,
 * keyed by a union so a missing or extra kind is a compile error).
 * `AttentionTable`'s per-row `TermHint` renders `${why} ${unlocks}` as two
 * sentences.
 *
 * Grounded against the live server/dashboard seam, not guessed:
 * - `refused_rework` -- `core/status_lanes.py::is_refused`/`_refusal_reason`
 *   ("rework and resubmit") and `mcp_server/tools_suggestions.py`'s real
 *   `withdraw` action (returns an approved suggestion to `pending` without
 *   asserting an ingest).
 * - `pending_suggestions` -- `QueueStage.tsx`'s own stage copy ("Approve
 *   queues an ingest instruction for the next interactive session; reject
 *   requires a reason").
 * - `compile_ready` -- `core/status.py::_is_compile_ready` (trainset and
 *   golden floors) plus the Instrument → Heal → Gate → Promote chain
 *   `docs/dashboard.md` describes.
 * - `runner_active` -- `AttentionTopicRow.runner.alive`, the same signal
 *   `deriveAttentionRows` reads for this row's `action: "Watch"`.
 */
export const ATTENTION_KIND_META: Record<AttentionKind, AttentionKindMeta> = {
  refused_rework: {
    why: "The gate refused an approved source, so that gap's pipeline is stopped; nothing downstream moves until a human reworks or withdraws it.",
    unlocks:
      "Reworking or withdrawing releases the queue: the source can be re-gated or the gap re-discovered.",
    // The refusal verdict is what needs reading, and Gate is where it is written.
    anchor: { lane: "fill", stage: "gate" },
  },
  pending_suggestions: {
    why: "Discovery already ran and ranked sources sit unreviewed; ingest cannot start without your approve or reject.",
    unlocks:
      "Approving queues an ingest instruction for the next interactive session, moving the gaps those sources answer toward closed.",
    anchor: { lane: "fill", stage: "approve" },
  },
  compile_ready: {
    why: "The trainset and golden set have both crossed the compile floor, so a better prompt is available but not yet built.",
    unlocks:
      "Compiling writes a candidate branch; if it clears the gate baseline it merges and this topic's answers improve.",
    // The compile control lives in Heal, not in Instrument, which only feeds it.
    anchor: { lane: "improve", stage: "heal" },
  },
  runner_active: {
    why: "The loop is working unattended; this row is here so you can watch it, not because it needs you.",
    unlocks:
      "Nothing to do — it will surface a waiting or blocked row the moment a human is needed.",
    // Watching means the trend and the runner-liveness chip, both in Observe.
    anchor: { lane: "improve", stage: "observe" },
  },
};
