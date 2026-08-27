import { cleanup, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { ObsidianContext } from "../../../obsidianLinks";
import type { ToolClient } from "../../../toolClient";
import type { MetricsWindow, WikiStatus } from "../../../types";

/**
 * `dashboard/src/lanes/improve/ImproveLane.tsx` does not exist yet -- this is
 * the RED half of a paired implementation/test step for
 * `INTERFACE_DESIGN.md` §2.4's six-stage assembly. Loaded through a
 * non-literal dynamic `import()` specifier -- the same device
 * `lanes/__tests__/LaneRail.test.tsx`, `lanes/improve/__tests__/GateStage.test.tsx`,
 * and `lanes/tend/__tests__/TendLane.test.tsx` used for their own
 * not-yet-existing modules: a literal `import { ImproveLane } from
 * "../ImproveLane"` would fail `tsc --noEmit` for the whole project the
 * moment this file lands; a dynamic import whose argument is not a string
 * literal is left unresolved by TypeScript, so the rest of the tree keeps
 * type-checking while this file fails at *runtime* with the missing-module
 * error the paired implementation step is gated on.
 *
 * All six landed stage bodies (`InstrumentStage`/`ObserveStage`/`GateStage`/
 * `HealStage`/`PromoteStage`/`ProveStage`) are boundary-mocked here, per
 * `dashboard/CLAUDE.md`'s own precedent (`GateStage.test.tsx` stubs
 * `PromptDiff` for the identical reason): each is already characterized by
 * its own dedicated suite (Steps 70/72/74), so re-exercising their internal
 * fetches/uPlot charts/two-phase billing here would test the same behavior
 * twice while adding real network/canvas machinery this *assembly* test does
 * not need. Only `ImproveLane`'s own wiring -- which stage mounts, what it is
 * handed, and the rail's own wrapper markup -- is under test.
 *
 * Load-bearing assumptions about the not-yet-landed assembly (the paired
 * implementation wins on conflict; each is independently falsified, not
 * tangled with its neighbours; full reasoning in
 * `LEARNINGS_test-engineer_step76.md`):
 *
 *   1. `<ImproveLane client={...} topic={...} vault={...} status={...}
 *      metrics={...} obsidianCtx={...} onStatusRefresh={...} />` -- `status`/
 *      `metrics` arrive as **props** from the app-level poll (mirroring
 *      `LoopPane.tsx`'s own shape), not from a second, lane-owned
 *      `wikiStatus`/`metricsRead` call. `client`/`topic`/`vault` are threaded
 *      straight through to whichever stage is mounted; `obsidianCtx` reaches
 *      only `ProveStage`; `onStatusRefresh` reaches `GateStage`/`HealStage`/
 *      `PromoteStage`, the three that already declare it.
 *   2. Each non-Home per-topic row's `wiki_status` payload carries
 *      `lanes.improve`, an array of `{id, state, reason}` in rail order
 *      (`core/status.py`, Step 48) -- already-derived server state. The
 *      watermark position is read directly off which entry's `state` is
 *      `"active"` or `"blocked"` (R5: at most one), never independently
 *      recomputed from `status.loop`/`status.gate`'s raw fields. The exact
 *      TypeScript shape of this block on `WikiStatus` is not asserted here
 *      (the type does not exist in `types.ts` yet, a testability gap flagged
 *      in `LEARNINGS_test-engineer_step76.md`); fixtures below reach it via
 *      a cast, matching the real Python payload's field names.
 *   3. Only the watermark stage (`active` or `blocked`) mounts its real,
 *      interactive body by default -- every other stage renders a
 *      precondition/one-line summary instead (`INTERFACE_DESIGN.md` §1.5,
 *      §2.4 rule 4; Step 75's own "Done when": "an ahead-of-watermark stage
 *      shows its precondition, never a disabled control"). What a `complete`
 *      stage does *on click* is deliberately not asserted here -- neither
 *      the plan nor `INTERFACE_DESIGN.md` names a mechanism, only an
 *      end-state ("Expandable on click → interactive"), and guessing one
 *      would pin an assumption nothing grounds.
 *   4. Each stage row renders as `<li class="lane-stage" data-state="...">`
 *      inside `<ol aria-label="improve stages">` -- the "Class contract" and
 *      accessibility floor `INTERFACE_DESIGN.md` §1.5 states for *every* new
 *      lane rail (both the generic `LaneRail.tsx` and the hand-rolled
 *      `TendLane.tsx` already honor this), not a guess about `ImproveLane`'s
 *      internal composition. The watermark stage additionally carries
 *      `aria-current="step"`.
 *
 * Not tested here (out of this step's scope, or covered elsewhere): the
 * cross-lane `onOpen*` source-scan is already automated, lane-tree-wide,
 * by `ProveStage.test.tsx`'s `no cross-lane navigation prop survives
 * anywhere under lanes/improve` suite -- it walks every `.ts`/`.tsx` file
 * under `dashboard/src/lanes/improve/` (excluding `__tests__`), so it will
 * cover `ImproveLane.tsx` the moment it lands without duplication here.
 * Whether a *complete* stage's own internal disclosure could end up nested
 * inside a second, `ImproveLane`-owned wrapper disclosure is a residual risk
 * this suite's boundary-mocked children cannot exercise (a stub renders no
 * `aria-expanded` element at all) -- flagged for the implementer and
 * verifier in `LEARNINGS_test-engineer_step76.md` rather than silently
 * assumed clean.
 */

interface ImproveLaneProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
  obsidianCtx: ObsidianContext;
  onStatusRefresh?: () => void | Promise<void>;
}

