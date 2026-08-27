import { describe, expect, it } from "vitest";

import { resolvePane } from "../paneRouting";
import type { PaneId } from "../types";

/**
 * `?pane=` routing, pinned as a pure function of the raw query-param value.
 *
 * These cases are written from the routing contract — the accepted values and
 * the fallback — not from the block they were extracted from. Anything the
 * extraction is free to change (whitespace handling, letter case) is
 * deliberately left unpinned rather than guessed at.
 *
 * The tool-shaped panes this file once enumerated (`vault`, `loop`, `arena`,
 * `datasets`, `notes`, then `ask`, `ingest`, `sources`) dissolved into the
 * process lanes; their keys survive as inbound aliases and are pinned in
 * `crossLaneLinkCensus.test.ts`/`m4DissolutionCensus.test.tsx`, which own the
 * legacy-alias tables. What stays here is what this file has always been
 * about: a self-resolving value opens the pane it names, and an unusable
 * value never throws.
 */

/** Values a caller may pass that name the pane they resolve to. */
const SELF_RESOLVING: readonly PaneId[] = [
  "home",
  "learn",
  "answer",
  "fill",
  "improve",
  "tend",
];

const FALLBACK_PANE: PaneId = "home";

describe("resolving the pane named by the ?pane= query param", () => {
  it.each(SELF_RESOLVING)(
    "opens the %s pane when the param names it",
    (pane) => {
      expect(resolvePane(pane)).toBe(pane);
    },
  );

  it("opens every pane the dashboard can show and never resolves to some other one", () => {
    // A single pass over the accepted set: an implementation that collapsed two
    // panes onto one target would satisfy each case above in isolation but
    // would not produce as many distinct panes as it accepts.
    const resolved = SELF_RESOLVING.map(resolvePane);

    expect(resolved).toEqual([...SELF_RESOLVING]);
    expect(new Set(resolved).size).toBe(SELF_RESOLVING.length);
  });

  it("falls back to the default pane when no pane is named at all", () => {
    expect(resolvePane(null)).toBe(FALLBACK_PANE);
  });

  it("falls back to the default pane when the param is present but empty", () => {
    expect(resolvePane("")).toBe(FALLBACK_PANE);
  });

  it.each(["nope", "compile", "gaps", "settings"])(
    "falls back to the default pane rather than trusting an unrecognised value like %s",
    (unknown) => {
      expect(resolvePane(unknown)).toBe(FALLBACK_PANE);
    },
  );

  it("answers from its argument alone, reading no browser location", () => {
    // This suite runs with no DOM, so a routing module that reached for
    // `window.location` would throw here instead of returning. That is the
    // property that lets the pane rail, deep links and tests share one reader.
    expect(typeof globalThis.window).toBe("undefined");
    expect(resolvePane("improve")).toBe("improve");
    expect(resolvePane("improve")).toBe(resolvePane("improve"));
  });
});
