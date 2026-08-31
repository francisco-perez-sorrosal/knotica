import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { SectionCard } from "../SectionCard";

/**
 * The stage-body grammar's container primitive (design §2.1) -- header,
 * body, optional footer, and the one hard rule that keeps it out of any
 * rail's own disclosure chain: it never carries `aria-expanded`.
 */

afterEach(cleanup);

describe("SectionCard", () => {
  it("renders a titled header, body, and footer", () => {
    render(
      <SectionCard
        title="MEASUREMENT"
        footer={<button type="button">Run eval now</button>}
      >
        <p>body content</p>
      </SectionCard>,
    );

    expect(screen.getByText("MEASUREMENT")).toBeTruthy();
    expect(screen.getByText("body content")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Run eval now" })).toBeTruthy();
  });

  it("omits the footer element when none is supplied", () => {
    render(<SectionCard title="PIPELINE">body</SectionCard>);

    expect(document.querySelector(".section-card-actions")).toBeNull();
  });

  it("never carries aria-expanded -- a card is never a disclosure", () => {
    const { container } = render(
      <SectionCard
        title="ARENA"
        tone="bad"
        headerActions={<span>4 racing</span>}
      >
        body
      </SectionCard>,
    );

    const section = container.querySelector(".section-card");
    expect(section?.hasAttribute("aria-expanded")).toBe(false);
    expect(section?.getAttribute("data-tone")).toBe("bad");
  });

  it("introduces no h3/h4 heading inside the card", () => {
    const { container } = render(
      <SectionCard title="EVAL RUN">
        <p>cadence facts</p>
      </SectionCard>,
    );

    expect(container.querySelector("h3, h4")).toBeNull();
  });
});