type ImproveLaneComponent = (props: ImproveLaneProps) => JSX.Element;

interface ImproveLaneModule {
  ImproveLane: ImproveLaneComponent;
}

const IMPROVE_LANE_MODULE_PATH = "../ImproveLane";

let ImproveLane: ImproveLaneComponent;

beforeAll(async () => {
  ({ ImproveLane } = (await import(
    IMPROVE_LANE_MODULE_PATH
  )) as ImproveLaneModule);
});

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

const STAGE_ORDER = [
  "instrument",
  "observe",
  "gate",
  "heal",
  "promote",
  "prove",
] as const;
type StageId = (typeof STAGE_ORDER)[number];

/** One captured-props array per stage, populated by the boundary mocks below. */
const captured: Record<StageId, Record<string, unknown>[]> = {
  instrument: [],
  observe: [],
  gate: [],
  heal: [],
  promote: [],
  prove: [],
};

function resetCaptured(): void {
  for (const id of STAGE_ORDER) captured[id] = [];
}

vi.mock("../InstrumentStage", () => ({
  InstrumentStage: (props: Record<string, unknown>) => {
    captured.instrument.push(props);
    return <div data-testid="stub-instrument" />;
  },
}));
vi.mock("../ObserveStage", () => ({
  ObserveStage: (props: Record<string, unknown>) => {
    captured.observe.push(props);
    return <div data-testid="stub-observe" />;
  },
}));
vi.mock("../GateStage", () => ({
  GateStage: (props: Record<string, unknown>) => {
    captured.gate.push(props);
    return <div data-testid="stub-gate" />;
  },
}));
vi.mock("../HealStage", () => ({
  HealStage: (props: Record<string, unknown>) => {
    captured.heal.push(props);
    return <div data-testid="stub-heal" />;
  },
}));
vi.mock("../PromoteStage", () => ({
  PromoteStage: (props: Record<string, unknown>) => {
    captured.promote.push(props);
    return <div data-testid="stub-promote" />;
  },
}));
vi.mock("../ProveStage", () => ({
  ProveStage: (props: Record<string, unknown>) => {
    captured.prove.push(props);
    return <div data-testid="stub-prove" />;
  },
}));

beforeEach(resetCaptured);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

type ImproveStageState = "pending" | "active" | "complete" | "blocked";

interface ImproveStageStatus {
  readonly id: StageId;
  readonly state: ImproveStageState;
  readonly reason?: string | null;
}

/** Mirrors `core/status.py`'s `lanes` block (Step 48): one entry per rail
 * stage, in order, each already-derived server state -- never a raw
 * watermark integer, and never business fields the client would have to
 * interpret itself. */
function lanesBlock(
  states: ImproveStageState[],
  reasons: Partial<Record<StageId, string>> = {},
): { improve: ImproveStageStatus[] } {
  return {
    improve: STAGE_ORDER.map((id, index) => ({
      id,
      state: states[index],
      reason: reasons[id] ?? null,
    })),
  };
}

const IDLE_STATES: ImproveStageState[] = [
  "pending",
  "pending",
  "pending",
  "pending",
  "pending",
  "pending",
];
const TERMINAL_STATES: ImproveStageState[] = [
  "complete",
  "complete",
  "complete",
  "complete",
  "complete",
  "complete",
];

/** Every stage before `index` is complete, `index` is active, the rest are
 * pending -- the one-active-stage-at-a-time shape every intermediate
 * position takes (R1-R3). */
function statesAtWatermark(index: number): ImproveStageState[] {
  return STAGE_ORDER.map((_, i) =>
    i < index ? "complete" : i === index ? "active" : "pending",
  );
}

function baseMetrics(): MetricsWindow {
  return {
    topic: TOPIC,
    records: [],
    has_more: false,
    next_before_generation: null,
    skipped_malformed: 0,
  };
}

