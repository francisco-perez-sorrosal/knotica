import { cleanup, fireEvent, render } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ObsidianContext } from "../../../obsidianLinks";
import type { ToolClient } from "../../../toolClient";
import type { MetricsWindow, WikiStatus } from "../../../types";

/**
 * The focus dimension (design §5.3, §7.2) — the fix for F1, "the Improve rail
 * is a dead checklist". `_improve_watermark` declares all six stages `pending`
 * in the overwhelmingly common case, so before this axis existed **no stage in
 * the flagship lane was interactive and a click did nothing**.
 *
 * These tests pin the axis's two halves: that focus *opens* a stage the server
 * calls `pending`, and that focus stays strictly orthogonal to the server's
 * own position — `aria-current="step"` never follows it, and the server never
 * steals it back.
 *
 * The six stage bodies are boundary-mocked exactly as the sibling
 * `ImproveLane.test.tsx` suite mocks them, and for the same reason: each is
 * characterized by its own dedicated suite, and re-exercising their fetches
 * and uPlot charts here would test the same behaviour twice.
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

const IMPROVE_LANE_MODULE_PATH = "../ImproveLane";

let ImproveLane: ImproveLaneComponent;

beforeAll(async () => {
  ({ ImproveLane } = (await import(IMPROVE_LANE_MODULE_PATH)) as {
    ImproveLane: ImproveLaneComponent;
  });
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
type StageState =
  | "pending"
  | "active"
  | "complete"
  | "blocked"
  | "unknown";

const mounted: Record<StageId, number> = {
  instrument: 0,
  observe: 0,
  gate: 0,
  heal: 0,
  promote: 0,
  prove: 0,
};

function stubFor(id: StageId) {
  return () => {
    mounted[id] += 1;
    return <div data-testid={`stub-${id}`} />;
  };
}

vi.mock("../InstrumentStage", () => ({ InstrumentStage: stubFor("instrument") }));
vi.mock("../ObserveStage", () => ({ ObserveStage: stubFor("observe") }));
vi.mock("../GateStage", () => ({ GateStage: stubFor("gate") }));
vi.mock("../HealStage", () => ({ HealStage: stubFor("heal") }));
vi.mock("../PromoteStage", () => ({ PromoteStage: stubFor("promote") }));
vi.mock("../ProveStage", () => ({ ProveStage: stubFor("prove") }));

beforeEach(() => {
  for (const id of STAGE_ORDER) mounted[id] = 0;
});

function statusWith(states: StageState[], topic = TOPIC): WikiStatus {
  return {
    topics: [
      {
        topic,
        lanes: {
          improve: STAGE_ORDER.map((id, index) => ({
            id,
            state: states[index],
            reason: null,
          })),
        },
      },
    ],
    gate: { state: "unknown", baseline: null, last_scalar: null },
    loop: { runner: { alive: false }, stage: "idle" },
  } as unknown as WikiStatus;
}

const IDLE: StageState[] = Array<StageState>(6).fill("pending");

function statesAtWatermark(index: number): StageState[] {
  return STAGE_ORDER.map((_, i) =>
    i < index ? "complete" : i === index ? "active" : "pending",
  );
}

/** Every method a click could conceivably reach. None may fire on a mount. */
function fakeClient(): ToolClient {
  return {
    wikiStatus: vi.fn(),
    loopRunEval: vi.fn(),
    loopOnce: vi.fn(),
    compileRun: vi.fn(),
    datasetsBootstrap: vi.fn(),
    datasetsBootstrapTrain: vi.fn(),
    query: vi.fn(),
  } as unknown as ToolClient;
}

function props(overrides: Partial<ImproveLaneProps> = {}): ImproveLaneProps {
  return {
    client: fakeClient(),
    topic: TOPIC,
    vault: VAULT,
    status: statusWith(IDLE),
    metrics: null,
    obsidianCtx: {},
    onStatusRefresh: vi.fn(),
    ...overrides,
  };
}

