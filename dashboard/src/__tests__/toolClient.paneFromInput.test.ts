import { describe, expect, it } from "vitest";

import { paneFromToolInput } from "../toolClient";

/**
 * `open_dashboard`'s `lane`/`focus` arguments, read from a bridge
 * `ontoolinput` payload exactly as `topicFromToolInput`/`vaultFromToolInput`
 * already read `topic`/`vault` (`App.tsx`'s `ontoolinput` handler calls all
 * three the same way). Only the reading + resolution seam is pinned here --
 * the pane resolution logic itself (`resolveLaneFocus`) has its own coverage
 * in `paneRouting.lanes.test.ts`.
 */
describe("paneFromToolInput reads lane/focus from a synthetic ontoolinput payload", () => {
  it("resolves a lane+focus pair to that lane's own pane", () => {
    const input = { arguments: { lane: "improve", focus: "heal" } };
    expect(paneFromToolInput(input, "tend")).toBe("improve");
  });

  it("resolves a bare lane with no focus to that lane's own mapping", () => {
    const input = { arguments: { lane: "fill" } };
    expect(paneFromToolInput(input, "tend")).toBe("sources");
  });

  it("keeps the caller's fallback pane when the payload carries no lane at all", () => {
    expect(paneFromToolInput({ arguments: {} }, "ingest")).toBe("ingest");
  });

  it("keeps the caller's fallback pane when a focus is present but its lane is not", () => {
    // A stray focus with no accompanying lane is not enough to navigate --
    // it must not be treated as though it qualified the fallback pane.
    const input = { arguments: { focus: "heal" } };
    expect(paneFromToolInput(input, "ingest")).toBe("ingest");
  });

  it("keeps the caller's fallback pane when the input is not a record at all", () => {
    expect(paneFromToolInput(null, "ask")).toBe("ask");
  });

  it("degrades an unrecognised lane to the default pane, not to the caller's fallback", () => {
    // Per dec-092, an unknown lane must degrade rather than error -- and it
    // degrades to the routing default, distinct from whatever pane the app was
    // already showing when the host sent the (bad) lane value.
    const input = { arguments: { lane: "not-a-real-lane" } };
    expect(paneFromToolInput(input, "ingest")).toBe("tend");
  });

  it("falls through to the lane's own mapping when the lane is known but the focus is not", () => {
    const input = { arguments: { lane: "improve", focus: "not-a-real-focus" } };
    expect(paneFromToolInput(input, "tend")).toBe("improve");
  });
});
