import { describe, expect, it } from "vitest";

import {
  DEFAULT_PANE,
  PANE_BY_PARAM,
  resolveLaneFocus,
  resolvePane,
} from "../paneRouting";
import type { PaneId } from "../types";

/**
 * The **routing contract** half of the Home wave, pinned durably now that the
 * wiring has landed. `m5HomeCensus.test.tsx` already pinned the *target
 * state* (the `PaneId` union, `App.tsx`'s Home tab and lane mount, the
 * cross-lane-navigation census) as a one-shot backstop that shrank to green
 * across three steps; this file is the permanent, standalone home for the
 * routing function-level contract those census assertions leaned on, mirroring
 * the house pattern every earlier wave established
 * (`paneRouting.improveTend.test.ts`, `paneRouting.learnAnswerFill.test.ts`,
 * `crossLaneLinkCensus.test.ts`'s own additive-only groups).
 *
 * One thing this file does differently from its siblings: the "every legacy
 * key still resolves to its documented target" regression is **derived from
 * `PANE_BY_PARAM` itself** (`[...PANE_BY_PARAM.entries()]`), not hand-listed.
 * A hand-listed table silently stops covering a key the moment a future wave
 * adds one and forgets to update every census that hand-copied the list; a
 * table built from the map's own entries covers whatever the map says on the
 * day the suite runs, by construction.
 *
 * `App.tsx` renders through Preact, and this file intentionally has a `.ts`
 * (not `.tsx`) extension -- `vitest.config.ts`'s "unit" project runs
 * `.test.ts` files under Node with no DOM, mirroring `paneRouting.test.ts`
 * next to it. No `App.tsx` source-scan lives here: the render-order claims
 * (`HomeLane` mounts first, the `Home` tab is first in nav order) are already
 * covered by `m5HomeCensus.test.tsx` group (d) and by the implementer's own
 * recorded verification; duplicating a source-text scan here would just be a
 * second, weaker copy of that same check.
 */

describe("a bare deep link (no ?pane=, no ?lane=) opens Home", () => {
  it("resolvePane(null) resolves to home", () => {
    expect(resolvePane(null)).toBe("home" as PaneId);
  });

  it("DEFAULT_PANE is itself home, which is what resolvePane(null) falls back to", () => {
    expect(DEFAULT_PANE).toBe("home" as PaneId);
  });
});

describe("both deep-link forms that name Home explicitly reach it", () => {
  it("?pane=home resolves to home", () => {
    expect(resolvePane("home")).toBe("home" as PaneId);
  });

  it("?lane=home (the open_dashboard MCP deep-link form) resolves to home through the same map", () => {
    expect(resolveLaneFocus("home", "")).toBe("home" as PaneId);
  });
});

describe("every ?pane= key in the allowlist resolves to its own documented target", () => {
  // Built from the map's own entries, not hand-listed -- see file header.
  const ENTRIES: ReadonlyArray<readonly [string, PaneId]> = [
    ...PANE_BY_PARAM.entries(),
  ];

  it("the allowlist is non-empty, so this census is not vacuously passing", () => {
    expect(ENTRIES.length).toBeGreaterThan(0);
  });

  it.each(ENTRIES)("?pane=%s resolves to %s", (param, pane) => {
    expect(resolvePane(param)).toBe(pane);
  });

  it("resolveLaneFocus agrees with resolvePane for every allowlisted key", () => {
    for (const [param, pane] of ENTRIES) {
      expect(resolveLaneFocus(param, "")).toBe(pane);
    }
  });
});

describe("an explicit resolution always wins over the default, never gets silently overridden", () => {
  const NON_HOME_ENTRIES: ReadonlyArray<readonly [string, PaneId]> = [
    ...PANE_BY_PARAM.entries(),
  ].filter(([, pane]) => pane !== DEFAULT_PANE);

  it("at least one allowlisted key resolves somewhere other than the default", () => {
    // Guards against the assertion below passing vacuously if every key ever
    // collapsed onto home.
    expect(NON_HOME_ENTRIES.length).toBeGreaterThan(0);
  });

  it.each(NON_HOME_ENTRIES)(
    "?pane=%s resolves to %s, not to the default pane",
    (param, pane) => {
      expect(resolvePane(param)).toBe(pane);
      expect(resolvePane(param)).not.toBe(DEFAULT_PANE);
    },
  );

  it("home is a real, present allowlist entry -- not merely an absent key that happens to fall through to a same-valued default", () => {
    // The hazard this guards against already bit once: an earlier default
    // move exposed a legacy key (`vault`) that resolved correctly only
    // because its target coincided with the then-current default, with
    // nothing distinguishing "matched in the allowlist" from "fell through."
    // `home` now shares that exact coincidence with the default it names, so
    // this checks the map directly rather than through resolvePane, where
    // the two cases would be indistinguishable.
    expect(PANE_BY_PARAM.has("home")).toBe(true);
    expect(PANE_BY_PARAM.get("home")).toBe("home" as PaneId);
  });
});

describe("home resolution is exact-match, mirroring PANE_BY_PARAM's own documented contract", () => {
  const MISTYPED_VARIANTS = ["Home", "HOME", " home", "home ", "hOme"];

  it.each(MISTYPED_VARIANTS)(
    "%s is not a key in the allowlist -- no trimming, no case folding",
    (variant) => {
      expect(PANE_BY_PARAM.has(variant)).toBe(false);
    },
  );

  it.each(MISTYPED_VARIANTS)(
    "resolvePane(%s) falls back to the default rather than matching home loosely",
    (variant) => {
      expect(resolvePane(variant)).toBe(DEFAULT_PANE);
    },
  );

  it("resolveLaneFocus is exact-match for home too, reading the same map", () => {
    expect(resolveLaneFocus("Home", "")).toBe(DEFAULT_PANE);
    expect(PANE_BY_PARAM.has("Home")).toBe(false);
  });
});
