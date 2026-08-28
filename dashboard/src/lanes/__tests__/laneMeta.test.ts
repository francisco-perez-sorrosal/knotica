import { describe, expect, it } from "vitest";

import { LANES } from "../../processModel";
import { LANE_META } from "../laneMeta";

/**
 * `LANE_META` census (design §9 CH-2): its keys must equal `LANES` exactly --
 * no more, no less. `PaneId`'s compile-time exhaustiveness (`Record<PaneId,
 * LaneMeta>`) already prevents a *missing* key from type-checking; this test
 * is the runtime backstop `crossLaneLinkCensus.test.ts`/`m5HomeCensus.test.tsx`
 * already established the need for -- `PaneId` (a hand-written TS union) and
 * `LANES` (a generated JS array from `process_model.py`) are two independent
 * declarations that only a test can catch drifting apart.
 */
describe("LANE_META census", () => {
  it("has exactly the same keys as LANES, no more, no less", () => {
    expect(new Set(Object.keys(LANE_META))).toEqual(new Set(LANES));
    expect(Object.keys(LANE_META)).toHaveLength(LANES.length);
  });
});