/** `lanes` is not yet a declared field on `WikiStatus["topics"][number]`
 * (`types.ts`) -- a testability gap the implementer must close alongside
 * `ImproveLane.tsx` itself; recorded in `LEARNINGS_test-engineer_step76.md`.
 * The cast below reaches it with the real Python payload's field names. */
function baseStatus(lanes: { improve: ImproveStageStatus[] }): WikiStatus {
  return {
    schema_version: 1,
    vault: VAULT,
    vault_name: VAULT,
    vault_path: "/tmp/vault",
    default_vault: VAULT,
    available_vaults: [],
    compile_ready_threshold: 20,
    topics: [
      {
        topic: TOPIC,
        pages: 10,
        curated: 8,
        to_compile_ready: 0,
        lint_violations: 0,
        last_eval: null,
        lanes,
      },
    ],
    totals: { topics: 1, pages: 10, curated: 8, lint_violations: 0 },
    last_lint: null,
    unpushed: null,
    gate: { state: "unknown", baseline: null, last_scalar: null },
    llm: { available: false, mode: null },
    loop: {
      runner: {
        alive: false,
        pid: null,
        beat_at: null,
        interval_seconds: null,
      },
      stage: "idle",
    },
  } as unknown as WikiStatus;
}

function fakeClient(): ToolClient {
  return { wikiStatus: vi.fn() } as unknown as ToolClient;
}

function baseProps(
  overrides: Partial<ImproveLaneProps> = {},
): ImproveLaneProps {
  return {
    client: fakeClient(),
    topic: TOPIC,
    vault: VAULT,
    status: baseStatus(lanesBlock(IDLE_STATES)),
    metrics: baseMetrics(),
    obsidianCtx: {},
    onStatusRefresh: vi.fn(),
    ...overrides,
  };
}

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

function statesOf(nodes: HTMLElement[]): (string | undefined)[] {
  return nodes.map((node) => node.dataset.state);
}

function indicesWhere(
  nodes: HTMLElement[],
  predicate: (node: HTMLElement) => boolean,
): number[] {
  return nodes
    .map((node, index) => (predicate(node) ? index : -1))
    .filter((index) => index !== -1);
}

// ---------------------------------------------------------------------------
// Structure
// ---------------------------------------------------------------------------

