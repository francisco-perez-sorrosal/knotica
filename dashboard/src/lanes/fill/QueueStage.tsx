import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import { Spinner } from "../../icons";
import { TwoPhaseConfirm, useTwoPhaseAction } from "../../TwoPhaseAction";
import type { ToolClient } from "../../toolClient";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import type { ProcessId } from "../processMeta";
import { GapOriginBadge } from "./badges";
import { QueueToolbar } from "./QueueToolbar";
import { SuggestionRow } from "./SuggestionRow";
import { mergeGhosts } from "./suggestionSort";
import type { GhostRow, QueueSortMode } from "./suggestionSort";
import type {
  GapfillDiscoverResult,
  GapRecord,
  GapsReadResult,
  LaneRailStageState,
  SuggestionAction,
  SuggestionRecord,
  SuggestionsReadResult,
  SuggestionsStatusFilter,
  SuggestionStatus,
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
 * A decided row never vanishes. The reload after a verdict re-reads under the
 * active filter, so the record it just decided is absent from the new payload;
 * the row is therefore snapshotted first and re-rendered in its own slot as a
 * *ghost* stating what became of it (an approval keeps a quiet Withdraw, the
 * one reversal that spends nothing). Without this the only feedback for a
 * click was a row disappearing from a list of dozens of near-identical rows --
 * indistinguishable from nothing having happened.
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

/**
 * Where each triage verb leaves the record. `withdraw` is deliberately absent:
 * it returns the suggestion to `pending`, so the reload carries it back as a
 * live row and its ghost is dropped rather than restated.
 */
const DECIDED_STATUS: Partial<Record<SuggestionAction, SuggestionStatus>> = {
  approve: "approved",
  reject: "rejected",
  defer: "deferred",
};

/** How the live region opens its sentence, per verb. */
const DECISION_ANNOUNCEMENT: Partial<Record<SuggestionAction, string>> = {
  approve: "Approved",
  reject: "Rejected",
  defer: "Deferred",
  withdraw: "Withdrawn, back to pending",
};

/**
 * Which registered process each triage verb runs. One client method backs all
 * four -- the verb is an argument, not a separate call -- but they are four
 * processes because they answer four different questions and leave four
 * different things owed.
 *
 * Partial for the same reason `DECIDED_STATUS` is: `mark_ingested` is a
 * machine transition the gate path stamps, not a control this queue offers.
 */
const DECISION_PROCESS: Partial<Record<SuggestionAction, ProcessId>> = {
  approve: "fill.suggestion_approve",
  reject: "fill.suggestion_reject",
  defer: "fill.suggestion_defer",
  withdraw: "fill.suggestion_withdraw",
};

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
  const [busy, setBusy] = useState<{ id: string; action: SuggestionAction } | null>(null);
  /**
   * Rows decided in this session that the current filter no longer returns.
   * Held so a decision *transforms* its row instead of deleting it: among
   * dozens of near-identical candidates a vanishing row is indistinguishable
   * from a click that did nothing, which is the confusion this exists to fix.
   */
  const [ghosts, setGhosts] = useState<Map<string, GhostRow>>(new Map());
  /** What the live region last said; the only outcome a screen reader gets. */
  const [announcement, setAnnouncement] = useState("");
  /**
   * Which verb produced that sentence, so the outcome can also say what the
   * decision leaves owed. Held in the stage, not in the row: the row it was
   * clicked on becomes a ghost and is re-rendered from a fresh read, so an
   * outcome parked there would vanish at the moment it needed reading.
   */
  const [decidedProcess, setDecidedProcess] = useState<ProcessId | null>(null);
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

  /** Returns the fresh payload so a caller can announce against its counts. */
  async function load(cursor = "", append = false): Promise<SuggestionsReadResult | null> {
    if (!client || !topic) return null;
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
      return next;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setLoading(false);
    }
  }

  /**
   * Ghosts are session-local to one *context*: a filter, topic or vault switch
   * is a deliberate change of what the reader is looking at, and carrying rows
   * decided under the old context into the new one would be a lie about what
   * the queue contains. Appending a page and re-sorting are not context
   * changes, so neither clears them.
   */
  useEffect(() => {
    setGhosts(new Map());
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault, filter]);

  function refresh() {
    setGhosts(new Map());
    void load();
  }

  // Not keyed on `filter` -- that selects a *suggestion* status and says
  // nothing about which gaps to show.
  useEffect(() => {
    void loadGaps();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault]);

  /**
   * Apply one verdict, then keep the row on screen saying what became of it.
   *
   * The reload re-reads under the active filter, so a decided suggestion is
   * simply absent from the new payload. Snapshotting it *before* the reload is
   * what turns a silent deletion into a visible transformation; `index` is the
   * slot it occupied in the list that was clicked in, so it comes back exactly
   * there rather than at the end of a fifty-row queue.
   */
  async function decide(
    suggestionId: string,
    action: SuggestionAction,
    index: number,
    reason = "",
  ) {
    if (!client || busy) return;
    const snapshot = suggestions.find((row) => row.suggestion_id === suggestionId);
    setBusy({ id: suggestionId, action });
    setError(null);
    try {
      await client.suggestionsReview(topic, suggestionId, action, "apply", reason, vault);
      setRejectOpenId(null);
      setReasonDraft((prev) => {
        const next = { ...prev };
        delete next[suggestionId];
        return next;
      });
      setGhosts((prev) => nextGhosts(prev, suggestionId, action, index, reason, snapshot));
      const [fresh] = await Promise.all([load(), onStatusRefresh?.()]);
      setAnnouncement(announce(action, snapshot, fresh));
      setDecidedProcess(DECISION_PROCESS[action] ?? null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  const declared = status?.topics.find((row) => row.topic === topic)?.lanes?.fill ?? [];
  const byId = new Map(declared.map((stage) => [stage.id, stage] as const));
  const stateOf = (id: StageId): LaneRailStageState => byId.get(id)?.state ?? "pending";

  // Re-sorted on every render, including after "Load more" appends -- the
  // ordering is a view over whatever is loaded, never a one-shot decision.
  // Ghosts join the same list through the same ordering, so a decided row
  // holds its slot instead of jumping anywhere.
  const loaded = result?.suggestions ?? [];
  const suggestions = mergeGhosts(loaded, [...ghosts.values()], sort);
  const loadedIds = new Set(loaded.map((row) => row.suggestion_id));
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
            server drains by count (max_gaps), not by gap id. The brief is a
            sibling of it, so `Discover sources…` keeps its accessible name and
            the two-phase quote is untouched. */}
        <ProcessBrief process="fill.gapfill_discover" term="why discover" />
        <button
          type="button"
          class="ghost"
          disabled={!client || discoverBusy !== null || discover.state.preview !== null}
          aria-busy={discoverBusy === "preview" || undefined}
          onClick={previewDiscover}
        >
          {discoverBusy === "preview" ? (
            <>
              <Spinner />
              Checking…
            </>
          ) : (
            "Discover sources…"
          )}
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
        {/* The drained/staged counts above are the outcome; this adds the one
            thing they never said -- that a staged suggestion is a proposal
            nobody has accepted yet, and where accepting it happens. */}
        {discover.state.outcome ? (
          <ProcessOutcome process="fill.gapfill_discover" />
        ) : null}
      </StageShell>

      <StageShell id="approve" state={stateOf("approve")} position={3}>
        <p class="muted">
          Ranked sources discovered for diagnosed knowledge gaps. Approve queues an ingest
          instruction for the next interactive session; reject requires a reason.
        </p>

        {/* The four verbs explained once for the queue, not once per row: the
            rows are identical in this respect and run to dozens, and a row
            sized for scanning cannot carry four explanations without burying
            the two numbers the decision actually turns on. */}
        <div class="process-brief-row">
          <ProcessBrief process="fill.suggestion_approve" term="why queue it" />
          <ProcessBrief process="fill.suggestion_reject" term="why turn it down" />
          <ProcessBrief process="fill.suggestion_defer" term="why park it" />
          <ProcessBrief process="fill.suggestion_withdraw" term="why undo it" />
        </div>

        <QueueToolbar
          filter={filter}
          onFilter={setFilter}
          counts={counts}
          allCount={allCount}
          sort={sort}
          onSort={setSort}
          loading={loading}
          onRefresh={refresh}
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
            {suggestions.map((suggestion, index) => (
              <SuggestionRow
                key={suggestion.suggestion_id}
                suggestion={suggestion}
                busyAction={busy?.id === suggestion.suggestion_id ? busy.action : null}
                anyBusy={busy !== null}
                ghost={!loadedIds.has(suggestion.suggestion_id)}
                rejectOpen={rejectOpenId === suggestion.suggestion_id}
                reasonDraft={reasonDraft[suggestion.suggestion_id] ?? ""}
                onApprove={() => void decide(suggestion.suggestion_id, "approve", index)}
                onDefer={() => void decide(suggestion.suggestion_id, "defer", index)}
                onWithdraw={() => void decide(suggestion.suggestion_id, "withdraw", index)}
                onOpenReject={() => setRejectOpenId(suggestion.suggestion_id)}
                onCancelReject={() => setRejectOpenId(null)}
                onReasonChange={(value) =>
                  setReasonDraft((prev) => ({ ...prev, [suggestion.suggestion_id]: value }))
                }
                onSubmitReject={() =>
                  void decide(
                    suggestion.suggestion_id,
                    "reject",
                    index,
                    reasonDraft[suggestion.suggestion_id] ?? "",
                  )
                }
              />
            ))}
          </ul>
        )}

        {/* The only outcome a screen reader gets: the row's transformation is
            silent, and `status_counts` moves without any focus change.

            Keyed on purpose. Its preceding sibling alternates between a `<ul>`
            and a bare `<p>` ("Loading suggestions…"), and an unkeyed `<p>` here
            is a diff match for that one -- Preact would recycle this very node
            into the loading line and take the announcement with it.

            It stays mounted from first paint and stays the only live region
            here. A region inserted into the DOM in the same commit as its text
            is not reliably announced -- which is why this one has always
            rendered empty rather than conditionally -- so the outcome below
            deliberately adds no second one: these four verbs report themselves
            through this sentence and the ghost row, and what they were missing
            was never the outcome but the follow-up. */}
        <p key="queue-live" class="sr-only" role="status">
          {announcement}
        </p>

        {decidedProcess ? <ProcessOutcome process={decidedProcess} /> : null}

        {result?.has_more ? (
          <button
            type="button"
            class="ghost sources-load-more"
            disabled={loading}
            aria-busy={loading || undefined}
            onClick={() => void load(result.next_cursor, true)}
          >
            {loading ? (
              <>
                <Spinner />
                Loading…
              </>
            ) : (
              "Load more"
            )}
          </button>
        ) : null}
      </StageShell>
    </>
  );
}

