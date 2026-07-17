import { useEffect, useMemo, useRef, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type { IngestActivity, IngestEvent, IngestRun } from "./types";

const STAGE_LABELS: Record<string, string> = {
  resolve_topic: "Topic",
  read_schema: "Schema",
  fetch: "Fetch",
  parse: "Parse",
  plan: "Plan",
  store_source: "Store",
  write_page: "Pages",
  curate: "Curate",
  complete: "Done",
  error: "Error",
};

const INGEST_FALLBACK = [
  "resolve_topic",
  "read_schema",
  "fetch",
  "parse",
  "plan",
  "store_source",
  "write_page",
  "complete",
];
const CURATE_FALLBACK = ["curate", "complete"];

/** Stages that may burst many near-identical events — collapse in the timeline. */
const GROUPABLE = new Set(["store_source", "write_page"]);

type TimelineItem =
  | { kind: "event"; event: IngestEvent }
  | { kind: "group"; stage: string; events: IngestEvent[] };

export function IngestPane({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
}) {
  const [activity, setActivity] = useState<IngestActivity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string>("");
  const timelineRef = useRef<HTMLOListElement>(null);
  const stickToEnd = useRef(true);

  useEffect(() => {
    if (!client) return;
    const active = client;
    let stopped = false;

    async function refresh() {
      try {
        // Always fetch the topic window; filter to the selected/active run client-side
        // so the run list stays populated while the timeline stays focused.
        const payload = await active.ingestActivityRead(topic, vault, "");
        if (!stopped) {
          setActivity(payload);
          setError(null);
        }
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : String(cause));
      }
    }

    void refresh();
    const interval = window.setInterval(() => void refresh(), 1_000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [client, topic, vault]);

  const run: IngestRun | null = useMemo(() => {
    if (!activity) return null;
    if (selectedRun) {
      return activity.runs.find((row) => row.run_id === selectedRun) ?? activity.active_run;
    }
    return activity.active_run;
  }, [activity, selectedRun]);

  const runId = selectedRun || run?.run_id || "";

  // Chronological (oldest → newest) for the selected/active run only.
  const timelineItems = useMemo(() => {
    const rows = (activity?.events ?? [])
      .filter((event) => !runId || event.run_id === runId)
      .slice()
      .sort((a, b) => a.ts.localeCompare(b.ts));
    return groupTimeline(rows);
  }, [activity, runId]);

  useEffect(() => {
    const host = timelineRef.current;
    if (!host || !stickToEnd.current) return;
    host.scrollTop = host.scrollHeight;
  }, [timelineItems]);

  const workflow = run?.workflow === "curate" ? "curate" : "ingest";
  const stages =
    workflow === "curate"
      ? (activity?.curate_pipeline_stages ?? CURATE_FALLBACK)
      : (activity?.pipeline_stages ?? INGEST_FALLBACK);
  const live = Boolean(run && !run.terminal);
  const activeRunId = selectedRun || activity?.active_run?.run_id || "";

  return (
    <main class="pane-main ingest">
      <section class="ingest-hero">
        <div>
          <p class="eyebrow">{workflow === "curate" ? "Example curation" : "Source ingestion"}</p>
          <h2 class="ingest-heading">
            {workflow === "curate" ? "Curate a training example" : "Watch the wiki grow"}
          </h2>
          <p class="muted">
            {workflow === "curate"
              ? "Curation is its own short workflow — save a (query, pages, answer, verdict) example without holding the ingest rail open."
              : "Chronological checkpoint stream — topic → schema → fetch → plan → store → pages — so you can follow the paper as it lands. Curation is listed separately when you save an example."}
          </p>
        </div>
        <div class={`ingest-pulse ${live ? "live" : run ? "idle" : "empty"}`}>
          <span class="pulse-dot" aria-hidden="true" />
          <strong>{live ? "In progress" : run ? "Last run" : "Waiting"}</strong>
          <small>
            {run?.current_title ||
              "Start an ingest in Claude; progress appears here automatically."}
          </small>
        </div>
      </section>

      {error ? <aside role="alert">Ingest activity failed: {error}</aside> : null}

      <section
        class={`pipeline workflow-${workflow}`}
        aria-label={workflow === "curate" ? "Curate pipeline" : "Ingest pipeline"}
      >
        {stages
          .filter((stage) => stage !== "error")
          .map((stage, index) => {
            const reached = (run?.stage_index ?? -1) >= index;
            const current = run?.current_stage === stage;
            return (
              <div
                class={`pipe-stage ${reached ? "reached" : ""} ${current ? "current" : ""}`}
                key={stage}
              >
                <span class="pipe-index">{index + 1}</span>
                <strong>{STAGE_LABELS[stage] || stage}</strong>
                {current ? <small>{live ? "now" : "last"}</small> : null}
              </div>
            );
          })}
      </section>

      <div class="ingest-layout">
        <section class="panel ingest-runs">
          <header>
            <div>
              <h2>Runs</h2>
              <p>{activity?.runs.length ?? 0} recent</p>
            </div>
          </header>
          <ul class="run-list">
            {(activity?.runs.length ?? 0) === 0 ? (
              <li class="muted">No ingest or curate activity yet for this topic.</li>
            ) : (
              activity!.runs.map((row) => {
                const kind = row.workflow === "curate" ? "curate" : "ingest";
                const label =
                  row.citation_key ||
                  row.run_id.replace(/^(ingest|curate)-/, "") ||
                  row.run_id;
                return (
                  <li key={row.run_id}>
                    <button
                      type="button"
                      class={`run-item ${activeRunId === row.run_id ? "active" : ""}`}
                      onClick={() => setSelectedRun(row.run_id)}
                    >
                      <span class="run-title">
                        <span class={`run-kind kind-${kind}`}>{kind}</span>
                        {label}
                      </span>
                      <span class="run-meta">
                        {STAGE_LABELS[row.current_stage] || row.current_stage}
                        {row.terminal ? " · done" : " · live"}
                      </span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </section>

        <section class="panel ingest-timeline">
          <header>
            <div>
              <h2>Timeline</h2>
              <p>
                {run
                  ? `${run.event_count} events · ${workflow} · ${run.topic || topic}`
                  : "Events stream here during ingest or curation"}
              </p>
            </div>
          </header>
          <ol
            class="timeline"
            ref={timelineRef}
            onScroll={(event) => {
              const el = event.currentTarget;
              const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
              stickToEnd.current = distance < 48;
            }}
          >
            {timelineItems.length === 0 ? (
              <li class="muted empty-timeline">
                Open Claude and ingest a source. Cognitive steps report via{" "}
                <code>ingest_progress</code>; store/write auto-appear. Saving an
                example opens a separate curate run.
              </li>
            ) : (
              timelineItems.map((item, index) =>
                item.kind === "group" ? (
                  <GroupedEvents key={`g-${item.stage}-${index}`} stage={item.stage} events={item.events} />
                ) : (
                  <TimelineEvent key={`${item.event.ts}-${index}`} event={item.event} />
                ),
              )
            )}
          </ol>
        </section>
      </div>
    </main>
  );
}

function groupTimeline(events: IngestEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  for (const event of events) {
    const last = items[items.length - 1];
    if (GROUPABLE.has(event.stage) && last?.kind === "group" && last.stage === event.stage) {
      last.events.push(event);
      continue;
    }
    if (
      GROUPABLE.has(event.stage) &&
      last?.kind === "event" &&
      last.event.stage === event.stage
    ) {
      items[items.length - 1] = {
        kind: "group",
        stage: event.stage,
        events: [last.event, event],
      };
      continue;
    }
    items.push({ kind: "event", event });
  }
  return items;
}

function GroupedEvents({ stage, events }: { stage: string; events: IngestEvent[] }) {
  const [open, setOpen] = useState(false);
  const label = STAGE_LABELS[stage] || stage;
  const first = events[0];
  const last = events[events.length - 1];
  return (
    <li class={`tl-event tl-group status-ok source-server`}>
      <div class="tl-rail" aria-hidden="true">
        <span class="tl-dot" />
      </div>
      <div class="tl-body">
        <div class="tl-top">
          <span class="tl-stage">{label}</span>
          <time dateTime={last.ts}>
            {formatTime(first.ts)} – {formatTime(last.ts)}
          </time>
        </div>
        <strong class="tl-title">
          {stage === "store_source"
            ? `Stored ${events.length} source chunks`
            : `Wrote ${events.length} pages`}
        </strong>
        <p class="tl-detail">
          {events
            .map((event) => event.citation_key || event.path.split("/").pop() || event.title)
            .filter(Boolean)
            .slice(0, 8)
            .join(" · ")}
          {events.length > 8 ? ` · +${events.length - 8} more` : ""}
        </p>
        <button type="button" class="toggle" onClick={() => setOpen((value) => !value)}>
          {open ? "Hide details" : "Show each checkpoint"}
        </button>
        {open ? (
          <ol class="timeline nested">
            {events.map((event, index) => (
              <TimelineEvent key={`${event.ts}-n-${index}`} event={event} />
            ))}
          </ol>
        ) : null}
      </div>
    </li>
  );
}

function TimelineEvent({ event }: { event: IngestEvent }) {
  const label = STAGE_LABELS[event.stage] || event.stage;
  return (
    <li
      class={`tl-event status-${event.status} source-${event.source}${
        event.out_of_order ? " out-of-order" : ""
      }`}
    >
      <div class="tl-rail" aria-hidden="true">
        <span class="tl-dot" />
      </div>
      <div class="tl-body">
        <div class="tl-top">
          <span class="tl-stage">
            {label}
            {event.out_of_order ? " · late" : ""}
          </span>
          <time dateTime={event.ts}>{formatTime(event.ts)}</time>
        </div>
        <strong class="tl-title">{event.title}</strong>
        {event.detail ? <p class="tl-detail">{event.detail}</p> : null}
        {event.out_of_order ? (
          <p class="tl-detail late-note">
            Reported after a later pipeline step — shown in time order; stage rail stays
            monotonic.
          </p>
        ) : null}
        <div class="tl-refs">
          {event.citation_key ? <span class="ref">{event.citation_key}</span> : null}
          {event.path ? <span class="ref path">{event.path}</span> : null}
          {event.commit_sha ? (
            <span class="ref sha">{event.commit_sha.slice(0, 8)}</span>
          ) : null}
          <span class="ref origin">{event.source}</span>
        </div>
      </div>
    </li>
  );
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleTimeString();
}
