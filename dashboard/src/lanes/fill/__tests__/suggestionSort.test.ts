import { describe, expect, it } from "vitest";

import { sortSuggestions } from "../suggestionSort";
import type { SuggestionRecord, SuggestionReputability } from "../types";

/**
 * The comparator's edge cases, away from the DOM: two unrated records must
 * still order deterministically (an infinite sentinel would subtract to NaN
 * and hand the engine an undefined comparator), and sorting must never
 * mutate the array `QueueStage` holds in state.
 */

function record(
  id: string,
  rank: number,
  reputability: SuggestionReputability | null,
): SuggestionRecord {
  return {
    suggestion_id: id,
    rank,
    candidate: { reputability },
  } as unknown as SuggestionRecord;
}

const ids = (rows: SuggestionRecord[]): string[] => rows.map((row) => row.suggestion_id);

describe("sortSuggestions", () => {
  it("falls back to rank when neither record carries a reputability block", () => {
    const rows = [record("b", 4, null), record("a", 2, null)];
    expect(ids(sortSuggestions(rows, "priority"))).toEqual(["a", "b"]);
  });

  it("ranks any scored record above every unscored one, however low the score", () => {
    const rows = [
      record("unrated", 1, null),
      record("barely", 9, { tier: "general_web", score: 0.01, signals: [] }),
    ];
    expect(ids(sortSuggestions(rows, "priority"))).toEqual(["barely", "unrated"]);
  });

  it("returns a new array and leaves the caller's own order untouched", () => {
    const rows = [
      record("low", 1, { tier: "general_web", score: 0.1, signals: [] }),
      record("high", 2, { tier: "peer_reviewed", score: 0.9, signals: [] }),
    ];
    const sorted = sortSuggestions(rows, "priority");

    expect(sorted).not.toBe(rows);
    expect(ids(rows)).toEqual(["low", "high"]);
    expect(ids(sorted)).toEqual(["high", "low"]);
  });

  it("hands back the server's order verbatim under 'newest'", () => {
    const rows = [
      record("second", 2, { tier: "general_web", score: 0.1, signals: [] }),
      record("first", 1, { tier: "peer_reviewed", score: 0.9, signals: [] }),
    ];
    expect(ids(sortSuggestions(rows, "newest"))).toEqual(["second", "first"]);
  });
});
