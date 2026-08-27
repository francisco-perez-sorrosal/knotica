import { cleanup, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ObsidianContext } from "../../../obsidianLinks";
import { LANE_STAGES } from "../../../processModel";
import type { ToolClient } from "../../../toolClient";
import type { IngestActivity, IngestRun } from "../../../types";

/**
 * `dashboard/src/lanes/learn/LearnLane.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step for `INTERFACE_DESIGN.md`
 * §2.2 (Learn's `source -> fetch/parse -> pages -> curate` rail). Loaded
 * through a non-literal dynamic `import()` specifier, the same device
 * `lanes/improve/__tests__/ImproveLane.test.tsx` and
 * `lanes/answer/__tests__/AnswerLane.test.tsx` used for their own
 * not-yet-existing modules: a literal `import { LearnLane } from
 * "../LearnLane"` would fail `tsc --noEmit` for the whole project the moment
 * this file lands; a dynamic import whose argument is not a string literal
 * is left unresolved by TypeScript, so the rest of the tree keeps
 * type-checking while this file fails at *runtime* with the missing-module
 * error the paired implementation step is gated on.
 *
 * `HandoffStage` is a real, already-tested component (Step 88) -- stubbing
 * it here is a boundary mock (its own poll/dispatch-tier machinery is out of
 * scope for this suite), not a mock of the unit under test, mirroring
 * `GateStage.test.tsx`'s `PromptDiff` stub for the identical reason.
 *
 * Load-bearing assumptions about the not-yet-landed component (the paired
 * implementation wins on conflict; full reasoning in
 * `LEARNINGS_test-engineer_step93.md`):
 *
 *   1. `<LearnLane client={...} topic={...} vault={...} obsidianCtx={...} />`
 *      -- the exact prop shape `IngestPane.tsx` already takes (§2.2's
 *      "behaviour-preserving move" reads as prop-surface-preserving too).
 *      `LearnLane` polls `client.ingestActivityRead(topic, vault, "")`
 *      itself, mirroring `IngestPane.tsx`'s own `useEffect` -- there is no
 *      second, `wiki_status`-sourced state for this lane's own detailed
 *      view (unlike `ImproveLane`), because the rail's per-stage facts
 *      (paper title, section counts, pages written) only exist in the rich
 *      `ingest_activity` payload, not in `wiki_status`'s coarse
 *      `{id, state, reason}` lanes block.
 *   2. The four rail stages render in declared order from
 *      `processModel.ts::LANE_STAGES.learn` -- read by identity, not
 *      hand-copied (the `INGEST_STAGES`-shaped list `IngestPane.tsx` kept at
 *      its own top retires with the pane). This suite asserts against the
 *      imported declaration's own `.title` values, never a literal
 *      duplicate, so a change to the declaration cannot silently desync
 *      from what this suite expects.
 *   3. **The current-vs-watermark divergence, resolved in favor of
 *      `IngestPane.tsx`'s existing monotonic "reached" derivation** (the
 *      plan's own instruction that "the derivation moves unchanged"): the
 *      ingest journal's `_LEARN_JOURNAL_FOLD` groups are not contiguous in
 *      `pipeline_stages` order (`store_source` folds into `source` despite
 *      running after `plan`, which folds into `pages`), so a coarse
 *      watermark computed by looking up only the run's raw `current_stage`
 *      would regress the rail backwards the moment `store_source` reports.
 *      The assumption: `LearnLane` instead takes the *maximum* fold-group
 *      index over every raw stage at or before `run.stage_index` --
 *      monotonic by construction, and the reason `store_source` reporting
 *      after `plan` must not un-reach `pages`. This is the one behaviour
 *      this suite treats as load-bearing rather than incidental, since a
 *      naive `current_stage`-keyed lookup (which is what
 *      `core/status_lanes.py::_learn_watermark` uses for the unrelated
 *      cross-topic Home projection) would fail it.
 *   4. `curate` is genuinely decoupled from the ingest run's own watermark
 *      (`§2.2`: "a separate workflow server-side... deliberately, so an
 *      un-curated ingest is not stuck"): its own state is read off whichever
 *      run in `activity.runs` has `workflow === "curate"`, independent of
 *      the ingest run's `terminal`/`stage_index`. Once the ingest run is
 *      `terminal`, `pages` renders `complete` (not stuck at `active`
 *      forever) even with no curate run yet -- this is the "terminal
 *      precedes the last stage" case `§2.2` states explicitly.
 *   5. **Not an assumption -- ground truth from the already-landed Step 91
 *      whole-target-state census** (`src/__tests__/m4DissolutionCensus.test.tsx`
 *      group (e)): `lanes/learn`'s subtree embeds **exactly one**
 *      `<HandoffStage ... command="ingest" ... />` JSX tag, full stop.
 *      `pages` is the only stage that ever mounts a `HandoffStage`; it
 *      toggles `active` on/off as the watermark moves on/off `pages`, but
 *      the tag itself is the rail's single static embed site. `curate` --
 *      despite also being declared `handoff: true` in `LANE_STAGES.learn`,
 *      per `process_model.py::_build_learn_rail`'s "every stage is a
 *      handoff" docstring -- renders no `HandoffStage` of its own; its
 *      `active`/`complete` state is a plain text change, not a mounted
 *      control. (An earlier draft of this suite assumed a second,
 *      curate-commanded embed; the census's static source-grep settles it
 *      before this step needs to guess.) `source`/`fetch_parse` likewise
 *      never mount one, consistent with the single-static-tag count.
 *
 * Not tested here (out of this step's scope, or `HandoffStage`'s own already
 * -tested contract): the exact prose of each stage's `ask` copy, dispatch
 * tier affordances (`DispatchControl`'s A/B/C/D labels, Step 88), and the
 * per-stage "fact" text (paper title, section/page counts) beyond presence
 * where the design pins a concrete value.
 */

