import { beforeAll, describe, expect, it } from "vitest";

/**
 * The WCAG 2.2 AA text-contrast floor, computed from the shipped tokens rather
 * than eyeballed.
 *
 * `td-060` and `td-069` were the same failure twice: a semantic tone used as
 * small text against a surface it does not clear 4.5:1 on -- first the light
 * theme's `--good`/`--warn`/`--running` (~2.8-3.2:1), then the dark theme's
 * `--bad` (3.84:1). Both were found by measuring after the fact. This suite is
 * the gate that stops the third one: it parses `theme.css`, resolves every
 * `var()` indirection, and computes the real ratio for each (tone, surface)
 * pair on **both** themes.
 *
 * It deliberately checks the *text* floor (4.5:1) for every semantic tone,
 * because every one of them is used as a `color:` somewhere in `app.css` --
 * chips, state labels, `.loop-node-state`, `output.observe-chip`. A tone that
 * is only ever a border would be over-constrained here; none is.
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` load through a
 * dynamic `import()` with a variable specifier, the same device
 * `toolClientMethodCensus.test.ts` uses.
 */

interface FsModule {
  readFileSync(path: string, encoding: string): string;
}
interface PathModule {
  dirname(path: string): string;
  join(...parts: string[]): string;
}
interface UrlModule {
  fileURLToPath(url: string): string;
}

const FS_MODULE_NAME = "fs";
const PATH_MODULE_NAME = "path";
const URL_MODULE_NAME = "url";

/** WCAG 2.2 AA, 1.4.3 Contrast (Minimum) -- body text and UI labels. */
const TEXT_FLOOR = 4.5;

/** Every semantic tone `app.css` sets as a `color:`. */
const TONES = [
  "accent",
  "good",
  "bad",
  "warn",
  "running",
  "neutral",
  "text",
  "muted",
] as const;

/** Every surface those tones are painted on. */
const SURFACES = ["bg", "surface", "surface-raised", "surface-sunken"] as const;

let themeSource: string;

beforeAll(async () => {
  const fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  const pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  themeSource = fsModule.readFileSync(
    pathModule.join(testDir, "..", "theme.css"),
    "utf-8",
  );
});

/**
 * The declarations inside one top-level rule, keyed by custom-property name.
 * Selectors are matched literally rather than by a CSS parser: the file has
 * three flat blocks, none of them nested, so the block body is everything
 * between the selector's own `{` and the next `}`. The `@media` wrapper is
 * never a selector passed here -- its inner `:root:not(...)` rule is.
 */
function declarationsIn(selector: string): Record<string, string> {
  const start = themeSource.indexOf(selector);
  expect(start, `theme.css declares ${selector}`).toBeGreaterThanOrEqual(0);
  const open = themeSource.indexOf("{", start);
  const end = themeSource.indexOf("}", open);
  const body = themeSource.slice(open + 1, end);

  const declarations: Record<string, string> = {};
  for (const match of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    declarations[match[1].slice(2)] = match[2].trim();
  }
  return declarations;
}

/** Follows `var(--x)` chains until a literal `#rrggbb` falls out. */
function resolve(
  name: string,
  block: Record<string, string>,
  root: Record<string, string>,
): string {
  let value: string | undefined = block[name] ?? root[name];
  for (let hops = 0; hops < 8; hops += 1) {
    expect(value, `theme.css defines --${name}`).toBeDefined();
    const reference = /^var\(\s*--([a-z0-9-]+)\s*\)$/.exec(value as string);
    if (!reference) break;
    const next: string | undefined =
      block[reference[1]] ?? root[reference[1]];
    value = next;
  }
  expect(value, `--${name} resolves to a hex literal`).toMatch(
    /^#[0-9a-f]{6}$/i,
  );
  return value as string;
}

/** Relative luminance, WCAG 2.x definition. */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => {
    const channel = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return channel <= 0.03928
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrastRatio(foreground: string, background: string): number {
  const a = luminance(foreground);
  const b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/**
 * `button.danger` paints `--bad` text on a fill mixed from `--bad` itself
 * (`app.css`: 10% at rest, 16% on hover), so the surface moves toward the
 * text as the tone changes -- the one place a "darken the token" fix can
 * silently fail to buy what it looks like it bought. These are the tightest
 * (tone, surface) pairs on the whole surface, so the gate computes them.
 */
const DANGER_FILL_RATIOS = [0.1, 0.16] as const;

function mix(foreground: string, background: string, weight: number): string {
  const channel = (offset: number): string => {
    const a = parseInt(foreground.slice(offset, offset + 2), 16);
    const b = parseInt(background.slice(offset, offset + 2), 16);
    return Math.round(a * weight + b * (1 - weight))
      .toString(16)
      .padStart(2, "0");
  };
  return `#${channel(1)}${channel(3)}${channel(5)}`;
}

interface Theme {
  readonly label: string;
  readonly block: Record<string, string>;
}

function themes(): Theme[] {
  const root = declarationsIn(":root {");
  return [
    { label: "light", block: root },
    { label: "dark", block: declarationsIn(':root[data-theme="dark"]') },
  ];
}

describe("every semantic tone clears the AA text floor on both themes", () => {
  it("resolves and measures each (tone, surface) pair", () => {
    const root = declarationsIn(":root {");
    const offenders: string[] = [];

    for (const { label, block } of themes()) {
      for (const tone of TONES) {
        const foreground = resolve(tone, block, root);
        for (const surface of SURFACES) {
          const background = resolve(surface, block, root);
          const ratio = contrastRatio(foreground, background);
          if (ratio < TEXT_FLOOR) {
            offenders.push(
              `${label}: --${tone} (${foreground}) on --${surface} (${background}) = ${ratio.toFixed(2)}:1`,
            );
          }
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it("keeps button.danger legible on its own tinted fill", () => {
    const root = declarationsIn(":root {");
    const offenders: string[] = [];

    for (const { label, block } of themes()) {
      const bad = resolve("bad", block, root);
      const surface = resolve("surface", block, root);
      for (const weight of DANGER_FILL_RATIOS) {
        const fill = mix(bad, surface, weight);
        const ratio = contrastRatio(bad, fill);
        if (ratio < TEXT_FLOOR) {
          offenders.push(
            `${label}: --bad (${bad}) on ${weight * 100}% fill (${fill}) = ${ratio.toFixed(2)}:1`,
          );
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  /**
   * The dark palette is written twice -- once under
   * `@media (prefers-color-scheme: dark)` for the system preference and once
   * under `[data-theme="dark"]` for the explicit toggle. The suite above
   * measures the second; this keeps the first from drifting out from under it,
   * which is exactly how a theme-scoped fix ships half-applied.
   */
  it("keeps both dark-theme rendering paths on identical token values", () => {
    const media = declarationsIn(':root:not([data-theme="light"])');
    const explicit = declarationsIn(':root[data-theme="dark"]');
    expect(media).toEqual(explicit);
  });
});
