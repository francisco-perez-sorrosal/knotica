import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { Spinner } from "../icons";

/**
 * The house busy affordance. Its whole contract is that it adds *motion* and
 * nothing else: no glyph to the 26-name census, no text to an accessible
 * name. Every busy call site keeps its label word, so a reader who cannot
 * see the rotation still learns which verb is running.
 */

afterEach(cleanup);

describe("Spinner", () => {
  it("renders the shared refresh glyph as an aria-hidden svg carrying .spin", () => {
    const { container } = render(<Spinner />);

    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.getAttribute("class")).toBe("spin");
    expect(svg?.getAttribute("fill")).toBe("none");
    expect(svg?.getAttribute("stroke")).toBe("currentColor");
  });

  it("honours the three fixed icon sizes", () => {
    const { container } = render(<Spinner size={20} />);

    expect(container.querySelector("svg")?.getAttribute("width")).toBe("20");
  });

  it("adds no name to a button that already has one", () => {
    render(
      <button type="button" aria-busy="true">
        <Spinner />
        Approve
      </button>,
    );

    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });
});
