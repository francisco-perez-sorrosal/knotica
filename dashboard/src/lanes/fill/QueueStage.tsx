import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import { Icon } from "../../icons";
import { TermHint } from "../../TermHint";
import { TwoPhaseConfirm, useTwoPhaseAction } from "../../TwoPhaseAction";
import type { ToolClient } from "../../toolClient";
import { GapOriginBadge } from "./badges";
import { SuggestionRow } from "./SuggestionRow";
import { sortSuggestions } from "./suggestionSort";
import type { QueueSortMode } from "./suggestionSort";
import type {
  GapfillDiscoverResult,
  GapRecord,
  GapsReadResult,
  LaneRailStageState,
  SuggestionAction,
  SuggestionsReadResult,
  SuggestionsStatusFilter,
  WikiStatus,
} from "../../types";

/**
 * `QueueStage` -- Fill's `gap`/`discover`/`approve` rail stages: the diagnosed
 * gap queue, the billed two-phase discovery drain, and the human triage queue
 * the drain feeds.
 *
 * Content is **not** gated by a stage's own `data-state` (no progressive
 * disclosure, unlike `ImproveLane`): this content has always rendered
 * unconditionally and must not be hidden behind a new visibility gate.
 * `data-state` is a wrapper attribute only, sourced verbatim from
 * `status.topics[].lanes.fill` (server-derived, `core/status_lanes.py`) and
 * defaulted to `"pending"` when absent.
 *
 * The approve stage is the dense one. Its queue routinely runs to dozens of
 * records, so it carries one toolbar (filters + counts on the left, outcome
 * chips on the right, sort order underneath) and renders each suggestion as a
 * collapsed `SuggestionRow` -- see that file for the row grammar. Ordering is
 * client-side over the loaded pages, which the toolbar says out loud whenever
 * the read has more to give.
 *
 * `review_gap` (the human dismiss/reopen transition on a gap) is out of scope
 * here: it is a flat MCP tool already registered, not a dashboard affordance.
 */

/** The server's "no cap" sentinel for a drain -- it decides how many gaps to take. */
const DISCOVER_ALL_GAPS = 0;

/**
 * One read usually covers the whole queue, so priority order is usually the
 * whole queue's order rather than a page's. Raised from 20 once the rows
 * became collapsed triage rows: a 50-row page is now shorter on screen than
 * a 20-card page was.
 */
const SUGGESTIONS_PAGE_SIZE = 50;

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

const STAGE_ORDER = ["gap", "discover", "approve"] as const;
type StageId = (typeof STAGE_ORDER)[number];

const STAGE_TITLE: Record<StageId, string> = {
  gap: "Gap",
  discover: "Discover",
  approve: "Approve",
};

function stageGlyph(state: LaneRailStageState, position: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(position);
}

