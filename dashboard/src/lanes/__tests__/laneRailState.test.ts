import { beforeAll, describe, expect, it } from "vitest";

import { LANE_STAGES } from "../../processModel";

/**
 * The pure lane-rail state machine, pinned from the rail contract before the
 * module exists.
 *
 * Two derivation functions, both framework-free (no Preact import, no DOM,
 * no fetch): `deriveSequenceStages` turns one monotonic watermark into a
 * per-stage `pending`/`active`/`complete`/`blocked` state for the four
 * ordered lanes; `deriveChecklistStages` turns a set of independently
 * evaluable checks (the fifth lane's peers, no watermark) into the same
 * per-stage vocabulary, plus an optional UI-focus id.
 *
 * The module under test does not exist yet — this is the RED half of a
 * paired step. A literal `import { ... } from "../laneRailState"` would
 * fail `tsc --noEmit` for the whole project the moment this file is added,
 * not just this suite, so the specifier is loaded through a non-literal
 * binding below: TypeScript does not resolve a dynamic `import()` whose
 * argument isn't a string literal, so the rest of the tree keeps
 * type-checking while this file still fails at runtime with the
 * missing-module error the paired implementation step is gated on. The
 * types below are this suite's own mirror of the expected surface, not an
 * import of the real ones — once the module lands, sibling files that
 * `import type` it directly are what actually proves its exports exist.
 */

type StageState = "pending" | "active" | "complete" | "blocked";

interface BlockedInfo {
  what: string;
  why: string;
  fix: string;
}

interface DerivedStage {
  id: string;
  title: string;
  state: StageState;
  blocked: BlockedInfo | null;
}

interface SequenceStageInput {
  id: string;
  title: string;
}

interface ChecklistCheckInput {
  id: string;
  title: string;
  status: "complete" | "blocked" | "pending";
  reason?: BlockedInfo | null;
}

interface LaneRailStateModule {
  deriveSequenceStages(
    watermark: number | null,
    stages: readonly SequenceStageInput[],
    blockedReason?: BlockedInfo | null,
  ): DerivedStage[];
  deriveChecklistStages(
    checks: readonly ChecklistCheckInput[],
    activeId?: string | null,
  ): DerivedStage[];
}

const LANE_RAIL_STATE_MODULE_PATH = "../laneRailState";

let laneRailState: LaneRailStateModule;

beforeAll(async () => {
  laneRailState = (await import(
    LANE_RAIL_STATE_MODULE_PATH
  )) as LaneRailStateModule;
});

const ACTIVE_OR_BLOCKED = new Set<StageState>(["active", "blocked"]);

const BLOCKED_REASON: BlockedInfo = {
  what: "baseline",
  why: "not frozen yet",
  fix: "freeze the baseline before continuing",
};

// The Improve lane's declared stages, exactly as the served declaration and
// the generated mirror both carry them — six ordered stages, none a
// handoff. Used as the sequence fixture throughout.
const IMPROVE_STAGES: SequenceStageInput[] = LANE_STAGES.improve.map(
  ({ id, title }) => ({
    id,
    title,
  }),
);

// Tend's declared stages — the one checklist lane, five independent peers.
const TEND_CHECK_DESCRIPTORS = LANE_STAGES.tend.map(({ id, title }) => ({
  id,
  title,
}));

function allTendChecksWithStatus(
  status: ChecklistCheckInput["status"],
): ChecklistCheckInput[] {
  return TEND_CHECK_DESCRIPTORS.map(({ id, title }) => ({ id, title, status }));
}

