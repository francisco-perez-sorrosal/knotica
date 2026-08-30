import { describe, expect, it } from "vitest";

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
    "compile_ready",
    "runner_active",
  ] as const;

  it("has exactly the four known AttentionRow kinds, no more, no less", () => {
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
});
