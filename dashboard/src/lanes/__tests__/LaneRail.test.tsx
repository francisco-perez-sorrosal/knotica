import { cleanup, render, screen, within } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

/**
 * `dashboard/src/lanes/LaneRail.tsx` does not exist yet -- this is the RED
 * half of a paired step (INTERFACE_DESIGN.md §1.2, §1.5). A literal
 * `import { LaneRail } from "../LaneRail"` would fail `tsc --noEmit` for the
 * whole project the moment this file lands, so the module is loaded through
 * a non-literal specifier the same way `laneRailState.test.ts` (Step 62)
 * loaded its own not-yet-existing module: TypeScript does not resolve a
 * dynamic `import()` whose argument isn't a string literal, so the rest of
 * the tree keeps type-checking while this file fails at runtime with the
 * missing-module error the paired implementation step is gated on.
 *
 * The types below mirror this suite's own expectation of the component's
 * prop shape (§1.2's wire contract, carried verbatim) -- not an import of
 * the real ones. Three load-bearing assumptions the paired implementer may
 * satisfy differently (full reasoning in `LEARNINGS_test-engineer_step64.md`;
 * the paired implementation wins on conflict):
 *
 *   1. The component is invoked as `<LaneRail rail={...} />` -- one prop
 *      named `rail`, holding the full `LaneRail` wire contract (§1.2).
 *   2. `aria-current="step"` marks whichever stage sits at the watermark
 *      position, whether its rendered `data-state` is `"active"` or
 *      `"blocked"` -- R3 treats `blocked` as a modifier on the active
 *      position, not a separate one, so this suite treats both as
 *      "current" for the aria-current assertion.
 *   3. A stage whose `handoff` field is non-null renders a
 *      `[data-testid="lane-stage-handoff"]` marker inside its `.lane-stage`
 *      node; a stage with `handoff: null` does not. `HandoffSpec` itself is
 *      declared opaque as of Step 61 (§3's dispatch contract is designed in
 *      a later milestone), so this suite asserts presence-of-affordance
 *      only, never the handoff's internal shape or content.
 *
 * Stage lookup throughout is positional (`stageNodes(container)[i]`), not by
 * an invented id attribute -- the rail contract already requires stages to
 * render in their declared order, so index-into-the-fixture-array is a
 * property of the contract, not an extra assumption on top of it.
 */

type StageState = "pending" | "active" | "complete" | "blocked";
type LaneKind = "sequence" | "checklist";
type LaneCardinality = "singleton" | "aggregate";
type Actor = "you" | "claude" | "system" | null;

interface BlockedInfo {
  what: string;
  why: string;
  fix: string;
}

type HandoffSpec = Readonly<Record<string, unknown>>;

interface LaneStageFixture {
  id: string;
  title: string;
  state: StageState;
  fact: string;
  count: number | null;
  blocked: BlockedInfo | null;
  handoff: HandoffSpec | null;
  actor: Actor;
}

interface LaneRailFixture {
  lane: "home" | "learn" | "answer" | "improve" | "fill" | "tend";
  kind: LaneKind;
  cardinality: LaneCardinality;
  scope: { topic: string; vault: string };
  watermark: number | null;
  outcome: { state: string; label: string } | null;
  stages: readonly LaneStageFixture[];
}

type LaneRailComponent = (props: { rail: LaneRailFixture }) => JSX.Element;

interface LaneRailModule {
  LaneRail: LaneRailComponent;
}

const LANE_RAIL_MODULE_PATH = "../LaneRail";

let LaneRail: LaneRailComponent;

beforeAll(async () => {
  ({ LaneRail } = (await import(LANE_RAIL_MODULE_PATH)) as LaneRailModule);
});

afterEach(cleanup);

function stage(
  overrides: Partial<LaneStageFixture> &
    Pick<LaneStageFixture, "id" | "title" | "state">,
): LaneStageFixture {
  return {
    fact: "",
    count: null,
    blocked: null,
    handoff: null,
    actor: null,
    ...overrides,
  };
}

const BLOCKED_REASON: BlockedInfo = {
  what: "baseline",
  why: "no golden set has been frozen yet",
  fix: "freeze a baseline to unblock this stage",
};

