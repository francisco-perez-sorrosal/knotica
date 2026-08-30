import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { useState } from "preact/hooks";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_PANE, resolveAnchor, resolveLaneFocus } from "../paneRouting";
import { LANE_STAGES } from "../processModel";
import {
  canOpenAnchor,
  openAnchor,
  publishOpenAnchor,
} from "../lanes/laneNavigation";
import { ProcessOutcome } from "../lanes/ProcessOutcome";
import { useStageFocus } from "../lanes/stageFocus";
import { PROCESS_META } from "../lanes/processMeta";

/**
 * The navigation contract: one App-owned `openAnchor(lane, stage?)`, a
 * `(lane, stage)` coordinate that survives the URL, and a one-shot arrival that
 * seeds stage focus without ever stealing it.
 *
 * Four independently falsifiable groups:
 *
 *   1. `resolveAnchor` carries the stage a deep link names, instead of
 *      accepting `?focus=` and discarding it -- the regression test for the
 *      long-standing hole where a focus-qualified link landed on the lane's
 *      default stage every time.
 *   2. The degrade ruling: a bad *coordinate* costs the stage, never the lane.
 *   3. The published-callback seam -- `ProcessOutcome`'s destination is a real
 *      control when navigation is wired and plain prose when it is not, so a
 *      component rendered outside `App` never shows an affordance that lies.
 *   4. The arrival is one-shot: it seeds focus once and then the axis is the
 *      user's, which is what keeps "focus is never stolen" true in the presence
 *      of a navigation request.
 */

afterEach(() => {
  cleanup();
  publishOpenAnchor(null);
});

describe("resolveAnchor carries the stage a deep link names", () => {
  it("keeps a focus that the lane actually declares", () => {
    expect(resolveAnchor("improve", "heal")).toEqual({
      lane: "improve",
      stage: "heal",
    });
  });

  it("keeps a focus reached through a legacy lane alias, resolved against the surviving lane", () => {
    // `?lane=loop` is a bookmark from before the dissolution; its focus must be
    // validated against `improve`'s stages, not against the retired pane's.
    expect(resolveAnchor("loop", "gate")).toEqual({
      lane: "improve",
      stage: "gate",
    });
  });

  it("carries a stage for every lane that declares one", () => {
    for (const [lane, stages] of Object.entries(LANE_STAGES)) {
      if (stages.length === 0) continue;
      expect(resolveAnchor(lane, stages[0].id).stage).toBe(stages[0].id);
    }
  });
});

describe("a bad within-lane coordinate never costs the lane", () => {
  it("an unrecognised focus degrades to the lane's own landing surface", () => {
    expect(resolveAnchor("improve", "not-a-real-focus")).toEqual({
      lane: "improve",
      stage: null,
    });
  });

  it("a focus belonging to another lane is not honoured here", () => {
    expect(resolveAnchor("learn", "heal").stage).toBeNull();
  });

  it("an empty focus is simply no coordinate", () => {
    expect(resolveAnchor("fill", "")).toEqual({ lane: "fill", stage: null });
  });

  it("an unrecognised lane still degrades to the default pane, with no stage", () => {
    expect(resolveAnchor("not-a-real-lane", "gate")).toEqual({
      lane: DEFAULT_PANE,
      stage: null,
    });
  });

  it("home has no stages, so a focus against it resolves to none", () => {
    expect(resolveAnchor("home", "gate").stage).toBeNull();
  });

  it("resolveLaneFocus keeps answering exactly what it always did", () => {
    // One resolution, projected two ways -- the pane-only projection is what
    // every existing caller reads, and it must not have moved.
    expect(resolveLaneFocus("improve", "heal")).toBe("improve");
    expect(resolveLaneFocus("not-a-real-lane", "heal")).toBe(DEFAULT_PANE);
  });
});

