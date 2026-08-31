import { cleanup, render } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { Icon } from "../icons";
import type { IconName } from "../icons";

/**
 * Exhaustive render smoke test for the 26-glyph inventory. A
 * missing case in `icons.tsx`'s `GLYPHS` record is a TypeScript compile
 * error, but this test is the runtime proof that every declared name
 * actually mounts an `<svg>` -- and that the shared stroke contract
 * (`currentColor`, `aria-hidden`, no fill) holds for every one of them.
 */

const ALL_ICON_NAMES: IconName[] = [
  "lane:home",
  "lane:learn",
  "lane:answer",
  "lane:improve",
  "lane:fill",
  "lane:tend",
  "state:pending",
  "state:active",
  "state:complete",
  "state:blocked",
  "state:unknown",
  "state:running",
  "stage:instrument",
  "stage:observe",
  "stage:gate",
  "stage:heal",
  "stage:promote",
  "stage:prove",
  "info",
  "close",
  "chevron-right",
  "copy",
  "external-link",
  "plus",
  "refresh",
  "search",
];

afterEach(cleanup);

describe("the icon inventory", () => {
  it("has exactly 26 glyphs", () => {
    expect(ALL_ICON_NAMES).toHaveLength(26);
  });

  it.each(ALL_ICON_NAMES)("renders %s as a CSP-safe inline svg", (name) => {
    const { container } = render(<Icon name={name} />);

    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg?.getAttribute("fill")).toBe("none");
    expect(svg?.getAttribute("stroke")).toBe("currentColor");
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.querySelector("path, circle")).toBeTruthy();
  });

  it("defaults to a 16px box and scales to the two other fixed sizes", () => {
    const { container: small } = render(<Icon name="info" />);
    const { container: card } = render(<Icon name="info" size={20} />);
    const { container: chrome } = render(<Icon name="info" size={24} />);

    expect(small.querySelector("svg")?.getAttribute("width")).toBe("16");
    expect(card.querySelector("svg")?.getAttribute("width")).toBe("20");
    expect(chrome.querySelector("svg")?.getAttribute("width")).toBe("24");
  });
});
