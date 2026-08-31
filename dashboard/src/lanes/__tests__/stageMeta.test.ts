import { describe, expect, it } from "vitest";

import { LANE_STAGES, LANES } from "../../processModel";
import { STAGE_META, stageMeta } from "../stageMeta";

/**
 * `STAGE_META` census: its keys must equal `LANE_STAGES`
 * exactly, lane by lane and stage by stage. `LANE_STAGES` is generated from
 * `core/process_model.py`, so a stage added, removed, or renamed there must
 * fail here rather than silently render a rail node with no copy behind it.
 * Two lanes declare a stage called `gate`, which is why the keying is
 * lane-then-stage and why a flat stage-id census would not catch drift.
 */
describe("STAGE_META census", () => {
  it("covers exactly the declared lanes, no more, no less", () => {
    expect(new Set(Object.keys(STAGE_META))).toEqual(new Set(LANES));
    expect(Object.keys(STAGE_META)).toHaveLength(LANES.length);
  });

  it.each(LANES)("covers exactly %s's declared stages", (lane) => {
    const declared = LANE_STAGES[lane].map((stage) => stage.id);

    expect(new Set(Object.keys(STAGE_META[lane]))).toEqual(new Set(declared));
    expect(Object.keys(STAGE_META[lane])).toHaveLength(declared.length);
  });

  it.each(LANES)("gives every %s stage both copy slots", (lane) => {
    for (const stage of LANE_STAGES[lane]) {
      const meta = stageMeta(lane, stage.id);
      expect(meta).not.toBeNull();
      expect(meta?.whatThisIs.length).toBeGreaterThan(0);
      expect(meta?.whatToDoNext.length).toBeGreaterThan(0);
    }
  });

  it("returns null for a lane/stage pair the model does not declare", () => {
    expect(stageMeta("improve", "nonesuch")).toBeNull();
    expect(stageMeta("nonesuch", "observe")).toBeNull();
  });
});
