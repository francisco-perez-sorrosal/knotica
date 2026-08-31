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
 * - `gaps_awaiting_discovery` -- `core/gapfill.py`'s discovery leg, which
 *   turns an open gap into ranked candidate sources; the row exists because
 *   nothing else reports a gap queue that discovery never reached.
 * - `gaps_answered_in_vault` -- `core/gapfill/drain.py`'s
 *   `answered_in_vault_at` stamp, written when a drain finds a gap's entire
 *   candidate yield already stored as vault sources; `GapCard.tsx`'s dismiss
 *   form is the other half of the answer when the gap is simply stale.
 * - `arena_aborted` -- `core/arena.py::ArenaStage.aborted` ("refused before
 *   scoring: the scorer and the baseline are not the same instrument"), and
 *   `HealStage.tsx`'s abort card, whose scorer switch is the fix.
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
  gaps_awaiting_discovery: {
    why: "Gaps are filed against this topic and discovery has never run, so no source has ever been proposed for them — the queue is stalled at its first step.",
    unlocks:
      "Running discovery turns each open gap into ranked candidate sources you can approve, which is what starts an ingest.",
    // Discovery is the stalled step, so Discover is where the user must land.
    anchor: { lane: "fill", stage: "discover" },
  },
  gaps_answered_in_vault: {
    why: "A drain found every source it could for these gaps already stored in the vault, so no discovery run will ever close them — the answer is present and the retrieval or linking to it is what failed.",
    unlocks:
      "Fixing the retrieval path (or the pages' links) makes the stored sources reachable; dismissing the gap is the honest exit when it is simply stale. Either way the gap stops costing a billed search on every drain.",
    // The gap card -- its evidence and its dismiss form -- is what needs
    // reading here; no amount of Discover will help.
    anchor: { lane: "fill", stage: "gap" },
  },
  baseline_unreachable: {
    why: "The frozen gate baseline sits above what the default branch itself measures, so no candidate, source, or arena variant can pass — every refusal blames the content for a shortfall the bar created.",
    unlocks:
      "Rebaselining to the current measurement (loop rebaseline mode=latest) unjams the gate; the Gate stage shows both numbers and the exact command.",
    // GateStage renders the full unreachable alert with the fix; land there.
    anchor: { lane: "improve", stage: "gate" },
  },
  arena_aborted: {
    why: "A prompt race was refused before scoring because the arena scorer and the gate baseline are not the same instrument, so no ranking between them would mean anything.",
    unlocks:
      "Switching the scorer to the gate-comparable one lets the next race actually rank — until then every race is refused the same way.",
    // The abort card and the scorer switch that clears it are both in Heal.
    anchor: { lane: "improve", stage: "heal" },
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
