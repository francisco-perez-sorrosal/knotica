import type {
  AttentionRow,
  AttentionStatus,
  AttentionTopicRow,
} from "../../types";

/**
 * `deriveAttentionRows` -- the pure grouping function behind `HomeLane`
 * (`INTERFACE_DESIGN.md §2.1`, `dec-092`). Turns the cross-topic
 * `wiki_status(view="attention")` payload into a flat list of actionable
 * rows: one per independent signal, never one per topic -- a topic that is
 * simultaneously blocked and waiting on you surfaces two rows, not one. A
 * quiet topic (every signal false) contributes zero rows -- "not rendered"
 * is the fourth, correct urgency class.
 *
 * Routing (signal → lane, both suggestion-lifecycle signals land on `fill`;
 * both Improve-folded signals land on `improve`, per `INTERFACE_DESIGN.md
 * §2.4`): `refused_awaiting_rework` → blocked/fill, `pending` →
 * waiting/fill, `compile_ready` → waiting/improve, `runner.alive` →
 * running/improve. `action` is `"Watch"` only for `running` rows, `"Open"`
 * otherwise.
 */
export function deriveAttentionRows(payload: AttentionStatus): AttentionRow[] {
  return payload.topics.flatMap(rowsForTopic);
}

function rowsForTopic(topic: AttentionTopicRow): AttentionRow[] {
  const rows: AttentionRow[] = [];

  if (topic.suggestions.refused_awaiting_rework > 0) {
    rows.push({
      topic: topic.topic,
      lane: "fill",
      urgency: "blocked",
      narration: `${topic.suggestions.refused_awaiting_rework} suggestion(s) refused, awaiting rework.`,
      action: "Open",
    });
  }

  if (topic.suggestions.pending > 0) {
    rows.push({
      topic: topic.topic,
      lane: "fill",
      urgency: "waiting",
      narration: `${topic.suggestions.pending} suggestion(s) pending review.`,
      action: "Open",
    });
  }

  if (topic.compile_ready) {
    rows.push({
      topic: topic.topic,
      lane: "improve",
      urgency: "waiting",
      narration: "Ready to compile.",
      action: "Open",
    });
  }

  if (topic.runner.alive) {
    rows.push({
      topic: topic.topic,
      lane: "improve",
      urgency: "running",
      narration: "A loop runner is active.",
      action: "Watch",
    });
  }

  return rows;
}