describe("deriveSequenceStages — one watermark drives every stage's state", () => {
  it("marks every stage pending when the lane is idle (no run has ever advanced the watermark)", () => {
    const result = laneRailState.deriveSequenceStages(null, IMPROVE_STAGES);

    expect(result.map((stage) => stage.state)).toEqual(
      IMPROVE_STAGES.map(() => "pending"),
    );
  });

  it("renders every declared stage even while idle — an idle lane is not an empty rail", () => {
    const result = laneRailState.deriveSequenceStages(null, IMPROVE_STAGES);

    expect(result).toHaveLength(IMPROVE_STAGES.length);
  });

  it.each([0, 1, 2, 3, 4, 5])(
    "at watermark %i, every earlier stage is complete, that stage is active, and every later stage is pending",
    (watermark) => {
      const result = laneRailState.deriveSequenceStages(
        watermark,
        IMPROVE_STAGES,
      );

      const expected = IMPROVE_STAGES.map((_, index) =>
        index < watermark
          ? "complete"
          : index === watermark
            ? "active"
            : "pending",
      );
      expect(result.map((stage) => stage.state)).toEqual(expected);
    },
  );

  it("marks every stage complete once the watermark has passed the last one (a finished run)", () => {
    const result = laneRailState.deriveSequenceStages(
      IMPROVE_STAGES.length,
      IMPROVE_STAGES,
    );

    expect(result.map((stage) => stage.state)).toEqual(
      IMPROVE_STAGES.map(() => "complete"),
    );
  });

  it("marks the watermark stage blocked instead of active when a precondition is unmet, and carries the reason", () => {
    const result = laneRailState.deriveSequenceStages(
      1,
      IMPROVE_STAGES,
      BLOCKED_REASON,
    );

    expect(result[0].state).toBe("complete");
    expect(result[1].state).toBe("blocked");
    expect(result[1].blocked).toEqual(BLOCKED_REASON);
    expect(result[2].state).toBe("pending");
  });

  it("leaves every non-watermark stage's `blocked` field null — the reason names one position only", () => {
    const result = laneRailState.deriveSequenceStages(
      2,
      IMPROVE_STAGES,
      BLOCKED_REASON,
    );

    const others = result.filter((_, index) => index !== 2);
    expect(others.every((stage) => stage.blocked === null)).toBe(true);
  });

  it("carries no reason at all when the watermark stage has none — it is active, not blocked", () => {
    const result = laneRailState.deriveSequenceStages(2, IMPROVE_STAGES);

    expect(result[2].state).toBe("active");
    expect(result[2].blocked).toBeNull();
  });

  it("is a pure function of its own arguments — the same watermark always derives the same output", () => {
    const first = laneRailState.deriveSequenceStages(3, IMPROVE_STAGES);
    const second = laneRailState.deriveSequenceStages(3, IMPROVE_STAGES);

    expect(second).toEqual(first);
  });

  it("carries no memory between calls — a lower watermark called after a higher one still derives from the rule table alone", () => {
    laneRailState.deriveSequenceStages(IMPROVE_STAGES.length, IMPROVE_STAGES);
    const result = laneRailState.deriveSequenceStages(1, IMPROVE_STAGES);

    expect(result.map((stage) => stage.state)).toEqual([
      "complete",
      "active",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("carries each stage's declared id and title through unchanged, in declared order", () => {
    const result = laneRailState.deriveSequenceStages(3, IMPROVE_STAGES);

    expect(result.map(({ id, title }) => ({ id, title }))).toEqual(
      IMPROVE_STAGES,
    );
  });

  it.each<Array<number | null>>([[null], [0], [1], [2], [3], [4], [5], [6]])(
    "never marks more than one stage active-or-blocked at watermark %s",
    (watermark) => {
      const result = laneRailState.deriveSequenceStages(
        watermark,
        IMPROVE_STAGES,
      );

      const activeOrBlocked = result.filter((stage) =>
        ACTIVE_OR_BLOCKED.has(stage.state),
      );
      expect(activeOrBlocked.length).toBeLessThanOrEqual(1);
    },
  );

  it("marks zero stages active-or-blocked exactly when idle or terminal, and exactly one at every position in between", () => {
    const watermarks: Array<number | null> = [
      null,
      0,
      1,
      2,
      3,
      4,
      5,
      IMPROVE_STAGES.length,
    ];
    const counts = watermarks.map(
      (watermark) =>
        laneRailState
          .deriveSequenceStages(watermark, IMPROVE_STAGES)
          .filter((stage) => ACTIVE_OR_BLOCKED.has(stage.state)).length,
    );

    expect(counts).toEqual([0, 1, 1, 1, 1, 1, 1, 0]);
  });

  it("cannot represent Observe and Heal both in play at once — the exact pair a per-step ready/current pair renders as simultaneously active today", () => {
    const observeIndex = IMPROVE_STAGES.findIndex(
      (stage) => stage.id === "observe",
    );
    const healIndex = IMPROVE_STAGES.findIndex((stage) => stage.id === "heal");

    const result = laneRailState.deriveSequenceStages(
      observeIndex,
      IMPROVE_STAGES,
    );

    expect(result[observeIndex].state).toBe("active");
    expect(result[healIndex].state).toBe("pending");
  });

  it("has no parameter that could name a second, independently-advanced position — only the one watermark can ever be active", () => {
    // A payload that tracks two signals (a reached watermark and a
    // separately-reported "current" stage) can let the second run ahead of
    // the first. This function's signature accepts one watermark integer
    // and nothing else that could name a stage position, so that divergence
    // is not just avoided but unrepresentable through this API.
    const result = laneRailState.deriveSequenceStages(2, IMPROVE_STAGES);

    const active = result.filter((stage) => stage.state === "active");
    expect(active).toHaveLength(1);
    expect(active[0].id).toBe(IMPROVE_STAGES[2].id);
  });

  it.each(["learn", "answer", "improve", "fill"] as const)(
    "derives the declared stage count for the %s lane rather than assuming one fixed length",
    (lane) => {
      const stages = LANE_STAGES[lane].map(({ id, title }) => ({ id, title }));

      const result = laneRailState.deriveSequenceStages(0, stages);

      expect(result).toHaveLength(stages.length);
      expect(result[0].state).toBe("active");
    },
  );
});

describe("deriveChecklistStages — independently evaluable peer checks, no watermark", () => {
  it("derives each check's state from its own status alone, with no cross-check coupling", () => {
    const checks: ChecklistCheckInput[] = [
      {
        id: TEND_CHECK_DESCRIPTORS[0].id,
        title: TEND_CHECK_DESCRIPTORS[0].title,
        status: "complete",
      },
      {
        id: TEND_CHECK_DESCRIPTORS[1].id,
        title: TEND_CHECK_DESCRIPTORS[1].title,
        status: "blocked",
        reason: BLOCKED_REASON,
      },
      {
        id: TEND_CHECK_DESCRIPTORS[2].id,
        title: TEND_CHECK_DESCRIPTORS[2].title,
        status: "pending",
      },
      {
        id: TEND_CHECK_DESCRIPTORS[3].id,
        title: TEND_CHECK_DESCRIPTORS[3].title,
        status: "complete",
      },
      {
        id: TEND_CHECK_DESCRIPTORS[4].id,
        title: TEND_CHECK_DESCRIPTORS[4].title,
        status: "pending",
      },
    ];

    const result = laneRailState.deriveChecklistStages(checks);

    expect(result.map((stage) => stage.state)).toEqual([
      "complete",
      "blocked",
      "pending",
      "complete",
      "pending",
    ]);
    expect(result[1].blocked).toEqual(BLOCKED_REASON);
  });

  it("is order-independent — reordering the input checks changes no check's own derived state", () => {
    const checks = allTendChecksWithStatus("pending").map((check, index) =>
      index === 1 ? { ...check, status: "complete" as const } : check,
    );

    const forward = laneRailState.deriveChecklistStages(checks);
    const reversed = laneRailState.deriveChecklistStages([...checks].reverse());

    const byId = (stages: DerivedStage[]) =>
      Object.fromEntries(stages.map((stage) => [stage.id, stage.state]));
    expect(byId(reversed)).toEqual(byId(forward));
  });

  it("marks zero checks active when nothing is in UI focus", () => {
    const result = laneRailState.deriveChecklistStages(
      allTendChecksWithStatus("pending"),
    );

    expect(result.filter((stage) => stage.state === "active")).toHaveLength(0);
  });

  it("marks exactly the pending check named by activeId as active, and nothing else", () => {
    const focusedId = TEND_CHECK_DESCRIPTORS[2].id;

    const result = laneRailState.deriveChecklistStages(
      allTendChecksWithStatus("pending"),
      focusedId,
    );

    const active = result.filter((stage) => stage.state === "active");
    expect(active).toHaveLength(1);
    expect(active[0].id).toBe(focusedId);
  });

  it("does not fabricate an active check for an activeId that names no declared check", () => {
    const result = laneRailState.deriveChecklistStages(
      allTendChecksWithStatus("pending"),
      "not-a-declared-check",
    );

    expect(result.filter((stage) => stage.state === "active")).toHaveLength(0);
  });

  it("keeps a blocked check's own state when it is also the one in UI focus — focus never masks a remedy", () => {
    const focusedId = TEND_CHECK_DESCRIPTORS[0].id;
    const checks = allTendChecksWithStatus("pending").map((check) =>
      check.id === focusedId
        ? { ...check, status: "blocked" as const, reason: BLOCKED_REASON }
        : check,
    );

    const result = laneRailState.deriveChecklistStages(checks, focusedId);

    const focused = result.find((stage) => stage.id === focusedId);
    expect(focused?.state).toBe("blocked");
    expect(focused?.blocked).toEqual(BLOCKED_REASON);
  });

  it("keeps a complete check's own state when it is also the one in UI focus", () => {
    const focusedId = TEND_CHECK_DESCRIPTORS[0].id;
    const checks = allTendChecksWithStatus("pending").map((check) =>
      check.id === focusedId
        ? { ...check, status: "complete" as const }
        : check,
    );

    const result = laneRailState.deriveChecklistStages(checks, focusedId);

    expect(result.find((stage) => stage.id === focusedId)?.state).toBe(
      "complete",
    );
  });

  it("is clean only when every declared check is complete", () => {
    const allComplete = allTendChecksWithStatus("complete");

    const result = laneRailState.deriveChecklistStages(allComplete);

    expect(result.every((stage) => stage.state === "complete")).toBe(true);
  });

  it("is not clean when even one declared check is not complete", () => {
    const oneBlocked = allTendChecksWithStatus("complete").map(
      (check, index) =>
        index === 0
          ? { ...check, status: "blocked" as const, reason: BLOCKED_REASON }
          : check,
    );

    const result = laneRailState.deriveChecklistStages(oneBlocked);

    expect(result.every((stage) => stage.state === "complete")).toBe(false);
  });

  it("carries each check's declared id and title through unchanged, in declared order", () => {
    const checks = allTendChecksWithStatus("pending");

    const result = laneRailState.deriveChecklistStages(checks);

    expect(result.map(({ id, title }) => ({ id, title }))).toEqual(
      TEND_CHECK_DESCRIPTORS,
    );
  });
});
