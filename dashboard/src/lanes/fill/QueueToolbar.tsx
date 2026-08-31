import type { JSX } from "preact";

import { Icon, Spinner } from "../../icons";
import { TermHint } from "../../TermHint";
import type {
  SuggestionsReadResult,
  SuggestionsStatusFilter,
} from "../../types";
import type { QueueSortMode } from "./suggestionSort";

/** Labels are deliberately never "approved" -- that text collides with the
 * accessible name of the row's own "Approve" action button in this same
 * stage (both would match a `/approve/i` role query), so this filter option
 * is worded to avoid the substring entirely. */
const FILTERS: Array<{ value: SuggestionsStatusFilter; label: string }> = [
  { value: "pending", label: "pending" },
  { value: "approved", label: "accepted" },
  { value: "all", label: "all" },
];

const SORT_MODES: Array<{ value: QueueSortMode; label: string }> = [
  { value: "priority", label: "priority" },
  { value: "newest", label: "newest" },
];

/**
 * The approve queue's single toolbar: what is listed (left), what came of the
 * listed records (right), and in what order (below).
 *
 * The outcome chips read `status` (a synchronous prop) for `refused` and the
 * suggestions read for `ingested`, so the topic-wide count renders on first
 * paint rather than waiting on a page-local fetch. There is deliberately no
 * `approved` chip: the ACCEPTED filter pill already prints that exact number
 * from the same `status_counts.approved` field.
 */
export function QueueToolbar({
  filter,
  onFilter,
  counts,
  allCount,
  sort,
  onSort,
  loading,
  onRefresh,
  refusedCount,
  pageIsPartial,
  loadedCount,
}: {
  filter: SuggestionsStatusFilter;
  onFilter: (value: SuggestionsStatusFilter) => void;
  counts: SuggestionsReadResult["status_counts"] | undefined;
  allCount: number | null;
  sort: QueueSortMode;
  onSort: (value: QueueSortMode) => void;
  loading: boolean;
  onRefresh: () => void;
  refusedCount: number;
  pageIsPartial: boolean;
  loadedCount: number;
}): JSX.Element {
  const pillCount = (value: SuggestionsStatusFilter): string => {
    if (value === "all") return allCount == null ? "" : ` ${allCount}`;
    return counts ? ` ${counts[value]}` : "";
  };

  return (
    <div class="queue-toolbar">
      <div class="queue-toolbar-row">
        <div class="queue-pills" role="group" aria-label="Filter by status">
          {FILTERS.map((entry) => (
            <button
              type="button"
              key={entry.value}
              class="queue-pill"
              aria-pressed={filter === entry.value}
              onClick={() => onFilter(entry.value)}
            >
              {entry.label}
              {pillCount(entry.value)}
            </button>
          ))}
          <button
            type="button"
            class="queue-icon-button"
            aria-label="Refresh"
            disabled={loading}
            aria-busy={loading || undefined}
            onClick={onRefresh}
          >
            {/* The glyph the reader just clicked is the one that turns. */}
            {loading ? <Spinner /> : <Icon name="refresh" size={16} />}
          </button>
        </div>

        <div
          class="queue-outcomes"
          role="group"
          aria-label="Suggestion counts by outcome"
        >
          <span class="chip" data-tone="warn">
            <span aria-hidden="true">⚠</span>{" "}
            <TermHint
              id="fill-outcome-refused"
              term={`refused ${refusedCount}`}
              title="Refused at the gate"
              body="Approved suggestions whose most recent gate pass was refused — re-workable, not re-submitted. Counted topic-wide, not just across the rows on screen."
              align="end"
            />
          </span>
          {counts ? (
            <span class="chip">
              <TermHint
                id="fill-outcome-ingested"
                term={`ingested ${counts.ingested}`}
                title="Ingested"
                body="Suggestions whose source has been written into the vault and indexed. Nothing further is owed on them."
                align="end"
              />
            </span>
          ) : null}
        </div>
      </div>

      <div class="queue-toolbar-row queue-toolbar-sub">
        <div class="queue-sort" role="group" aria-label="Sort order">
          <span class="microlabel">Sort</span>
          {SORT_MODES.map((mode) => (
            <button
              type="button"
              key={mode.value}
              class="queue-pill"
              aria-pressed={sort === mode.value}
              onClick={() => onSort(mode.value)}
            >
              {mode.label}
            </button>
          ))}
        </div>
        {sort === "priority" && pageIsPartial ? (
          <p class="muted queue-sort-note">
            Sorted across the {loadedCount} loaded — Load more to widen.
          </p>
        ) : null}
      </div>
    </div>
  );
}