/** Learn's three-stage rail, watermark at "pages" (index 1): complete/active/pending. */
function sequenceRail(
  overrides: Partial<LaneRailFixture> = {},
): LaneRailFixture {
  return {
    lane: "learn",
    kind: "sequence",
    cardinality: "aggregate",
    scope: { topic: "agentic-systems", vault: "main" },
    watermark: 1,
    outcome: null,
    stages: [
      stage({
        id: "source",
        title: "Source",
        state: "complete",
        fact: "stored",
      }),
      stage({
        id: "pages",
        title: "Pages",
        state: "active",
        fact: "3 pages written",
        actor: "claude",
      }),
      stage({
        id: "curate",
        title: "Curate",
        state: "pending",
        fact: "after the pages land",
      }),
    ],
    ...overrides,
  };
}

/** Same three-stage rail, but the watermark stage is blocked rather than active. */
function sequenceBlockedRail(): LaneRailFixture {
  return sequenceRail({
    stages: [
      stage({
        id: "source",
        title: "Source",
        state: "complete",
        fact: "stored",
      }),
      stage({
        id: "pages",
        title: "Pages",
        state: "blocked",
        fact: "waiting on a baseline",
        blocked: BLOCKED_REASON,
        actor: "you",
      }),
      stage({
        id: "curate",
        title: "Curate",
        state: "pending",
        fact: "after the pages land",
      }),
    ],
  });
}

/** Tend's four-check rail: complete, blocked, pending, active(focused) -- all four states at once. */
function checklistRail(): LaneRailFixture {
  return {
    lane: "tend",
    kind: "checklist",
    cardinality: "aggregate",
    scope: { topic: "", vault: "main" },
    watermark: null,
    outcome: null,
    stages: [
      stage({
        id: "doctor",
        title: "Doctor",
        state: "complete",
        fact: "clean",
      }),
      stage({
        id: "lint",
        title: "Lint",
        state: "blocked",
        fact: "2 violations",
        blocked: BLOCKED_REASON,
        actor: "you",
      }),
      stage({ id: "okf", title: "OKF", state: "pending" }),
      stage({ id: "drift", title: "Drift", state: "active", actor: "you" }),
    ],
  };
}

function renderRail(rail: LaneRailFixture): Element {
  return render(<LaneRail rail={rail} />).container;
}

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

function currentStageCount(container: Element): number {
  return container.querySelectorAll('.lane-stage[aria-current="step"]').length;
}

describe("the rail container", () => {
  it("labels the stage list with the lane name", () => {
    renderRail(sequenceRail());

    expect(screen.getByRole("list", { name: "learn stages" })).toBeTruthy();
  });

  it("renders every declared stage, in declaration order", () => {
    const container = renderRail(sequenceRail());

    const nodes = stageNodes(container);
    expect(nodes).toHaveLength(3);
    expect(nodes.map((node) => node.dataset.state)).toEqual([
      "complete",
      "active",
      "pending",
    ]);
  });
});

describe("aria-current marks the watermark position (R3, R5)", () => {
  it("marks exactly the active stage as current", () => {
    const container = renderRail(sequenceRail());

    expect(currentStageCount(container)).toBe(1);
    expect(stageNodes(container)[1].getAttribute("aria-current")).toBe("step");
  });

  it("marks the blocked stage as current when the watermark position is blocked, not active", () => {
    const container = renderRail(sequenceBlockedRail());

    expect(currentStageCount(container)).toBe(1);
    expect(stageNodes(container)[1].getAttribute("aria-current")).toBe("step");
  });

  it("marks no stage as current when the lane is idle (watermark null)", () => {
    const container = renderRail(
      sequenceRail({
        watermark: null,
        stages: [
          stage({ id: "source", title: "Source", state: "pending" }),
          stage({ id: "pages", title: "Pages", state: "pending" }),
          stage({ id: "curate", title: "Curate", state: "pending" }),
        ],
      }),
    );

    expect(currentStageCount(container)).toBe(0);
  });

  it("marks no stage as current when the lane is terminal (watermark past the last stage)", () => {
    const container = renderRail(
      sequenceRail({
        watermark: 3,
        stages: [
          stage({
            id: "source",
            title: "Source",
            state: "complete",
            fact: "stored",
          }),
          stage({
            id: "pages",
            title: "Pages",
            state: "complete",
            fact: "3 pages written",
          }),
          stage({
            id: "curate",
            title: "Curate",
            state: "complete",
            fact: "curated",
          }),
        ],
      }),
    );

    expect(currentStageCount(container)).toBe(0);
  });
});