function rows(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

function disclosureIn(row: HTMLElement): HTMLButtonElement {
  const button = row.querySelector<HTMLButtonElement>(".lane-stage-disclosure");
  if (!button) throw new Error("row has no disclosure control");
  return button;
}

// ---------------------------------------------------------------------------
// F1: a pending stage is reachable
// ---------------------------------------------------------------------------

describe("focus opens a stage the server declares pending (the F1 fix)", () => {
  it("mounts no stage body at all while the rail is idle and untouched", () => {
    render(<ImproveLane {...props()} />);

    expect(STAGE_ORDER.every((id) => mounted[id] === 0)).toBe(true);
  });

  it("mounts the real stage body when its rail disclosure is clicked", () => {
    const { container } = render(<ImproveLane {...props()} />);

    fireEvent.click(disclosureIn(rows(container)[2]));

    expect(mounted.gate).toBe(1);
    expect(STAGE_ORDER.filter((id) => id !== "gate").every((id) => mounted[id] === 0)).toBe(true);
  });

  it("mounts the real stage body when its loop-strip node is clicked", () => {
    const { container } = render(<ImproveLane {...props()} />);

    const nodes = container.querySelectorAll<HTMLButtonElement>("button.loop-node");
    expect(nodes).toHaveLength(6);
    fireEvent.click(nodes[4]);

    expect(mounted.promote).toBe(1);
  });

  it("closes the stage again when its own disclosure is clicked a second time", () => {
    const { container } = render(<ImproveLane {...props()} />);

    const disclosure = disclosureIn(rows(container)[1]);
    fireEvent.click(disclosure);
    expect(container.querySelector('[data-testid="stub-observe"]')).toBeTruthy();

    fireEvent.click(disclosureIn(rows(container)[1]));
    expect(container.querySelector('[data-testid="stub-observe"]')).toBeNull();
  });

  /**
   * NOT the mount-effect guard, despite what it looks like. The six stage
   * bodies are boundary-mocked in this suite (see the file docblock), so a
   * real stage's `useEffect` never reaches this fake client -- what is
   * asserted is that `ImproveLane` **itself** calls nothing billed while
   * routing focus, which is a claim about the rail, not about the stages.
   *
   * The stage-level guarantee is pinned where a real component actually
   * mounts: `ObserveStage.test.tsx`'s "a focus-mount writes nothing" case,
   * which renders the real `ObserveStage` against a recording client
   * (`td-059`). Do not add mount-safety assertions here -- they would pass
   * vacuously.
   */
  it("routes focus without the rail itself calling any billed method", () => {
    const client = fakeClient();
    const { container } = render(<ImproveLane {...props({ client })} />);

    for (const row of rows(container)) fireEvent.click(disclosureIn(row));

    const billed = client as unknown as Record<string, ReturnType<typeof vi.fn>>;
    for (const method of [
      "loopRunEval",
      "loopOnce",
      "compileRun",
      "datasetsBootstrap",
      "datasetsBootstrapTrain",
      "query",
    ]) {
      expect(billed[method]).not.toHaveBeenCalled();
    }
  });
});

// ---------------------------------------------------------------------------
// §5.3: focus and declared state are orthogonal
// ---------------------------------------------------------------------------

describe("focus never touches the server's own position", () => {
  it("leaves aria-current unset on a focused pending stage", () => {
    const { container } = render(<ImproveLane {...props()} />);

    fireEvent.click(disclosureIn(rows(container)[3]));

    const heal = rows(container)[3];
    expect(heal.dataset.focus).toBe("true");
    expect(heal.getAttribute("aria-current")).toBeNull();
    expect(container.querySelectorAll('.lane-stage[aria-current="step"]')).toHaveLength(0);
  });

  it("keeps aria-current on the declared stage even while another one is focused", () => {
    const { container } = render(
      <ImproveLane {...props({ status: statusWith(statesAtWatermark(1)) })} />,
    );

    fireEvent.click(disclosureIn(rows(container)[4]));

    const current = Array.from(
      container.querySelectorAll<HTMLElement>('.lane-stage[aria-current="step"]'),
    );
    expect(current).toHaveLength(1);
    expect(current[0].textContent?.toLowerCase()).toContain("observe");
    expect(rows(container)[4].dataset.focus).toBe("true");
  });

  it("does not steal focus when the server declares a new active stage", () => {
    const { container, rerender } = render(<ImproveLane {...props()} />);

    fireEvent.click(disclosureIn(rows(container)[2]));
    expect(rows(container)[2].dataset.focus).toBe("true");

    rerender(
      <ImproveLane {...props({ status: statusWith(statesAtWatermark(1)) })} />,
    );

    // Gate keeps focus and stays open; Observe opens on its own because the
    // server declared it active -- that is the render matrix, not focus theft.
    expect(rows(container)[2].dataset.focus).toBe("true");
    expect(container.querySelector('[data-testid="stub-gate"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stub-observe"]')).toBeTruthy();
  });

  it("resets focus when the topic changes", () => {
    const { container, rerender } = render(<ImproveLane {...props()} />);

    fireEvent.click(disclosureIn(rows(container)[2]));
    expect(container.querySelector('[data-testid="stub-gate"]')).toBeTruthy();

    rerender(
      <ImproveLane
        {...props({ topic: "physics", status: statusWith(IDLE, "physics") })}
      />,
    );

    expect(container.querySelector('[data-testid="stub-gate"]')).toBeNull();
    expect(rows(container).every((row) => row.dataset.focus === "false")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// §7.2: the idle rail still names its entry point
// ---------------------------------------------------------------------------

describe("the idle rail is not a dead end", () => {
  it("cues the first stage while nothing is open", () => {
    const { container } = render(<ImproveLane {...props()} />);

    const cues = container.querySelectorAll(".lane-stage-cue");
    expect(cues).toHaveLength(1);
    expect(rows(container)[0].querySelector(".lane-stage-cue")).toBeTruthy();
  });

  it("drops the cue once something is open", () => {
    const { container } = render(<ImproveLane {...props()} />);

    fireEvent.click(disclosureIn(rows(container)[0]));

    expect(container.querySelectorAll(".lane-stage-cue")).toHaveLength(0);
  });

  it("offers no disclosure on a declared-current stage -- it is already open", () => {
    const { container } = render(
      <ImproveLane {...props({ status: statusWith(statesAtWatermark(1)) })} />,
    );

    expect(rows(container)[1].querySelector(".lane-stage-disclosure")).toBeNull();
    expect(container.querySelector('[data-testid="stub-observe"]')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// CH-1: the server's fifth word. `unknown` is the honest absence of a
// position, and the rail must say so rather than render it as `pending`.
// ---------------------------------------------------------------------------

const UNRECORDED: StageState[] = Array<StageState>(6).fill("unknown");

describe("an unknown rail is rendered as unknown, not as pending", () => {
  it("shows every stage's declared state as visible text", () => {
    const { container } = render(
      <ImproveLane {...props({ status: statusWith(UNRECORDED) })} />,
    );

    expect(
      rows(container).map((row) => row.dataset.state),
    ).toEqual(UNRECORDED);
    expect(
      rows(container)[0].querySelector(".lane-state-label")?.textContent,
    ).toBe("unknown");
  });

  it("leaves aria-current unset -- the process marker needs a declared position", () => {
    const { container } = render(
      <ImproveLane {...props({ status: statusWith(UNRECORDED) })} />,
    );

    expect(
      container.querySelectorAll('.lane-stage[aria-current="step"]'),
    ).toHaveLength(0);
  });

  it("stays reachable through focus, mounting the real body on disclosure", () => {
    const { container } = render(
      <ImproveLane {...props({ status: statusWith(UNRECORDED) })} />,
    );

    fireEvent.click(disclosureIn(rows(container)[3]));

    expect(container.querySelector('[data-testid="stub-heal"]')).toBeTruthy();
    expect(mounted.heal).toBe(1);
  });
});
