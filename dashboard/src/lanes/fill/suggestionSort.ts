import type { SuggestionRecord } from "./types";

/**
 * Approve-queue ordering.
 *
 * `newest` is the server's own order, returned verbatim -- `suggestions_read`
 * pages by proposal recency, so this mode is "whatever the wire said".
 *
 * `priority` re-orders the *loaded* page(s) so the sources most worth a
 * human's attention surface first: reputability score descending, ties broken
 * by the discovery `rank` ascending. Records carrying no reputability block at
 * all sort last -- absence is not a low score, and ranking an unmeasured
 * candidate against measured ones would invent a comparison the data does not
 * support.
 *
 * The sort is deliberately client-side and therefore *page-local*: the read is
 * cursor-paginated, so priority order holds across the records currently
 * loaded, not across the whole queue. `QueueStage` says so on screen whenever
 * `has_more` is true rather than implying a global ranking it cannot deliver.
 */
export type QueueSortMode = "priority" | "newest";

/** Compares two records under `priority`; exported for the row-order tests. */
export function comparePriority(a: SuggestionRecord, b: SuggestionRecord): number {
  const scoreA = a.candidate.reputability?.score;
  const scoreB = b.candidate.reputability?.score;
  // Null-last, without an infinite sentinel: `-Infinity - -Infinity` is NaN,
  // and a comparator that returns NaN has undefined sort behaviour.
  if (scoreA == null && scoreB != null) return 1;
  if (scoreB == null && scoreA != null) return -1;
  if (scoreA != null && scoreB != null && scoreA !== scoreB) return scoreB - scoreA;
  return a.rank - b.rank;
}

/** Returns a new array -- never mutates the loaded page held in state. */
export function sortSuggestions(
  rows: readonly SuggestionRecord[],
  mode: QueueSortMode,
): SuggestionRecord[] {
  if (mode === "newest") return [...rows];
  return [...rows].sort(comparePriority);
}
