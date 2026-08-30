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

/**
 * A row the reader has just decided, held on screen after the server dropped
 * it from the filtered payload.
 *
 * `index` is the position it occupied in the list that was clicked in. Under
 * `priority` it is unused -- the comparator re-derives the same slot from the
 * snapshot's own score and rank -- but `newest` is the wire's order and has no
 * comparator to re-derive anything from, so the remembered position is the
 * only anchor that keeps the row where the eye left it.
 */
export interface GhostRow {
  record: SuggestionRecord;
  index: number;
}

/**
 * The rendered list: the loaded page plus every ghost the page no longer
 * carries, each in the slot it held before it was decided.
 *
 * A ghost whose id is back in the payload (a withdrawn approval, a re-read
 * that returned it) is dropped -- the live record always wins over a snapshot
 * of it.
 */
export function mergeGhosts(
  loaded: readonly SuggestionRecord[],
  ghosts: readonly GhostRow[],
  mode: QueueSortMode,
): SuggestionRecord[] {
  const present = new Set(loaded.map((row) => row.suggestion_id));
  const absent = ghosts.filter((ghost) => !present.has(ghost.record.suggestion_id));
  if (absent.length === 0) return sortSuggestions(loaded, mode);
  if (mode === "priority") {
    return sortSuggestions([...loaded, ...absent.map((ghost) => ghost.record)], mode);
  }
  const rows = [...loaded];
  // Ascending, so splicing an earlier ghost cannot shift a later one's anchor.
  for (const ghost of [...absent].sort((a, b) => a.index - b.index)) {
    rows.splice(Math.min(ghost.index, rows.length), 0, ghost.record);
  }
  return rows;
}
