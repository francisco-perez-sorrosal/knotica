import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { Stat, StatGrid } from "../Stat";

/**
 * The readout primitive replacing every `Label: <strong>value</strong>`
 * prose fragment (design §2.2). Absence renders `—`, never blank, never
 * `0` (round 1 §7.3), and a tone is a colour on the value only.
 */

afterEach(cleanup);

describe("Stat", () => {
  it("renders a label and a tabular-nums value", () => {
    render(<Stat label="LATEST GEN" value={12} />);

    expect(screen.getByText("LATEST GEN")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
  });

  it("renders — for a null value, not blank and not 0", () => {
    render(<Stat label="BASELINE" value={null} />);

    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("renders — for an undefined value", () => {
    render(<Stat label="HELD-OUT" value={undefined} />);

    expect(screen.getByText("—")).toBeTruthy();
  });

  it("colours only the value, never a count", () => {
    const { container } = render(<Stat label="Δ BASELINE" value="+0.0131" tone="good" />);

    const stat = container.querySelector(".stat");
    expect(stat?.getAttribute("data-tone")).toBe("good");
  });

  it("drops an explicit tone when the value is absent -- absence is neutral, not a verdict", () => {
    const { container } = render(<Stat label="BASELINE" value={null} tone="bad" />);

    const stat = container.querySelector(".stat");
    expect(stat?.hasAttribute("data-tone")).toBe(false);
  });
});

describe("StatGrid", () => {
  it("renders its children inside a stat-grid container", () => {
    render(
      <StatGrid>
        <Stat label="A" value={1} />
        <Stat label="B" value={2} />
      </StatGrid>,
    );

    const grid = document.querySelector(".stat-grid");
    expect(grid).toBeTruthy();
    expect(grid?.querySelectorAll(".stat").length).toBe(2);
  });
});