vi.mock("../../HandoffStage", () => ({
  HandoffStage: (props: {
    command: string;
    active: boolean;
    ask: string;
    suggestionId: string;
  }) => (
    <div
      data-testid="handoff-stage-mock"
      data-command={props.command}
      data-active={props.active ? "true" : "false"}
      data-has-ask={props.ask ? "true" : "false"}
    />
  ),
}));

interface LearnLaneProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  obsidianCtx: ObsidianContext;
}

type LearnLaneComponent = (props: LearnLaneProps) => JSX.Element;

interface LearnLaneModule {
  LearnLane: LearnLaneComponent;
}

const LEARN_LANE_MODULE_PATH = "../LearnLane";

let LearnLane: LearnLaneComponent;

beforeAll(async () => {
  ({ LearnLane } = (await import(LEARN_LANE_MODULE_PATH)) as LearnLaneModule);
});

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

const SOURCE = 0;
const FETCH_PARSE = 1;
const PAGES = 2;
const CURATE = 3;

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

function ingestRun(overrides: Partial<IngestRun> = {}): IngestRun {
  return {
    run_id: "ingest-run-1",
    workflow: "ingest",
    topic: TOPIC,
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

function curateRun(overrides: Partial<IngestRun> = {}): IngestRun {
  return {
    run_id: "curate-run-1",
    workflow: "curate",
    topic: TOPIC,
    citation_key: "smith2024",
    current_stage: "curate",
    current_title: "Curating example",
    status: "running",
    terminal: false,
    stage_index: 0,
    event_count: 1,
    stages_seen: ["curate"],
    ...overrides,
  };
}

function activityOf(
  active: IngestRun | null,
  runs: IngestRun[] = active ? [active] : [],
): IngestActivity {
  return {
    schema_version: 1,
    activity_path: "/tmp/activity.jsonl",
    pipeline_stages: PIPELINE_STAGES,
    curate_pipeline_stages: ["curate", "complete"],
    events: [],
    active_run: active,
    runs,
    has_more: false,
  };
}

function fakeClient(activity: IngestActivity): ToolClient {
  return {
    ingestActivityRead: vi.fn(async () => activity),
  } as unknown as ToolClient;
}

function renderLane(activity: IngestActivity): Element {
  return render(
    <LearnLane
      client={fakeClient(activity)}
      topic={TOPIC}
      vault={VAULT}
      obsidianCtx={{}}
    />,
  ).container;
}

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

/** Waits for the async `ingestActivityRead` fixture to land, then reads the
 * rail nodes -- `findBy*` retries until the fetched fixture has rendered. */
async function renderAndWaitForRail(
  activity: IngestActivity,
): Promise<HTMLElement[]> {
  const container = renderLane(activity);
  await screen.findByRole("list", { name: "learn stages" });
  return stageNodes(container);
}

describe("the rail's structural source (INTERFACE_DESIGN.md §2.2 identity, not a copy)", () => {
  it("renders exactly the four stages LANE_STAGES.learn declares, in that order", async () => {
    const nodes = await renderAndWaitForRail(activityOf(null));

    const expectedTitles = LANE_STAGES.learn.map((stage) => stage.title);
    expect(nodes).toHaveLength(LANE_STAGES.learn.length);
    expect(
      nodes.map((node) => node.querySelector("strong")?.textContent),
    ).toEqual(expectedTitles);
  });

  it("renders every stage pending when no ingest or curate activity exists yet", async () => {
    const nodes = await renderAndWaitForRail(activityOf(null));

    expect(nodes.map((node) => node.dataset.state)).toEqual([
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });
});

describe("the four watermark positions, folded from the ingest journal", () => {
  it("marks source active during early ingest (topic/schema resolution)", async () => {
    const nodes = await renderAndWaitForRail(
      activityOf(ingestRun({ current_stage: "read_schema", stage_index: 1 })),
    );

    expect(nodes[SOURCE].dataset.state).toBe("active");
    expect(nodes[FETCH_PARSE].dataset.state).toBe("pending");
    expect(nodes[PAGES].dataset.state).toBe("pending");
    expect(nodes[CURATE].dataset.state).toBe("pending");
  });

  it("completes source and marks fetch/parse active once fetch/parse begin", async () => {
    const nodes = await renderAndWaitForRail(
      activityOf(ingestRun({ current_stage: "parse", stage_index: 3 })),
    );

    expect(nodes[SOURCE].dataset.state).toBe("complete");
    expect(nodes[FETCH_PARSE].dataset.state).toBe("active");
    expect(nodes[PAGES].dataset.state).toBe("pending");
  });

  it("completes source and fetch/parse and marks pages active once pages are being written", async () => {
    const nodes = await renderAndWaitForRail(
      activityOf(ingestRun({ current_stage: "write_page", stage_index: 6 })),
    );

    expect(nodes[SOURCE].dataset.state).toBe("complete");
    expect(nodes[FETCH_PARSE].dataset.state).toBe("complete");
    expect(nodes[PAGES].dataset.state).toBe("active");
    expect(nodes[CURATE].dataset.state).toBe("pending");
  });

  it(
    "does not un-reach pages when store_source reports after plan -- the fold is " +
      "monotonic over reached raw stages, not a point lookup on current_stage",
    async () => {
      // `store_source` folds into the `source` rail position, but it is
      // *reported* after `plan` (which folds into `pages`) in real ingest
      // runs. A coarse watermark that looked up only `current_stage`'s own
      // fold group would regress the rail from `pages` back to `source` the
      // instant this event lands -- the exact mutation this test pins
      // against IngestPane.tsx's existing monotonic "reached" derivation.
      const nodes = await renderAndWaitForRail(
        activityOf(
          ingestRun({ current_stage: "store_source", stage_index: 5 }),
        ),
      );

      expect(nodes[SOURCE].dataset.state).toBe("complete");
      expect(nodes[FETCH_PARSE].dataset.state).toBe("complete");
      expect(nodes[PAGES].dataset.state).toBe("active");
    },
  );

  it("completes pages once the ingest run is terminal, even with no curate run yet", async () => {
    const nodes = await renderAndWaitForRail(
      activityOf(
        ingestRun({
          current_stage: "complete",
          stage_index: 7,
          terminal: true,
        }),
      ),
    );

    expect(nodes[PAGES].dataset.state).toBe("complete");
    expect(nodes[CURATE].dataset.state).toBe("pending");
  });

  it("marks curate active from its own run, independent of a completed ingest run", async () => {
    const ingest = ingestRun({
      current_stage: "complete",
      stage_index: 7,
      terminal: true,
    });
    const curate = curateRun({ current_stage: "curate", terminal: false });
    const nodes = await renderAndWaitForRail(
      activityOf(curate, [ingest, curate]),
    );

    expect(nodes[PAGES].dataset.state).toBe("complete");
    expect(nodes[CURATE].dataset.state).toBe("active");
  });
});

describe("the handoff embed at whichever stage is the client's current write (HandoffStage, boundary-mocked)", () => {
  it("mounts no handoff while every stage is pending", async () => {
    await renderAndWaitForRail(activityOf(null));

    expect(screen.queryByTestId("handoff-stage-mock")).toBeNull();
  });

  it("mounts exactly one active ingest handoff once pages is the watermark", async () => {
    await renderAndWaitForRail(
      activityOf(ingestRun({ current_stage: "write_page", stage_index: 6 })),
    );

    const handoffs = screen.getAllByTestId("handoff-stage-mock");
    expect(handoffs).toHaveLength(1);
    expect(handoffs[0].dataset.command).toBe("ingest");
    expect(handoffs[0].dataset.active).toBe("true");
  });

  it("does not mount a handoff for pages once it is complete and curate has not started", async () => {
    await renderAndWaitForRail(
      activityOf(
        ingestRun({
          current_stage: "complete",
          stage_index: 7,
          terminal: true,
        }),
      ),
    );

    expect(screen.queryByTestId("handoff-stage-mock")).toBeNull();
  });

  it(
    "mounts no handoff for curate even while it is the watermark -- the rail's " +
      "one static HandoffStage embed is pages' alone (m4DissolutionCensus.test.tsx group (e))",
    async () => {
      const ingest = ingestRun({
        current_stage: "complete",
        stage_index: 7,
        terminal: true,
      });
      const curate = curateRun({ current_stage: "curate", terminal: false });
      await renderAndWaitForRail(activityOf(curate, [ingest, curate]));

      expect(screen.queryByTestId("handoff-stage-mock")).toBeNull();
    },
  );
});

describe("curate is optional -- the rail's one place the terminal precedes its last stage (§2.2)", () => {
  it(
    "reads the lane's terminal as reaching 'committed page' once pages completes, " +
      "while curate is still pending -- a mutated 'AND curate complete' implementation fails this",
    async () => {
      const container = renderLane(
        activityOf(
          ingestRun({
            current_stage: "complete",
            stage_index: 7,
            terminal: true,
          }),
        ),
      );

      await screen.findByText(/committed page/i);
      const nodes = stageNodes(container);
      expect(nodes[CURATE].dataset.state).toBe("pending");
    },
  );

  it("never renders the terminal before pages is reached", async () => {
    renderLane(
      activityOf(ingestRun({ current_stage: "parse", stage_index: 3 })),
    );

    await screen.findByRole("list", { name: "learn stages" });
    expect(screen.queryByText(/committed page/i)).toBeNull();
  });
});
