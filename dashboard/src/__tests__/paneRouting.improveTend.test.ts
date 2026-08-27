import { describe, expect, it, beforeAll } from "vitest";

import { PANE_BY_PARAM, resolvePane } from "../paneRouting";
import type { PaneId } from "../types";

/**
 * The `ImproveLane`/`TendLane` half of the dissolution
 * (`INTERFACE_DESIGN.md` — the four absorbed panes fold into `improve`; the
 * checklist panes fold into `tend`). Two things are pinned here:
 *
 * 1. `?pane=improve` and `?pane=tend` resolve to their own `PaneId`s — the
 *    real `ImproveLane`/`TendLane` components, both landed
 *    (`dashboard/src/lanes/improve/ImproveLane.tsx`,
 *    `dashboard/src/lanes/tend/TendLane.tsx`), are reachable end to end for
 *    the first time.
 * 2. `App.tsx` mounts both components when their pane is selected, wired
 *    from the app's own poll state — `status.value`/`metrics.value`/
 *    `client`/the resolved topic and vault — not a second, lane-owned fetch.
 *
 * `App.tsx` renders through Preact, and this file intentionally has a `.ts`
 * (not `.tsx`) extension — `vitest.config.ts`'s "unit" project runs `.test.ts`
 * files under Node, with no DOM, mirroring `paneRouting.test.ts`/
 * `paneRouting.lanes.test.ts` next to it. Mounting the real `<App />` and
 * asserting on a render tree is therefore not available from this file; the
 * wiring claim is instead verified the same way
 * `ProveStage.test.tsx`'s "no cross-lane navigation prop survives anywhere
 * under lanes/improve" suite verifies a structural property no runtime
 * render can see — by reading `App.tsx`'s own source text. `fs`/`path`/`url`
 * are loaded through a dynamic `import()` whose specifier is a variable, not
 * a string literal (the project carries no `@types/node`, so a literal
 * `import ... from "node:fs"` would fail `tsc --noEmit`; a non-literal
 * specifier is left unresolved, and therefore untyped, by TypeScript, while
 * Node still resolves it at runtime).
 *
 * Load-bearing assumptions about the not-yet-landed wiring (the paired
 * implementation wins on conflict; each independently falsified):
 *
 *   1. Each pane's conditional render in `App.tsx` follows the file's own
 *      uniform shape, `{pane === "<id>" ? (\n  <Component ... />\n) : null}`
 *      — uniformly true of every surviving pane.
 *   2. `ImproveLane`/`TendLane` are threaded with exactly the prop names
 *      their own already-landed signatures declare (`ImproveLane.tsx`:
 *      `client`/`topic`/`vault`/`status`/`metrics`/`obsidianCtx`/
 *      `onStatusRefresh`; `TendLane.tsx`: `client`/`vault`/`obsidianCtx`)
 *      — not a guess, since Preact prop destructuring makes any other
 *      attribute name a silent no-op for the receiving component.
 *   3. The *values* handed to those props are the same identifiers every
 *      other pane in `App.tsx` already reads from the app-level poll —
 *      `status.value`, `metrics.value`, `client`, `topic`,
 *      `resolvedVaultName`, `obsidianCtx` — proving "fed from the app poll,"
 *      not a fresh per-lane data source. This is the one part of the
 *      assumption set that is a genuine guess about spelling (a
 *      differently-named local variable holding the same signal would still
 *      satisfy the behavior); flagged in `LEARNINGS_test-engineer_step78.md`.
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

let appSource: string;

beforeAll(async () => {
  const fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  const pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  const appPath = pathModule.join(testDir, "..", "App.tsx");
  appSource = fsModule.readFileSync(appPath, "utf-8");
});

/**
 * Extracts the `{pane === "<paneId>" ? ( ... ) : null}` block for `paneId`
 * from `App.tsx`'s source, or `null` if that pane has no such block yet.
 * Deliberately anchored on the render-conditional shape
 * (`{pane === "X" ? (`), not the bare `pane === "X"` comparison — the nav
 * tab buttons also compare `pane === "X"` (inside a `class={...}` guard),
 * and matching that occurrence first would silently pick up the wrong block.
 */
