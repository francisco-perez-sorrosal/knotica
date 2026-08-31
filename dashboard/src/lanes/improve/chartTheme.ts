/**
 * Theme resolution for the one canvas surface in the dashboard.
 *
 * uPlot paints strokes onto a canvas, where a CSS `var()` never arrives — it
 * needs concrete color strings. Every other surface reads its colors straight
 * from `theme.css`, so the chart is the single place where the design language
 * has to be *carried across* rather than inherited. That gap is what let the
 * chart keep a hand-picked Solarized palette through a whole redesign
 * (`td-058`): the `--chart-*` tokens are now aliases of the semantic tones, and
 * this module is the seam that resolves them.
 *
 * Two halves, kept separate so each is testable on its own:
 * `readChartPalette` is a pure read of the live computed style, and
 * `useThemeVersion` is the subscription that tells a chart when to read again.
 */

import { useEffect, useState } from "preact/hooks";

/** The concrete colors one uPlot instance needs, resolved for the live theme. */
export interface ChartPalette {
  readonly series: string;
  readonly baseline: string;
  readonly axis: string;
  readonly grid: string;
}

/**
 * Fallbacks for a host that resolves no custom properties at all (jsdom's
 * `getComputedStyle` among them). They are the *light* theme's values, so a
 * palette-less host degrades to a legible chart rather than to uPlot's own
 * black-on-transparent defaults.
 */
const FALLBACK: ChartPalette = {
  series: "#1a6ba6",
  baseline: "#8a6400",
  axis: "#56564f",
  grid: "#c9c8c2",
};

/** Resolve the `--chart-*` tokens against `host`'s live computed style. */
export function readChartPalette(host: Element): ChartPalette {
  const computed = getComputedStyle(host);
  const pick = (token: string, fallback: string): string =>
    computed.getPropertyValue(token).trim() || fallback;
  return {
    series: pick("--chart-series", FALLBACK.series),
    baseline: pick("--chart-baseline", FALLBACK.baseline),
    axis: pick("--chart-axis", FALLBACK.axis),
    grid: pick("--chart-grid", FALLBACK.grid),
  };
}

/**
 * A counter that increments whenever the resolved theme could have changed.
 *
 * Include it in a chart effect's dependency list and the chart is rebuilt with
 * re-resolved colors on a theme flip. Both flip channels are watched, because
 * the app has both: `App.tsx` writes `documentElement.dataset.theme` when an
 * MCP host announces its theme, and the HTTP mount with no host follows
 * `prefers-color-scheme` instead. Watching only the first leaves a
 * system-theme flip painting the old palette until the next data refresh.
 */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const bump = (): void => setVersion((current) => current + 1);

    const root = document.documentElement;
    const observer =
      typeof MutationObserver === "undefined"
        ? undefined
        : new MutationObserver(bump);
    observer?.observe(root, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    // Optional-called: jsdom does not implement `matchMedia`, and the chart
    // must still mount there — the tests run in it.
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    query?.addEventListener("change", bump);

    return () => {
      observer?.disconnect();
      query?.removeEventListener("change", bump);
    };
  }, []);

  return version;
}
