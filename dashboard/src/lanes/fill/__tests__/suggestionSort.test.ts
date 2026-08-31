import { describe, expect, it } from "vitest";

import { mergeGhosts, sortSuggestions } from "../suggestionSort";
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

describe("mergeGhosts anchors each ghost against the list it is spliced into", () => {
  const live = (id: string, rank: number): SuggestionRecord =>
    record(id, rank, null);

  it("holds two stacked ghosts in the slots the reader last saw them in", () => {
    // `loaded` is the ghost-free payload; the anchors index THAT list, so
    // `g-1` sits before `c` and `g-3` before `e` no matter which lands first.
    const loaded = [live("a", 1), live("b", 2), live("c", 3), live("d", 4), live("e", 5)];
    const ghosts = [
      { index: 2, record: live("g-1", 90) },
      { index: 4, record: live("g-2", 91) },
    ];

    expect(ids(mergeGhosts(loaded, ghosts, "newest"))).toEqual([
      "a",
      "b",
      "g-1",
      "c",
      "d",
      "g-2",
      "e",
    ]);
  });

  it("drops a ghost the payload carries again -- the live record always wins", () => {
    const loaded = [live("a", 1), live("b", 2)];
    const ghosts = [{ index: 0, record: live("b", 99) }];

    expect(ids(mergeGhosts(loaded, ghosts, "newest"))).toEqual(["a", "b"]);
  });
});
