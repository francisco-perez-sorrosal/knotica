import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { StateList } from "../StateList";

/**
 * The multi-item live readout primitive (design §2.4) -- the direct fix
 * for `.arena-lane`'s unstyled `<strong>/<span>/<em>` fragments. The state
 * word must always be visible text next to the icon: round 1 §2.5's
 * never-colour-alone floor, applied one level deeper.
 */

afterEach(cleanup);

describe("StateList", () => {
  it("renders each row's name, state word, and right-aligned value", () => {
    render(
      <StateList
        label="Arena variants"
        rows={[
          {
            id: "variant-2",
            state: "running",
            icon: "state:active",
            name: "variant-2",
            stateLabel: "running",
            value: "0.6412",
          },
        ]}
      />,
    );

    const list = screen.getByRole("list", { name: "Arena variants" });
    expect(list).toBeTruthy();
    expect(screen.getByText("variant-2")).toBeTruthy();
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.getByText("0.6412")).toBeTruthy();
  });

  it("renders — for an absent value, never blank", () => {
    render(
      <StateList
        label="Pending candidates"
        rows={[
          {
            id: "variant-1",
            state: "pending",
            icon: "state:pending",
            name: "variant-1",
            stateLabel: "pending",
            value: null,
          },
        ]}
      />,
    );

    expect(screen.getByText("—")).toBeTruthy();
  });

  it("renders a quiet row action only when supplied", () => {
    render(
      <StateList
        label="Pending candidates"
        rows={[
          {
            id: "loop/c/9f3a",
            state: "pending",
            icon: "state:pending",
            name: "loop/c/9f3a",
            stateLabel: "pending",
            value: "a1b2c3d",
            action: <button type="button">Show query.md diff</button>,
          },
          {
            id: "loop/c/71ce",
            state: "processed",
            icon: "state:complete",
            name: "loop/c/71ce",
            stateLabel: "processed",
            value: "ff01e22",
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Show query.md diff" })).toBeTruthy();
    expect(document.querySelectorAll(".state-list-action").length).toBe(1);
  });

  it("carries each row's opaque state as data-state, distinct from any tone", () => {
    const { container } = render(
      <StateList
        label="Arena variants"
        rows={[
          {
            id: "variant-3",
            state: "winner",
            icon: "state:complete",
            name: "variant-3",
            stateLabel: "winner",
            tone: "good",
            value: "0.6731",
          },
        ]}
      />,
    );

    const row = container.querySelector(".state-list-row");
    expect(row?.getAttribute("data-state")).toBe("winner");
    expect(row?.querySelector(".chip")?.getAttribute("data-tone")).toBe("good");
  });
});
