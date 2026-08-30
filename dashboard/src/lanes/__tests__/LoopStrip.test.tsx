import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LaneRailStageState } from "../../types";
import { LoopStrip, loopHeadline, type LoopStripStage } from "../LoopStrip";

/**
 * The loop strip (design §3.3) is a projection of state the lane already
 * declares -- these tests pin what the projection must never lose: the state
 * word as visible text, the return arc only where the lane is a cycle, and
 * no clickable node unless the caller can actually act on the click.
 */

afterEach(cleanup);

function stages(...states: LaneRailStageState[]): LoopStripStage[] {
  const titles = ["Instrument", "Observe", "Gate", "Heal", "Promote", "Prove"];
  return states.map((state, index) => ({
    id: titles[index].toLowerCase(),
    title: titles[index],
    state,
  }));
}

const ALL_PENDING = stages(
  "pending",
  "pending",
  "pending",
  "pending",
  "pending",
  "pending",
);

function nodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".loop-strip-item"));
}

describe("the strip renders one node per declared stage", () => {
  it("carries each stage's title and its state as visible text, never colour alone", () => {
    const { container } = render(
      <LoopStrip lane="improve" stages={stages("complete", "active")} />,
    );

    const rendered = nodes(container);
    expect(rendered).toHaveLength(2);
    expect(rendered.map((node) => node.dataset.state)).toEqual([
      "complete",
      "active",
    ]);
    expect(rendered[0].textContent?.toLowerCase()).toContain("instrument");
    expect(rendered[0].textContent?.toLowerCase()).toContain("complete");
    expect(rendered[1].textContent?.toLowerCase()).toContain("active");
  });

  it("renders nothing at all for a lane with no declared stages", () => {
    const { container } = render(<LoopStrip lane="home" stages={[]} />);

    expect(container.querySelector(".loop-strip")).toBeNull();
  });
});

describe("shape decides the track and the return arc", () => {
  it("draws the return arc for a cycle-shaped lane", () => {
    const { container } = render(
      <LoopStrip lane="improve" stages={ALL_PENDING} />,
    );

    expect(container.querySelector(".loop-strip")?.getAttribute("data-shape")).toBe("cycle");
    expect(container.querySelector(".loop-strip-arc")).toBeTruthy();
    expect(screen.getByText(/prove returns to instrument/i)).toBeTruthy();
  });

  it("draws no arc for a line-shaped lane", () => {
    const { container } = render(
      <LoopStrip lane="learn" stages={stages("complete", "active")} />,
    );

    expect(container.querySelector(".loop-strip")?.getAttribute("data-shape")).toBe("line");
    expect(container.querySelector(".loop-strip-arc")).toBeNull();
  });

  it("draws no arc for a checks-shaped lane", () => {
    const { container } = render(
      <LoopStrip lane="tend" stages={stages("complete", "pending")} />,
    );

    expect(container.querySelector(".loop-strip")?.getAttribute("data-shape")).toBe("checks");
    expect(container.querySelector(".loop-strip-arc")).toBeNull();
  });
});

describe("a node is a control only when the caller can act on it", () => {
  it("renders no button when no focus handler is supplied -- an affordance that lies is worse than none", () => {
    const { container } = render(
      <LoopStrip lane="improve" stages={ALL_PENDING} />,
    );

    expect(container.querySelectorAll("button.loop-node")).toHaveLength(0);
  });

  it("reports the clicked stage id when a focus handler is supplied", () => {
    const onFocus = vi.fn();
    const { container } = render(
      <LoopStrip lane="improve" stages={ALL_PENDING} onFocus={onFocus} />,
    );

    const buttons = container.querySelectorAll<HTMLButtonElement>("button.loop-node");
    expect(buttons).toHaveLength(6);
    fireEvent.click(buttons[2]);
    expect(onFocus).toHaveBeenCalledWith("gate");
  });

  it("marks the focused node, and only that node", () => {
    const { container } = render(
      <LoopStrip lane="improve" stages={ALL_PENDING} focusedId="heal" />,
    );

    const focused = nodes(container).filter(
      (node) => node.dataset.focus === "true",
    );
    expect(focused).toHaveLength(1);
    expect(focused[0].textContent?.toLowerCase()).toContain("heal");
  });
});

describe("the headline narrates declared state, never focus", () => {
  it("reads idle when nothing is running", () => {
    expect(loopHeadline("improve", "cycle", ALL_PENDING)).toBe(
      "IMPROVE · CYCLE IDLE — nothing running",
    );
  });

  it("names the active stage when one is running", () => {
    expect(
      loopHeadline("improve", "cycle", stages("complete", "active")),
    ).toBe("IMPROVE · OBSERVE ACTIVE — in progress");
  });

  it("names the blocked stage ahead of any active one", () => {
    expect(loopHeadline("improve", "cycle", stages("blocked", "active"))).toBe(
      "IMPROVE · INSTRUMENT BLOCKED — a precondition failed",
    );
  });

  it("reads complete when every stage has finished", () => {
    expect(loopHeadline("tend", "checks", stages("complete", "complete"))).toBe(
      "TEND · CHECKS COMPLETE — nothing left to run",
    );
  });

  it("reads unknown, not idle, when the server recorded nothing either way", () => {
    expect(loopHeadline("improve", "cycle", stages("unknown", "unknown"))).toBe(
      "IMPROVE · CYCLE UNKNOWN — nothing recorded yet",
    );
    // Non-vacuity: the idle wording must remain reachable and distinct, or
    // this assertion would pass on a strip that lost the distinction.
    expect(loopHeadline("improve", "cycle", ALL_PENDING)).not.toBe(
      loopHeadline("improve", "cycle", stages("unknown", "unknown")),
    );
  });
});

describe("an unknown stage is rendered honestly, never as pending", () => {
  it("carries the state word as visible text so colour is never the only signal", () => {
    const { container } = render(
      <LoopStrip lane="improve" stages={stages("unknown", "unknown")} />,
    );

    const rendered = nodes(container);
    expect(rendered.map((node) => node.dataset.state)).toEqual([
      "unknown",
      "unknown",
    ]);
    expect(rendered[0].textContent?.toLowerCase()).toContain("unknown");
  });
});
