import { describe, expect, it } from "vitest";

import { PANE_BY_PARAM, resolveLaneFocus, resolvePane } from "../paneRouting";
import type { PaneId } from "../types";

/**
 * `?pane=` routing extended with the six process lanes.
 *
 * Two things are pinned here, and only two:
 *
 * 1. Each lane value is now an accepted `?pane=` value, resolving to the
 *    pane that best represents it today. This mapping is not itself an
 *    architectural decision recorded anywhere — no ADR or design doc names
 *    which existing pane a lane opens — so it is derived here from the
 *    project's own pane-to-lane research findings (the "comes from today"
 *    column) rather than invented from scratch. If a later decision picks a
 *    different representative pane for a lane, this is the one place that
 *    changes.
 * 2. The case/whitespace rule decided for this extension applies uniformly
 *    to both the legacy pane keys and the new lane keys: exact match, no
 *    trimming, no case folding. A caller who mistypes the case, or pastes a
 *    value with surrounding whitespace, falls back to the default pane --
 *    the same behaviour the original allowlist already had for every other
 *    unrecognised value.
 *
 * `PANE_BY_PARAM` itself is asserted directly (not only through
 * `resolvePane`) because the alias map needed to become an export for a
 * consumer to exist at all -- `resolvePane` alone cannot show that a lane
 * key was actually added to the allowlist rather than merely happening to
 * fall through to the same default a lane key would get if it were still
 * unrecognised.
 */

const LANE_TARGET_PANE: Readonly<Record<string, PaneId>> = {
  home: "tend",
  learn: "learn",
  answer: "answer",
  improve: "improve",
  fill: "fill",
  tend: "tend",
};

describe("the six process lanes as accepted ?pane= values", () => {
  it.each(Object.entries(LANE_TARGET_PANE))(
    "resolves the %s lane to its representative pane",
    (lane, pane) => {
      expect(resolvePane(lane)).toBe(pane);
    },
  );

  it("adds all six lanes to the allowlist, not just resolvePane's fallback", () => {
    for (const lane of Object.keys(LANE_TARGET_PANE)) {
      expect(PANE_BY_PARAM.has(lane)).toBe(true);
    }
  });

  it("keeps every lane's mapped target inside PANE_BY_PARAM's own values", () => {
    // A lane key must resolve to a pane the map itself recognises as a
    // destination -- not a value that exists only in this test's table.
    for (const pane of Object.values(LANE_TARGET_PANE)) {
      expect(new Set(PANE_BY_PARAM.values()).has(pane)).toBe(true);
    }
  });
});

describe("case and whitespace are exact-match, uniformly for legacy and lane keys", () => {
  it.each(["LOOP", "Loop", " loop", "loop "])(
    "does not resolve a mistyped legacy key like %j",
    (mistyped) => {
      expect(resolvePane(mistyped)).toBe("tend");
    },
  );

  it.each(["IMPROVE", "Improve", " improve", "improve "])(
    "does not resolve a mistyped lane key like %j",
    (mistyped) => {
      expect(resolvePane(mistyped)).toBe("tend");
    },
  );

  it("does not fold case for a lane key that would collide with a legacy key's casing", () => {
    // `answer` and `ask` are distinct concepts on two different surfaces;
    // this case guards against a lane key being accidentally case-insensitive
    // while the legacy allowlist stays case-sensitive.
    expect(resolvePane("ANSWER")).toBe("tend");
  });
});

/**
 * `open_dashboard(lane=, focus=)` carries a second axis `?pane=` never had.
 * It no longer selects a *pane*: the three pairs that used to open a
 * tool-shaped pane instead of the lane's own (`improve:heal`,
 * `improve:instrument`, `tend:drift`) all pointed at panes the lanes have
 * since absorbed, so a focus is now a within-lane coordinate — which stage
 * expands — and every lane+focus pair lands on the lane itself.
 *
 * Degrade-never-error (dec-092) still governs every branch here: a focus
 * never surfaces as `undefined`, and an unrecognised `lane` never throws, it
 * degrades to the same default pane `resolvePane` already falls back to.
 */
describe("focus-qualified lane resolution (resolveLaneFocus)", () => {
  it.each([
    ["improve", "heal", "improve"],
    ["improve", "instrument", "improve"],
    ["tend", "drift", "tend"],
  ] as const)(
    "resolves the %s lane with %s focus to the %s lane's own pane, not a retired one",
    (lane, focus, pane) => {
      expect(resolveLaneFocus(lane, focus)).toBe(pane);
    },
  );

  it("falls through to the lane's own plain mapping when focus is the empty string", () => {
    // No focus at all is the common case (a bare `open_dashboard(lane="improve")`).
    expect(resolveLaneFocus("improve", "")).toBe("improve");
  });

  it("falls through to the lane's own plain mapping when focus does not match any documented case", () => {
    // A focus value that simply doesn't exist for this lane must not surface
    // as undefined -- it degrades to what a bare lane, with no focus, would give.
    expect(resolveLaneFocus("improve", "not-a-real-focus")).toBe("improve");
  });

  it("falls through to the lane's own plain mapping for a lane with no documented focus case at all", () => {
    // "fill" never appears in the qualified table; any focus value on it
    // must still land on "fill", the lane's own unqualified mapping.
    expect(resolveLaneFocus("fill", "whatever")).toBe("fill");
  });

  it("does not let a focus value meaningful under one lane leak into another lane's resolution", () => {
    // "heal" is an Improve stage. Under "learn" it must still land on learn's
    // own mapping ("learn") -- a focus never redirects across lanes.
    expect(resolveLaneFocus("learn", "heal")).toBe("learn");
  });

  it("degrades an unrecognised lane to the default pane regardless of focus", () => {
    expect(resolveLaneFocus("not-a-real-lane", "")).toBe("tend");
    expect(resolveLaneFocus("not-a-real-lane", "heal")).toBe("tend");
  });
});