describe("state is never color-only (accessibility floor, §1.5)", () => {
  const CHECKLIST_STATE_CASES: ReadonlyArray<readonly [number, StageState]> = [
    [0, "complete"],
    [1, "blocked"],
    [2, "pending"],
    [3, "active"],
  ];

  it.each(CHECKLIST_STATE_CASES)(
    "shows the %s state as its own data-state attribute and as visible text",
    (index, expectedState) => {
      const container = renderRail(checklistRail());

      const node = stageNodes(container)[index];
      expect(node.dataset.state).toBe(expectedState);
      expect(node.textContent?.toLowerCase()).toContain(expectedState);
    },
  );
});

describe("a blocked stage always renders its three-part remedy inline", () => {
  it("shows what/why/fix without requiring a click", () => {
    const container = renderRail(checklistRail());

    const blockedNode = stageNodes(container)[1];
    expect(
      within(blockedNode).getByText(BLOCKED_REASON.what, { exact: false }),
    ).toBeTruthy();
    expect(
      within(blockedNode).getByText(BLOCKED_REASON.why, { exact: false }),
    ).toBeTruthy();
    expect(
      within(blockedNode).getByText(BLOCKED_REASON.fix, { exact: false }),
    ).toBeTruthy();
  });
});

describe("an ahead-of-watermark pending stage shows its precondition as content, never a disabled button", () => {
  it("renders the precondition text inline", () => {
    const container = renderRail(sequenceRail());

    const pendingNode = stageNodes(container)[2];
    expect(
      within(pendingNode).getByText("after the pages land", { exact: false }),
    ).toBeTruthy();
  });

  it("never pairs a disabled button with a title tooltip -- the LoopPane.tsx:879-880 pattern this design forbids", () => {
    const container = renderRail(sequenceRail());

    const offenders = container.querySelectorAll("button[disabled][title]");
    expect(offenders).toHaveLength(0);
  });
});

describe("disclosure controls on interactive stages", () => {
  it("gives a complete (behind-the-watermark) stage an aria-expanded disclosure button", () => {
    const container = renderRail(sequenceRail());

    const completeNode = stageNodes(container)[0];
    expect(completeNode.querySelector("button[aria-expanded]")).toBeTruthy();
  });
});

describe("mandatory fact line (§1.2: fact is mandatory on every non-pending stage)", () => {
  it("renders the complete stage's fact", () => {
    const container = renderRail(sequenceRail());

    expect(
      within(stageNodes(container)[0]).getByText("stored", { exact: false }),
    ).toBeTruthy();
  });

  it("renders the active stage's fact", () => {
    const container = renderRail(sequenceRail());

    expect(
      within(stageNodes(container)[1]).getByText("3 pages written", {
        exact: false,
      }),
    ).toBeTruthy();
  });
});

describe("the handoff affordance", () => {
  it("renders a handoff marker for a stage whose handoff field is present", () => {
    const container = renderRail(
      sequenceRail({
        stages: [
          stage({
            id: "source",
            title: "Source",
            state: "complete",
            fact: "stored",
          }),
          stage({
            id: "pages",
            title: "Pages",
            state: "active",
            fact: "3 pages written",
            actor: "claude",
            handoff: { ask: "Claude writes the pages for this topic." },
          }),
          stage({
            id: "curate",
            title: "Curate",
            state: "pending",
            fact: "after the pages land",
          }),
        ],
      }),
    );

    const activeNode = stageNodes(container)[1];
    expect(within(activeNode).getByTestId("lane-stage-handoff")).toBeTruthy();
  });

  it("renders no handoff marker for a stage whose handoff field is null", () => {
    const container = renderRail(sequenceRail());

    const activeNode = stageNodes(container)[1];
    expect(
      activeNode.querySelector('[data-testid="lane-stage-handoff"]'),
    ).toBeNull();
  });
});

describe("the checklist kind (Tend)", () => {
  it("labels the stage list with the lane name", () => {
    renderRail(checklistRail());

    expect(screen.getByRole("list", { name: "tend stages" })).toBeTruthy();
  });

  it("renders all four states as independent peers in one rail", () => {
    const container = renderRail(checklistRail());

    const nodes = stageNodes(container);
    expect(nodes.map((node) => node.dataset.state)).toEqual([
      "complete",
      "blocked",
      "pending",
      "active",
    ]);
  });

  it("marks only the focused (active) check as current, leaving complete/blocked/pending alone", () => {
    const container = renderRail(checklistRail());

    expect(currentStageCount(container)).toBe(1);
    expect(stageNodes(container)[3].getAttribute("aria-current")).toBe("step");
  });
});