describe("the assembled rail", () => {
  it("renders exactly the six declared stages, in declared order", () => {
    const { container } = render(<ImproveLane {...baseProps()} />);

    const nodes = stageNodes(container);
    expect(nodes).toHaveLength(6);
    STAGE_ORDER.forEach((id, index) => {
      expect(nodes[index].textContent ?? "").toMatch(new RegExp(id, "i"));
    });
  });

  it("labels the stage list with the improve lane name", () => {
    render(<ImproveLane {...baseProps()} />);

    expect(screen.getByRole("list", { name: "improve stages" })).toBeTruthy();
  });

  it("renders no tab bar anywhere -- VaultPane's nested-tabs failure mode does not reappear", () => {
    const { container } = render(
      <ImproveLane
        {...baseProps({ status: baseStatus(lanesBlock(statesAtWatermark(2))) })}
      />,
    );

    expect(
      container.querySelectorAll('[role="tab"], [role="tablist"], .check-tabs'),
    ).toHaveLength(0);
  });

  it("offers no jump/open navigation button of its own -- the rail is the only navigation", () => {
    render(
      <ImproveLane
        {...baseProps({ status: baseStatus(lanesBlock(statesAtWatermark(3))) })}
      />,
    );

    expect(
      screen.queryByRole("button", {
        name: /open (loop|arena|vault|ask|answer)/i,
      }),
    ).toBeNull();
  });

  it("carries no aria-expanded disclosure nested inside another one at the rail-wrapper level", () => {
    const { container } = render(
      <ImproveLane
        {...baseProps({ status: baseStatus(lanesBlock(statesAtWatermark(1))) })}
      />,
    );

    for (const toggle of Array.from(
      container.querySelectorAll("[aria-expanded]"),
    )) {
      expect(toggle.querySelector("[aria-expanded]")).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// R1-R5: the watermark position comes from server state, never re-derived
// ---------------------------------------------------------------------------

const POSITIONS: Array<{
  name: string;
  states: ImproveStageState[];
  reasons?: Partial<Record<StageId, string>>;
  expectedCurrent: number | null;
}> = [
  {
    name: "idle — every stage pending, the cycle has not started",
    states: IDLE_STATES,
    expectedCurrent: null,
  },
  {
    name: "active — observe is the live watermark",
    states: statesAtWatermark(1),
    expectedCurrent: 1,
  },
  {
    name: "blocked — observe's precondition failed",
    states: (() => {
      const states = statesAtWatermark(1);
      states[1] = "blocked";
      return states;
    })(),
    reasons: { observe: "eval harness offline" },
    expectedCurrent: 1,
  },
  {
    name: "terminal — every stage complete, the cycle finished",
    states: TERMINAL_STATES,
    expectedCurrent: null,
  },
];

describe.each(POSITIONS)(
  "rail position: $name",
  ({ states, reasons, expectedCurrent }) => {
    it("marks exactly one stage active-or-blocked, or none, matching R5", () => {
      const status = baseStatus(lanesBlock(states, reasons));
      const { container } = render(<ImproveLane {...baseProps({ status })} />);

      const nodes = stageNodes(container);
      const flagged = indicesWhere(
        nodes,
        (node) =>
          node.dataset.state === "active" || node.dataset.state === "blocked",
      );
      expect(flagged).toEqual(
        expectedCurrent === null ? [] : [expectedCurrent],
      );
    });

    it("marks that same stage aria-current='step', and no other", () => {
      const status = baseStatus(lanesBlock(states, reasons));
      const { container } = render(<ImproveLane {...baseProps({ status })} />);

      const nodes = stageNodes(container);
      const current = indicesWhere(
        nodes,
        (node) => node.getAttribute("aria-current") === "step",
      );
      expect(current).toEqual(
        expectedCurrent === null ? [] : [expectedCurrent],
      );
    });

    it("propagates every declared stage's state onto its own row's data-state", () => {
      const status = baseStatus(lanesBlock(states, reasons));
      const { container } = render(<ImproveLane {...baseProps({ status })} />);

      expect(statesOf(stageNodes(container))).toEqual(states);
    });
  },
);

it("mounts none of the six real stage bodies while idle -- every stage shows only its precondition", () => {
  render(
    <ImproveLane
      {...baseProps({ status: baseStatus(lanesBlock(IDLE_STATES)) })}
    />,
  );

  expect(STAGE_ORDER.every((id) => captured[id].length === 0)).toBe(true);
});

it("reads the watermark from status.topics[].lanes.improve, not by re-deriving it from status.loop", () => {
  // Poison the raw field a naive client-side heuristic might consult instead
  // of the already-derived `lanes.improve` block -- "evaluating" would
  // suggest `observe`, but the declared lane state below names `gate`.
  const status = baseStatus(lanesBlock(statesAtWatermark(2)));
  (status as unknown as { loop: { stage: string } }).loop.stage = "evaluating";

  render(<ImproveLane {...baseProps({ status })} />);

  expect(captured.gate).toHaveLength(1);
  expect(captured.observe).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// One data spine: props threaded to whichever stage is mounted, not re-fetched
// ---------------------------------------------------------------------------

describe("one data spine — status/metrics arrive as props, never a second wiki_status read", () => {
  it("never calls client.wikiStatus itself -- the app-level poll is the only reader", () => {
    const client = fakeClient();
    render(
      <ImproveLane
        {...baseProps({
          client,
          status: baseStatus(lanesBlock(statesAtWatermark(1))),
        })}
      />,
    );

    expect(client.wikiStatus).not.toHaveBeenCalled();
  });
});

describe.each(STAGE_ORDER.map((id, index) => ({ id, index })))(
  "the $id stage is the sole mounted body when it is the watermark",
  ({ id, index }) => {
    it("mounts only this stage's real body, and threads client/topic/vault/status/metrics/obsidianCtx/onStatusRefresh unchanged", () => {
      const client = fakeClient();
      const status = baseStatus(lanesBlock(statesAtWatermark(index)));
      const metrics = baseMetrics();
      const onStatusRefresh = vi.fn();
      const obsidianCtx: ObsidianContext = { vaultName: "kb" };

      render(
        <ImproveLane
          {...baseProps({
            client,
            status,
            metrics,
            obsidianCtx,
            onStatusRefresh,
          })}
        />,
      );

      for (const otherId of STAGE_ORDER) {
        expect(captured[otherId]).toHaveLength(otherId === id ? 1 : 0);
      }

      const props = captured[id][0];
      expect(props.client).toBe(client);
      expect(props.topic).toBe(TOPIC);
      expect(props.vault).toBe(VAULT);

      // `InstrumentStage` self-fetches its own dataset inventory (§2.4) and
      // takes no `status` prop -- every other stage reads the same object.
      if (id !== "instrument") {
        expect(props.status).toBe(status);
      }
      if (id === "observe") {
        expect(props.metrics).toBe(metrics);
      }
      if (id === "prove") {
        expect(props.obsidianCtx).toBe(obsidianCtx);
      }
      if (id === "gate" || id === "heal" || id === "promote") {
        expect(props.onStatusRefresh).toBe(onStatusRefresh);
      }
    });
  },
);
