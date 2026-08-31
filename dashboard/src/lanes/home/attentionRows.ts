import type {
  AttentionRow,
  AttentionStatus,
  AttentionTopicRow,
  AttentionUrgency,
} from "../../types";

/**
 * `deriveAttentionRows` -- the pure grouping function behind `HomeLane`
 * (`dec-092`). Turns the cross-topic
 * `wiki_status(view="attention")` payload into a flat list of actionable
 * rows: one per independent signal, never one per topic -- a topic that is
 * simultaneously blocked and waiting on you surfaces two rows, not one. A
 * quiet topic (every signal false) contributes zero rows -- "not rendered"
 * is the fourth, correct urgency class.
 *
 * Routing (signal → lane, every gap-queue signal lands on `fill`; every
 * Improve-folded signal lands on `improve`):
 * `refused_awaiting_rework` → blocked/fill, `pending` → waiting/fill, open
 * gaps with no discovery → waiting/fill, open gaps the vault already answers
 * → waiting/fill, an aborted race → blocked/improve,
 * `gate.baseline_unreachable` → blocked/improve, `compile_ready` →
 * waiting/improve, `runner.alive` → running/improve.
 * `action` is `"Watch"` only for `running` rows, `"Open"` otherwise. Each row
 * also carries its own `kind` -- one per branch below, never derived from
 * `urgency`/`lane` (four branches share `waiting`, four share `fill`) -- so
 * `attentionMeta.ts` can attach a rationale *and a destination stage* per
 * signal rather than per urgency class.
 *
 * The three later branches close the surface holes that made Home lie. A topic
 * with open gaps and no suggestions tripped nothing, so "nothing needs you"
 * rendered over a stalled queue; an arena race refused before scoring was
 * visible only to someone already standing in Improve → Heal on that topic;
 * and a gate baseline above the corpus's own scalar jammed every candidate
 * with nothing on Home to say so. All three are now signals the server sends
 * and this function reads.
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

  // Optionally read: a server whose `attention` view predates these two fields
  // still renders a working inbox, one signal short, rather than throwing on
  // the first topic and blanking Home entirely. `App.tsx` reads the summary
  // view's own `gaps` block the same way and for the same reason.
  const openGaps = topic.gaps?.open_total ?? 0;
  const suggestionsEverProposed = topic.suggestions?.total ?? 0;

  // Deliberately conservative: this fires only when *nothing* has ever been
  // proposed for the topic, so it cannot false-positive on a topic mid-pipeline.
  // Its known false negative -- three open gaps and one old suggestion hides two
  // of them -- is the price. A conservative signal that is always right when it
  // fires beats a clever one that cries wolf; the attention view's own doctrine
  // is that a wrong answer is worse than an absent one.
  if (openGaps > 0 && suggestionsEverProposed === 0) {
    rows.push({
      topic: topic.topic,
      lane: "fill",
      urgency: "waiting",
      kind: "gaps_awaiting_discovery",
      narration: `${openGaps} open gap(s), no discovery run yet.`,
      action: "Open",
    });
  }

  // The drain already looked and found nothing new to acquire: every source it
  // could reach for these gaps is stored in the vault already. Read optionally
  // for the same back-compat reason as the two fields above -- a server
  // predating the stamp renders an inbox one signal short, never a broken one.
  const answeredGaps = topic.gaps?.answered_in_vault ?? 0;
  if (answeredGaps > 0) {
    rows.push({
      topic: topic.topic,
      lane: "fill",
      urgency: "waiting",
      kind: "gaps_answered_in_vault",
      narration: `${answeredGaps} open gap(s) whose sources the vault already stores — retrieval or linking, not acquisition.`,
      action: "Open",
    });
  }

  // `blocked`, not `waiting`: an aborted race is a stopped pipeline, which is
  // exactly what the rank-0 class means. `aborted` is distinct from `reverted`
  // on purpose -- that word already means "raced and nobody won", which is a
  // normal terminal state needing nobody.
  if (topic.arena?.stage === "aborted") {
    rows.push({
      topic: topic.topic,
      lane: "improve",
      urgency: "blocked",
      kind: "arena_aborted",
      narration: "A prompt race was refused before scoring.",
      action: "Open",
    });
  }

  // A bar above what the default branch itself measures fails every candidate
  // and every arena variant by construction -- the pipeline is jammed, not
  // merely waiting, which is why this ranks `blocked`. The server withholds
  // the finding (null) when it cannot honestly assert it (cross-instrument,
  // probe anchor, no eval yet), so a rendered row is always a real jam.
  const unreachable = topic.gate?.baseline_unreachable ?? null;
  if (unreachable) {
    rows.push({
      topic: topic.topic,
      lane: "improve",
      urgency: "blocked",
      kind: "baseline_unreachable",
      narration: `Gate baseline ${unreachable.baseline.toFixed(4)} is above the corpus's own ${unreachable.last_scalar.toFixed(4)} — nothing can pass.`,
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