export function QueueStage({
  client,
  topic,
  vault,
  status,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status?: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const [filter, setFilter] = useState<SuggestionsStatusFilter>("pending");
  const [sort, setSort] = useState<QueueSortMode>("priority");
  const [result, setResult] = useState<SuggestionsReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reasonDraft, setReasonDraft] = useState<Record<string, string>>({});
  const [rejectOpenId, setRejectOpenId] = useState<string | null>(null);
  const [gaps, setGaps] = useState<GapsReadResult | null>(null);
  const [gapsError, setGapsError] = useState<string | null>(null);
  /** Billed and two-phase; the preview quotes, only an explicit second click drains. */
  const discover = useTwoPhaseAction<GapfillDiscoverResult>({
    preview: () => client!.gapfillDiscover(topic, DISCOVER_ALL_GAPS, "", vault),
    confirm: async (nonce, quoted) => {
      const done = await client!.gapfillDiscover(
        topic,
        quoted.max_gaps ?? DISCOVER_ALL_GAPS,
        nonce,
        vault,
      );
      // Both queues moved: gaps may have drained, suggestions may have appeared.
      await Promise.all([load(), loadGaps(), onStatusRefresh?.()]);
      return done;
    },
    onError: setGapsError,
  });
  const discoverBusy = discover.state.busy;

  function previewDiscover() {
    if (!client || discoverBusy) return;
    setGapsError(null);
    void discover.preview();
  }

  async function loadGaps() {
    if (!client || !topic) return;
    setGapsError(null);
    try {
      setGaps(await client.gapsRead(topic, "open", "", 20, vault));
    } catch (cause) {
      // Kept off `error` on purpose: gaps and suggestions are independent
      // queues read by independent calls, and the pane stays useful with
      // either one. Folding this into the suggestions error would let a gaps
      // failure blank a suggestions list that loaded perfectly well.
      setGapsError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function load(cursor = "", append = false) {
    if (!client || !topic) return;
    setLoading(!append);
    setError(null);
    try {
      const next = await client.suggestionsRead(
        topic,
        filter,
        cursor,
        SUGGESTIONS_PAGE_SIZE,
        vault,
      );
      setResult((prev) =>
        append && prev
          ? { ...next, suggestions: [...prev.suggestions, ...next.suggestions] }
          : next,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault, filter]);

  // Not keyed on `filter` -- that selects a *suggestion* status and says
  // nothing about which gaps to show.
  useEffect(() => {
    void loadGaps();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault]);

  async function decide(suggestionId: string, action: SuggestionAction, reason = "") {
    if (!client || busyId) return;
    setBusyId(suggestionId);
    setError(null);
    try {
      await client.suggestionsReview(topic, suggestionId, action, "apply", reason, vault);
      setRejectOpenId(null);
      setReasonDraft((prev) => {
        const next = { ...prev };
        delete next[suggestionId];
        return next;
      });
      await Promise.all([load(), onStatusRefresh?.()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }

  const declared = status?.topics.find((row) => row.topic === topic)?.lanes?.fill ?? [];
  const byId = new Map(declared.map((stage) => [stage.id, stage] as const));
  const stateOf = (id: StageId): LaneRailStageState => byId.get(id)?.state ?? "pending";

  // Re-sorted on every render, including after "Load more" appends -- the
  // ordering is a view over whatever is loaded, never a one-shot decision.
  const suggestions = sortSuggestions(result?.suggestions ?? [], sort);
  const counts = result?.status_counts;
  // Filter-independent: `status_counts` covers every status, so the total is
  // its sum -- `total_count` describes only the *current* filter's slice.
  const allCount = counts
    ? Object.values(counts).reduce((total, value) => total + value, 0)
    : null;
  const openGaps = gaps?.gaps ?? [];
  const gapCount = gaps?.total_count ?? openGaps.length;
  const gapsPageIsPartial = gaps?.has_more ?? false;
  // Single-sourced from wiki_status (topic-wide), not a page-local recount --
  // avoids undercounting refused suggestions outside the current filter/page.
  const refusedCount =
    status?.topics.find((entry) => entry.topic === topic)?.suggestions?.refused_awaiting_rework ??
    0;

  return (
    <>
      <StageShell id="gap" state={stateOf("gap")} position={1}>
        <p class="muted">
          Diagnosed and waiting for source discovery -- there is nothing to approve on them yet.
        </p>
        {gapsError ? (
          <p class="sources-error" role="alert">
            Open gaps could not be loaded: {gapsError}
          </p>
        ) : null}
        {openGaps.length > 0 ? (
          <>
            <h4 class="sources-gaps-head">
              Open gaps · {gapCount}
              {gapsPageIsPartial ? ` (showing ${openGaps.length})` : ""}
            </h4>
            <ul class="sources-list">
              {openGaps.map((gap) => (
                <GapCard key={gap.gap_id} gap={gap} />
              ))}
            </ul>
          </>
        ) : (
          <p class="muted">No open gaps right now.</p>
        )}
      </StageShell>

      <StageShell id="discover" state={stateOf("discover")} position={2}>
        <p class="muted">
          Searches for candidate sources for open gaps; each one that ranks becomes a row in the
          Approve stage below.
        </p>
        {/* Deliberately one control for every open gap, not one per row: the
            server drains by count (max_gaps), not by gap id. */}
        <button
          type="button"
          class="ghost"
          disabled={!client || discoverBusy !== null || discover.state.preview !== null}
          onClick={previewDiscover}
        >
          {discoverBusy === "preview" ? "Checking…" : "Discover sources…"}
        </button>

        {discover.state.preview ? (
          <TwoPhaseConfirm
            busy={discoverBusy}
            busyLabel="Searching"
            extraClass="sources-discover-confirm"
            disabled={
              !client || discoverBusy !== null || !discover.state.preview.provider_configured
            }
            onConfirm={discover.confirm}
            onCancel={discover.reset}
          >
            {discover.state.preview.provider_configured ? (
              <>
                Would search for <strong>{discover.state.preview.would_drain}</strong> of{" "}
                <strong>{discover.state.preview.open_gaps}</strong> open gap
                {discover.state.preview.open_gaps === 1 ? "" : "s"} —{" "}
                {discover.state.preview.estimated_cost}. This has NOT billed yet; confirm to run
                and bill.
              </>
            ) : (
              <>
                No search provider is configured, so this would stage nothing. Set{" "}
                <code>KNOTICA_YOUCOM_API_KEY</code> and try again.
              </>
            )}
          </TwoPhaseConfirm>
        ) : null}

        {discover.state.outcome ? (
          <p class="muted sources-partial-note">
            Discovery drained {discover.state.outcome.gaps_drained} of{" "}
            {discover.state.outcome.gaps_considered} gap
            {discover.state.outcome.gaps_considered === 1 ? "" : "s"} and staged{" "}
            {discover.state.outcome.suggestions_staged} suggestion
            {discover.state.outcome.suggestions_staged === 1 ? "" : "s"}.
            {discover.state.outcome.suggestions_staged === 0
              ? " Nothing ranked — the gap stays open."
              : ""}
          </p>
        ) : null}
      </StageShell>

      <StageShell id="approve" state={stateOf("approve")} position={3}>
        <p class="muted">
          Ranked sources discovered for diagnosed knowledge gaps. Approve queues an ingest
          instruction for the next interactive session; reject requires a reason.
        </p>

        <QueueToolbar
          filter={filter}
          onFilter={setFilter}
          counts={counts}
          allCount={allCount}
          sort={sort}
          onSort={setSort}
          loading={loading}
          onRefresh={() => void load()}
          refusedCount={refusedCount}
          pageIsPartial={result?.has_more ?? false}
          loadedCount={suggestions.length}
        />

        {error ? (
          <p class="sources-error" role="alert">
            {error}
          </p>
        ) : null}

        {result && result.skipped_malformed > 0 ? (
          <p class="muted sources-partial-note">
            {result.skipped_malformed} suggestion record{result.skipped_malformed === 1 ? "" : "s"}{" "}
            were malformed and skipped.
          </p>
        ) : null}

        {loading && suggestions.length === 0 ? (
          <p class="muted">Loading suggestions…</p>
        ) : suggestions.length === 0 ? (
          <div class="sources-empty">
            <p>No gap-fill suggestions yet.</p>
            {gapCount > 0 ? (
              // The state that used to be indistinguishable from "nothing has
              // happened": gaps exist, discovery has not run, so the queue this
              // list reads is legitimately empty. Say which step is outstanding
              // rather than sending the reader off to manufacture a new gap.
              <p class="muted">
                {gapCount === 1 ? "1 gap is" : `${gapCount} gaps are`} already open above, waiting
                on discovery — run <code>knotica gapfill discover --topic {topic}</code> to search
                for sources.
              </p>
            ) : (
              <p class="muted">
                The loop writes suggestions here after it diagnoses a <code>genuine_gap</code> and
                discovery finds ranked sources. To exercise it: freeze a golden question the vault
                lacks, regress, let the loop classify, then run{" "}
                <code>knotica gapfill discover --topic {topic}</code>.
              </p>
            )}
          </div>
        ) : (
          <ul class="triage-list">
            {suggestions.map((suggestion) => (
              <SuggestionRow
                key={suggestion.suggestion_id}
                suggestion={suggestion}
                busy={busyId === suggestion.suggestion_id}
                anyBusy={busyId !== null}
                rejectOpen={rejectOpenId === suggestion.suggestion_id}
                reasonDraft={reasonDraft[suggestion.suggestion_id] ?? ""}
                onApprove={() => void decide(suggestion.suggestion_id, "approve")}
                onDefer={() => void decide(suggestion.suggestion_id, "defer")}
                onOpenReject={() => setRejectOpenId(suggestion.suggestion_id)}
                onCancelReject={() => setRejectOpenId(null)}
                onReasonChange={(value) =>
                  setReasonDraft((prev) => ({ ...prev, [suggestion.suggestion_id]: value }))
                }
                onSubmitReject={() =>
                  void decide(
                    suggestion.suggestion_id,
                    "reject",
                    reasonDraft[suggestion.suggestion_id] ?? "",
                  )
                }
              />
            ))}
          </ul>
        )}

        {result?.has_more ? (
          <button
            type="button"
            class="ghost sources-load-more"
            disabled={loading}
            onClick={() => void load(result.next_cursor, true)}
          >
            {loading ? "Loading…" : "Load more"}
          </button>
        ) : null}
      </StageShell>
    </>
  );
}

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
function QueueToolbar({
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
            onClick={onRefresh}
          >
            <Icon name="refresh" size={16} />
          </button>
        </div>

        <div class="queue-outcomes" role="group" aria-label="Suggestion counts by outcome">
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

/** The shared `.lane-stage` shell every row of this queue renders through --
 * matches `TendLane.tsx`'s `StageShell` markup contract exactly, so the two
 * absorbed lanes stay visually consistent under `FillLane`'s shared rail. */
function StageShell({
  id,
  state,
  position,
  children,
}: {
  id: StageId;
  state: LaneRailStageState;
  position: number;
  children: JSX.Element | Array<JSX.Element | null>;
}): JSX.Element {
  return (
    <li class="lane-stage" data-state={state}>
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(state, position)}
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{STAGE_TITLE[id]}</strong>
          <span class="lane-state-label muted">{state}</span>
        </div>
        <div class="lane-stage-body">{children}</div>
      </div>
    </li>
  );
}

/**
 * One diagnosed gap, before discovery has found anything for it.
 *
 * Deliberately shows no generation and no scalar. Both are constant zeros on a
 * `reported` or `retracted` gap -- placeholders, never measurements -- and
 * rendering `gen-0` next to a hand-filed gap presents a filler value as a
 * finding. What the reader needs here is what was asked and why it went
 * unanswered.
 */
function GapCard({ gap }: { gap: GapRecord }): JSX.Element {
  return (
    <li class="sources-card sources-gap-card">
      <div class="sources-card-head">
        <span class="status-chip">
          {gap.fault_class} · filed {gap.detected_at.slice(0, 10)}
        </span>
        <span class="sources-card-badges">
          <GapOriginBadge origin={gap.origin} />
        </span>
      </div>

      <div class="sources-card-question">
        <span class="stat-label">Unanswered question</span>
        <p>“{gap.question}”</p>
        {gap.reference_pages.length > 0 ? (
          <p class="muted">references: {gap.reference_pages.join(", ")}</p>
        ) : null}
      </div>

      {gap.reported_reason ? (
        <div class="sources-card-source">
          <span class="stat-label">Why the wiki fell short</span>
          <p class="muted">{gap.reported_reason}</p>
        </div>
      ) : null}
    </li>
  );
}
