import { cleanup, render, screen, waitFor } from "@testing-library/preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { afterEach, describe, expect, it, vi } from "vitest";

import { readChartPalette, useThemeVersion } from "../chartTheme";

/**
 * The chart is the one surface that carries the design language across a
 * canvas boundary by hand, so the two halves of that carry are pinned here:
 * `readChartPalette` must read the *live* token values rather than a frozen
 * copy, and a theme flip must make a chart effect re-run so it reads them
 * again. `td-058` was what happens when neither is true — a palette that
 * stops following the tokens it is supposed to alias.
 *
 * jsdom's `getComputedStyle` does not resolve custom properties from a
 * stylesheet, so the resolver is exercised against a stubbed
 * `getPropertyValue` — the same seam a browser fills from `theme.css`.
 */

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function stubComputedTokens(tokens: Record<string, string>): void {
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (token: string) => tokens[token] ?? "",
  } as unknown as CSSStyleDeclaration);
}

describe("readChartPalette reads the live token values", () => {
  it("returns whatever the computed style currently resolves", () => {
    stubComputedTokens({
      "--chart-series": " #1a6ba6 ",
      "--chart-baseline": "#8a6400",
      "--chart-axis": "#56564f",
      "--chart-grid": "#c9c8c2",
    });

    expect(readChartPalette(document.createElement("div"))).toEqual({
      series: "#1a6ba6",
      baseline: "#8a6400",
      axis: "#56564f",
      grid: "#c9c8c2",
    });
  });

  it("falls back to legible colors when a host resolves no tokens", () => {
    stubComputedTokens({});
    const palette = readChartPalette(document.createElement("div"));
    for (const value of Object.values(palette)) {
      expect(value).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});

/** A chart-shaped consumer: re-resolves the palette on every effect run. */
function PaletteProbe(): preact.JSX.Element {
  const host = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState<string[]>([]);
  const themeVersion = useThemeVersion();

  useEffect(() => {
    if (!host.current) return;
    const { series } = readChartPalette(host.current);
    setSeen((previous) => [...previous, series]);
  }, [themeVersion]);

  return (
    <div ref={host}>
      <output data-testid="resolved">{seen.join(",")}</output>
    </div>
  );
}

describe("a theme flip re-resolves the palette", () => {
  it("re-runs the chart effect when data-theme changes", async () => {
    let series = "#1a6ba6";
    vi.spyOn(window, "getComputedStyle").mockImplementation(
      () =>
        ({
          getPropertyValue: (token: string) =>
            token === "--chart-series" ? series : "",
        }) as unknown as CSSStyleDeclaration,
    );

    render(<PaletteProbe />);
    await waitFor(() =>
      expect(screen.getByTestId("resolved").textContent).toBe("#1a6ba6"),
    );

    series = "#268bd2";
    document.documentElement.dataset.theme = "dark";

    // The flip travels three hops before it is visible — `MutationObserver`
    // delivery, the hook's state bump, then the consumer's own effect — so
    // this polls rather than counting turns of the event loop.
    await waitFor(() =>
      expect(screen.getByTestId("resolved").textContent).toBe(
        "#1a6ba6,#268bd2",
      ),
    );
    delete document.documentElement.dataset.theme;
  });
});
