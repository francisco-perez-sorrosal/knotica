import type { IconName } from "../icons";
import type { PaneId } from "../types";

/**
 * How `LoopStrip` draws a lane's rail: `cycle` closes with a
 * return arc (the only such lane today is `improve` -- prove loops back to
 * instrument); `line` is a straight sequence with no arc; `checks` renders
 * independent peer chips with no track at all, matching
 * `laneRailState.ts::deriveChecklistStages`'s "independent peers, no
 * watermark" semantics. `home` carries no rail (`LANE_STAGES.home` is
 * empty) -- its `shape` is present only for `LANE_META`'s own exhaustiveness
 * and is never read.
 */
export type LaneShape = "cycle" | "line" | "checks";

export interface LaneMeta {
  icon: IconName;
  /** One-line description rendered on the Home lane card. */
  blurb: string;
  shape: LaneShape;
}

/**
 * Per-lane presentation copy (orchestrator disposition:
 * "adopt dashboard-local for blurb, icon AND shape"). `process_model.py` is
 * the single source of truth for the lane *census* (`processModel.ts`'s
 * `LANES`); this module supplies presentation-only copy that census does not
 * carry, keyed by the same `PaneId` union so a missing or extra lane is a
 * compile error. The `laneMeta.test.ts` census test asserts
 * `Object.keys(LANE_META)` equals `LANES` at runtime too, since `PaneId` and
 * `LANES` are independently declared (one a TS union, one a generated JS
 * array) and only a test can catch the two drifting apart.
 */
export const LANE_META: Record<PaneId, LaneMeta> = {
  home: {
    icon: "lane:home",
    blurb: "What needs you, across every topic.",
    shape: "line",
  },
  learn: {
    icon: "lane:learn",
    blurb: "Read the wiki and grow it.",
    shape: "line",
  },
  answer: {
    icon: "lane:answer",
    blurb: "Ask, cite, react -- the answer loop.",
    shape: "line",
  },
  improve: {
    icon: "lane:improve",
    blurb: "Measure, heal, prove the loop.",
    shape: "cycle",
  },
  fill: {
    icon: "lane:fill",
    blurb: "Find gaps and fill them.",
    shape: "line",
  },
  tend: {
    icon: "lane:tend",
    blurb: "Keep the vault healthy.",
    shape: "checks",
  },
};