describe("the published navigation callback", () => {
  it("is unwired until App publishes, and calling it is a no-op rather than a crash", () => {
    expect(canOpenAnchor()).toBe(false);
    expect(() => openAnchor("improve", "gate")).not.toThrow();
  });

  it("delivers the lane and stage verbatim to whatever App published", () => {
    const spy = vi.fn();
    publishOpenAnchor(spy);
    expect(canOpenAnchor()).toBe(true);

    openAnchor("fill", "approve");

    expect(spy).toHaveBeenCalledWith("fill", "approve");
  });
});

describe("ProcessOutcome's NEXT STEP reaches the destination it names", () => {
  // A row whose `next` is unconditional, so the destination is deterministic.
  const PROCESS = "answer.gap_report" as const;
  const NEXT = PROCESS_META[PROCESS].next;

  it("renders the destination as prose when no navigation is published", () => {
    render(<ProcessOutcome process={PROCESS} />);

    expect(
      screen.queryByRole("button", { name: /^go to /i }),
    ).toBeNull();
    expect(screen.getByText(/^go to /i)).toBeTruthy();
  });

  it("renders the destination as a control once navigation is published", () => {
    publishOpenAnchor(vi.fn());
    render(<ProcessOutcome process={PROCESS} />);

    expect(screen.getByRole("button", { name: /^go to /i })).toBeTruthy();
  });

  it("clicking it opens the registry's own anchor, not a lane-level guess", () => {
    const spy = vi.fn();
    publishOpenAnchor(spy);
    render(<ProcessOutcome process={PROCESS} />);

    fireEvent.click(screen.getByRole("button", { name: /^go to /i }));

    expect(NEXT.kind).toBe("always");
    if (NEXT.kind !== "always") throw new Error("fixture drifted");
    expect(spy).toHaveBeenCalledWith(NEXT.go.lane, NEXT.go.stage);
  });
});

/**
 * A minimal rail harness. `useStageFocus`'s contract is only observable over
 * time -- what a *second* render does with a request that has since been
 * cleared is the whole of the one-shot property -- so it needs a component,
 * not a pure call.
 */
function FocusHarness({
  stages,
  initialRequest,
}: {
  stages: readonly { id: string; state: "active" | "pending" }[];
  initialRequest: string | null;
}) {
  const [request, setRequest] = useState<string | null>(initialRequest);
  const [scope, setScope] = useState("vault/topic-a");
  const { focusedId, focus } = useStageFocus(scope, stages, request);
  return (
    <div>
      <span data-testid="focused">{focusedId ?? "none"}</span>
      {/* App clears the request after the target lane's first render. */}
      <button type="button" onClick={() => setRequest(null)}>
        clear request
      </button>
      <button type="button" onClick={() => focus("prove")}>
        user opens prove
      </button>
      <button type="button" onClick={() => setScope("vault/topic-b")}>
        change topic
      </button>
    </div>
  );
}

describe("an arrival request seeds focus exactly once", () => {
  const RAIL = [
    { id: "observe", state: "active" },
    { id: "gate", state: "pending" },
    { id: "prove", state: "pending" },
  ] as const;

  function renderHarness(initialRequest: string | null) {
    render(<FocusHarness stages={RAIL} initialRequest={initialRequest} />);
    return () => screen.getByTestId("focused").textContent;
  }

  it("opens the requested stage on arrival", () => {
    const focused = renderHarness("gate");
    expect(focused()).toBe("gate");
  });

  it("leaves the server's active stage focused when nothing was requested", () => {
    const focused = renderHarness(null);
    expect(focused()).toBe("observe");
  });

  it("does not re-seed when the request is cleared -- the axis is the user's afterwards", () => {
    const focused = renderHarness("gate");
    fireEvent.click(screen.getByText("user opens prove"));
    expect(focused()).toBe("prove");

    fireEvent.click(screen.getByText("clear request"));
    expect(focused()).toBe("prove");
  });

  it("does not survive a scope change -- a stale request must not follow the user to a new topic", () => {
    const focused = renderHarness("gate");
    fireEvent.click(screen.getByText("clear request"));
    fireEvent.click(screen.getByText("change topic"));
    expect(focused()).toBe("observe");
  });
});