function extractPaneBlock(paneId: string): string | null {
  const marker = `{pane === "${paneId}" ? (`;
  const start = appSource.indexOf(marker);
  if (start === -1) return null;
  const end = appSource.indexOf(") : null}", start);
  if (end === -1) return null;
  return appSource.slice(start, end);
}

describe("resolvePane resolves improve/tend to their own new panes, not the absorbed ones", () => {
  it("resolves ?pane=improve to the new improve pane rather than the absorbed loop pane", () => {
    expect(resolvePane("improve")).toBe("improve" as PaneId);
  });

  it("resolves ?pane=tend to the new tend pane rather than the absorbed vault pane", () => {
    expect(resolvePane("tend")).toBe("tend" as PaneId);
  });

  it("adds both new pane ids to the PANE_BY_PARAM allowlist itself, not just resolvePane's fallback", () => {
    expect(PANE_BY_PARAM.get("improve")).toBe("improve" as PaneId);
    expect(PANE_BY_PARAM.get("tend")).toBe("tend" as PaneId);
  });
});

describe("the surviving panes' own ?pane= resolutions are untouched by the lane work", () => {
  // The keys whose target the dissolution did not move. Every key it *did*
  // repoint (`loop`/`vault`/`arena`/`datasets`/`golden`/`notes`/`home`) is
  // pinned in `crossLaneLinkCensus.test.ts`, which owns the legacy-alias table.
  const UNCHANGED: ReadonlyArray<readonly [string, PaneId]> = [
    ["ask", "ask"],
    ["ingest", "ingest"],
    ["sources", "sources"],
    ["learn", "learn"],
    ["answer", "answer"],
    ["fill", "fill"],
  ];

  it.each(UNCHANGED)("still resolves %s to %s", (param, pane) => {
    expect(resolvePane(param)).toBe(pane);
  });

  it("falls back to the default pane for an unrecognised value", () => {
    expect(resolvePane("not-a-real-pane")).toBe("tend" as PaneId);
  });
});

describe("App.tsx mounts ImproveLane and TendLane, fed from the app's own poll state", () => {
  it("imports ImproveLane from its landed lane module", () => {
    expect(appSource).toMatch(
      /import\s*\{\s*ImproveLane\s*\}\s*from\s*["']\.\/lanes\/improve\/ImproveLane["']/,
    );
  });

  it("imports TendLane from its landed lane module", () => {
    expect(appSource).toMatch(
      /import\s*\{\s*TendLane\s*\}\s*from\s*["']\.\/lanes\/tend\/TendLane["']/,
    );
  });

  it("renders ImproveLane when pane is improve", () => {
    expect(extractPaneBlock("improve")).not.toBeNull();
    expect(extractPaneBlock("improve") ?? "").toContain("<ImproveLane");
  });

  it("feeds ImproveLane from the app poll's own status/metrics/client state, not a fresh fetch", () => {
    const block = extractPaneBlock("improve") ?? "";
    expect(block).toMatch(/status=\{status\.value\}/);
    expect(block).toMatch(/metrics=\{metrics\.value\}/);
    expect(block).toMatch(/client=\{client\}/);
    expect(block).toMatch(/vault=\{resolvedVaultName\}/);
  });

  it("renders TendLane when pane is tend", () => {
    expect(extractPaneBlock("tend")).not.toBeNull();
    expect(extractPaneBlock("tend") ?? "").toContain("<TendLane");
  });

  it("feeds TendLane from the app poll's own client/vault state, not a fresh fetch", () => {
    const block = extractPaneBlock("tend") ?? "";
    expect(block).toMatch(/client=\{client\}/);
    expect(block).toMatch(/vault=\{resolvedVaultName\}/);
  });

  it("keeps every surviving pane's render block intact", () => {
    for (const survivor of ["ask", "sources", "ingest"]) {
      expect(extractPaneBlock(survivor)).not.toBeNull();
    }
  });
});
