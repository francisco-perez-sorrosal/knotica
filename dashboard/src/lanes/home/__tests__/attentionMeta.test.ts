import { describe, expect, it } from "vitest";

import { LANES, LANE_STAGES } from "../../../processModel";
import { ATTENTION_KIND_META } from "../attentionMeta";

/**
 * `ATTENTION_KIND_META` census -- the `laneMeta.test.ts` precedent applied to
 * `AttentionKind`: `Record<AttentionKind, ...>` already forces compile-time
 * exhaustiveness, but a hand-written literal union and this map are two
 * independent declarations that only a runtime test catches drifting apart.
 */
describe("ATTENTION_KIND_META census", () => {
  const KNOWN_KINDS = [
    "refused_rework",
    "pending_suggestions",
    "gaps_awaiting_discovery",
    "gaps_answered_in_vault",
    "compile_ready",
    "arena_aborted",
    "baseline_unreachable",
    "runner_active",
  ] as const;

  it("has exactly the eight known AttentionRow kinds, no more, no less", () => {
    expect(new Set(Object.keys(ATTENTION_KIND_META))).toEqual(
      new Set(KNOWN_KINDS),
    );
    expect(Object.keys(ATTENTION_KIND_META)).toHaveLength(KNOWN_KINDS.length);
  });

  it.each(KNOWN_KINDS)("%s carries a non-empty why and unlocks", (kind) => {
    const meta = ATTENTION_KIND_META[kind];
    expect(meta.why.length).toBeGreaterThan(0);
    expect(meta.unlocks.length).toBeGreaterThan(0);
  });

  /**
   * The anchor half. Home's `[Open]`/`[Watch]` is the app's most-used piece of
   * navigation, and its destination is data here rather than a literal at the
   * call site -- which is only safe if the data is validated against the same
   * lane/stage model `core/process_model.py` declares. A renamed stage must
   * fail here, not strand a user at the top of a six-stage lane.
   */
  it.each(KNOWN_KINDS)(
    "%s routes to a lane the process model declares",
    (kind) => {
      expect(LANES).toContain(ATTENTION_KIND_META[kind].anchor.lane);
    },
  );

  it.each(KNOWN_KINDS)(
    "%s routes to a stage that lane actually has",
    (kind) => {
      const { lane, stage } = ATTENTION_KIND_META[kind].anchor;
      // Every attention row is about a control, and every control lives in a
      // stage -- a lane-level anchor here would be an admission we do not know
      // where to send the user.
      expect(stage).not.toBeNull();
      expect(LANE_STAGES[lane].map((entry) => entry.id)).toContain(stage);
    },
  );
});
