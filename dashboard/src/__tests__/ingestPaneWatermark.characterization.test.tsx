import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { IngestPane } from "../IngestPane";
import type { ToolClient } from "../toolClient";
import type { IngestActivity, IngestEvent, IngestRun } from "../types";

/**
 * M3 characterization net for `IngestPane`'s rail: the `pipe-stage`
 * `reached`/`current` derivation and the `out_of_order` "late" timeline
 * rendering that keeps that rail monotonic. Pinned exactly as they exist
 * today, BEFORE the M3 dissolution retires `IngestPane`'s hand-copied
 * `INGEST_STAGES`-shaped pipeline in favor of the generalized lane-rail
 * `sequence` kind. Every assertion here must pass unmodified today; the
 * suite is a regression guard, not a new requirement.
 *
 * `IngestPane` only ever renders the activity its `client.ingestActivityRead`
 * call resolves -- there is no prop-level injection point -- so the fixture
 * is delivered through a fake client that resolves it. `ToolClient` is the
 * project's own MCP-call seam (`dashboard/CLAUDE.md`: "the single seam for
 * MCP calls"), so faking just the one method this component calls is a
 * boundary fake, not a mock of the unit under test.
 */

afterEach(cleanup);

const PIPELINE_STAGES = [
  "resolve_topic",
  "read_schema",
  "fetch",
  "parse",
  "plan",
  "store_source",
  "write_page",
  "complete",
];

/** Label text `IngestPane` renders for a given `stage` key -- see `STAGE_LABELS`. */
const LABEL: Record<string, string> = {
  resolve_topic: "Topic",
  read_schema: "Schema",
  fetch: "Fetch",
  parse: "Parse",
  plan: "Plan",
  store_source: "Store",
  write_page: "Pages",
  complete: "Done",
};

function baseRun(overrides: Partial<IngestRun> = {}): IngestRun {
  return {
    run_id: "ingest-run-1",
    workflow: "ingest",
    topic: "agentic-systems",
    citation_key: "smith2024",
    current_stage: "parse",
    current_title: "Parsing paper",
    status: "running",
    terminal: false,
    stage_index: 3,
    event_count: 1,
    stages_seen: PIPELINE_STAGES.slice(0, 4),
    ...overrides,
  };
}

function baseEvent(overrides: Partial<IngestEvent> = {}): IngestEvent {
  return {
    schema_version: 1,
    ts: "2026-08-27T10:00:00Z",
    run_id: "ingest-run-1",
    workflow: "ingest",
    topic: "agentic-systems",
    stage: "parse",
    status: "ok",
    title: "Parsed paper body",
    detail: "",
    citation_key: "smith2024",
    path: "",
    commit_sha: "",
    source: "server",
    ...overrides,
  };
}

function baseActivity(run: IngestRun | null, events: IngestEvent[] = []): IngestActivity {
  return {
    schema_version: 1,
    activity_path: "/tmp/activity.jsonl",
    pipeline_stages: PIPELINE_STAGES,
    events,
    active_run: run,
    runs: run ? [run] : [],
    has_more: false,
  };
}

function fakeClient(activity: IngestActivity): ToolClient {
  return {
    ingestActivityRead: async () => activity,
  } as unknown as ToolClient;
}

function renderPane(activity: IngestActivity) {
  render(
    <IngestPane
      client={fakeClient(activity)}
      topic="agentic-systems"
      vault="main"
      obsidianCtx={{}}
    />,
  );
}

/** The `.pipe-stage` element for a given pipeline stage key. */
async function pipeStage(stage: string): Promise<HTMLElement> {
  const label = LABEL[stage] ?? stage;
  const heading = await screen.findByText(label, { selector: "strong" });
  const el = heading.closest(".pipe-stage");
  if (!el) throw new Error(`no .pipe-stage ancestor for "${label}"`);
  return el as HTMLElement;
}

describe("the pipeline rail's reached watermark", () => {
  it("marks every stage up to and including stage_index as reached", async () => {
    renderPane(baseActivity(baseRun({ stage_index: 3 })));

    expect((await pipeStage("resolve_topic")).classList.contains("reached")).toBe(true);
    expect((await pipeStage("parse")).classList.contains("reached")).toBe(true);
  });

  it("does not mark the stage immediately past stage_index as reached", async () => {
    renderPane(baseActivity(baseRun({ stage_index: 3 })));

    expect((await pipeStage("parse")).classList.contains("reached")).toBe(true);
    expect((await pipeStage("plan")).classList.contains("reached")).toBe(false);
  });

  it("treats the watermark as monotonic: a higher stage_index reaches every stage below it too", async () => {
    renderPane(baseActivity(baseRun({ stage_index: 6 })));

    for (const stage of ["resolve_topic", "read_schema", "fetch", "parse", "plan", "store_source", "write_page"]) {
      expect((await pipeStage(stage)).classList.contains("reached")).toBe(true);
    }
    expect((await pipeStage("complete")).classList.contains("reached")).toBe(false);
  });

  it("reaches nothing when there is no active run -- the stage_index fallback is -1", async () => {
    renderPane(baseActivity(null));

    await screen.findByText("Waiting");

    expect((await pipeStage("resolve_topic")).classList.contains("reached")).toBe(false);
  });
});

describe("the pipeline rail's current marker", () => {
  it("marks exactly the run's current_stage as current", async () => {
    renderPane(baseActivity(baseRun({ stage_index: 3, current_stage: "parse" })));

    expect((await pipeStage("parse")).classList.contains("current")).toBe(true);
    expect((await pipeStage("fetch")).classList.contains("current")).toBe(false);
  });

  it("derives current from current_stage independently of stage_index -- a stage can be current without being reached", async () => {
    renderPane(baseActivity(baseRun({ stage_index: 2, current_stage: "write_page" })));

    const fetchStage = await pipeStage("fetch");
    const pagesStage = await pipeStage("write_page");

    expect(fetchStage.classList.contains("reached")).toBe(true);
    expect(fetchStage.classList.contains("current")).toBe(false);
    expect(pagesStage.classList.contains("current")).toBe(true);
    expect(pagesStage.classList.contains("reached")).toBe(false);
  });
});

describe("the out-of-order timeline marker that keeps the rail monotonic", () => {
  it("renders the late marker and the monotonic-rail note for an out-of-order event", async () => {
    renderPane(
      baseActivity(
        baseRun({ stage_index: 3, current_stage: "parse" }),
        [baseEvent({ ts: "2026-08-27T10:00:05Z", stage: "parse", out_of_order: true })],
      ),
    );

    const late = await screen.findByText(/Parse\s*·\s*late/);
    const item = late.closest("li.tl-event");
    expect(item?.classList.contains("out-of-order")).toBe(true);
    expect(
      screen.getByText(
        "Reported after a later pipeline step — shown in time order; stage rail stays monotonic.",
      ),
    ).toBeTruthy();
  });

  it("renders no late marker or monotonic-rail note for an in-order event", async () => {
    renderPane(
      baseActivity(
        baseRun({ stage_index: 3, current_stage: "parse" }),
        [baseEvent({ ts: "2026-08-27T10:00:05Z", stage: "parse", out_of_order: false })],
      ),
    );

    const title = await screen.findByText("Parsed paper body");
    const item = title.closest("li.tl-event");
    expect(item?.classList.contains("out-of-order")).toBe(false);
    expect(screen.queryByText(/·\s*late/)).toBeNull();
  });
});