/**
 * The ghost map after one applied verdict.
 *
 * A verdict that decides the record stamps a snapshot -- status, decision time
 * and reason -- so the row can state its own outcome without a second read. A
 * `withdraw` decides nothing: it puts the record back in the pending queue, so
 * its ghost is dropped and the reload's live row takes over.
 */
function nextGhosts(
  prev: Map<string, GhostRow>,
  suggestionId: string,
  action: SuggestionAction,
  index: number,
  reason: string,
  snapshot?: SuggestionRecord,
): Map<string, GhostRow> {
  const next = new Map(prev);
  const landed = DECIDED_STATUS[action];
  if (!landed || !snapshot) {
    next.delete(suggestionId);
    return next;
  }
  next.set(suggestionId, {
    index,
    record: {
      ...snapshot,
      status: landed,
      decided_at: new Date().toISOString(),
      decided_reason: reason || null,
    },
  });
  return next;
}

/** One sentence: what was decided, on what, and what the queue holds now. */
function announce(
  action: SuggestionAction,
  snapshot: SuggestionRecord | undefined,
  fresh: SuggestionsReadResult | null,
): string {
  const verb = DECISION_ANNOUNCEMENT[action] ?? "Decided";
  const title = snapshot?.candidate.title ?? "suggestion";
  const pending = fresh?.status_counts.pending;
  const remaining = pending == null ? "" : ` — ${pending} pending remaining`;
  return `${verb}: ${title}${remaining}`;
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
    <li class="lane-stage" data-state={state} data-anchor={`fill:${id}`}>
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
