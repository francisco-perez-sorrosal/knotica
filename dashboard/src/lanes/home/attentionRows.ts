import type {
  AttentionRow,
  AttentionStatus,
  AttentionTopicRow,
  AttentionUrgency,
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
 * otherwise. Each row also carries its own `kind` -- one per branch below,
 * never derived from `urgency`/`lane` (two branches share `waiting`, two
 * share `fill`) -- so `attentionMeta.ts` can attach a rationale per signal
 * rather than per urgency class.
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
      kind: "refused_rework",
      narration: `${topic.suggestions.refused_awaiting_rework} suggestion(s) refused, awaiting rework.`,
      action: "Open",
    });
  }

  if (topic.suggestions.pending > 0) {
    rows.push({
      topic: topic.topic,
      lane: "fill",
      urgency: "waiting",
      kind: "pending_suggestions",
      narration: `${topic.suggestions.pending} suggestion(s) pending review.`,
      action: "Open",
    });
  }

  if (topic.compile_ready) {
    rows.push({
      topic: topic.topic,
      lane: "improve",
      urgency: "waiting",
      kind: "compile_ready",
      narration: "Ready to compile.",
      action: "Open",
    });
  }

  if (topic.runner.alive) {
    rows.push({
      topic: topic.topic,
      lane: "improve",
      urgency: "running",
      kind: "runner_active",
      narration: "A loop runner is active.",
      action: "Watch",
    });
  }

  return rows;
}

/** Display priority per urgency class -- lower sorts first. */
const URGENCY_RANK: Record<AttentionUrgency, number> = {
  blocked: 0,
  waiting: 1,
  running: 2,
};

/**
 * Orders `deriveAttentionRows`'s flat output for display: blocked outranks
 * waiting outranks running -- stopped pipelines first, then things awaiting
 * a decision, then things merely running unattended. Stable within a class
 * (`Array.prototype.sort` is spec-guaranteed stable since ES2019), so a
 * cross-topic interleave keeps each class in the derivation's own topic
 * order rather than re-sorting by topic name. Returns a new array; never
 * mutates `rows`.
 */
export function sortAttentionRows(rows: AttentionRow[]): AttentionRow[] {
  return [...rows].sort(
    (a, b) => URGENCY_RANK[a.urgency] - URGENCY_RANK[b.urgency],
  );
}
