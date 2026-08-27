import { cleanup, render } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// `ImproveLane`'s `observe` stage pulls in `uplot`, which calls
// `window.matchMedia` at import time -- unimplemented by jsdom. Mocked the
// same way `paneSmoke.test.tsx`/`ObserveStage.test.tsx`/
// `m4DissolutionCensus.test.tsx` already do; `vi.mock` is hoisted above
// these imports by Vitest.
vi.mock("uplot", () => ({ default: class {} }));

import { AnswerLane } from "../lanes/answer/AnswerLane";
import { FillLane } from "../lanes/fill/FillLane";
import { ImproveLane } from "../lanes/improve/ImproveLane";
import { LearnLane } from "../lanes/learn/LearnLane";
import { TendLane } from "../lanes/tend/TendLane";
import {
  DEFAULT_PANE,
  PANE_BY_PARAM,
  resolveLaneFocus,
  resolvePane,
} from "../paneRouting";
import type { PaneId } from "../types";

/**
 * The **whole target state** of the M5 Home wave (`IMPLEMENTATION_PLAN.md`
 * Step 112), written before Steps 113-115 land -- the M3/M4 lesson applied
 * directly a third time: a post-hoc census only catches a missed batch
 * because it asserts the *whole* target state, and both prior milestones
 * wrote their own census before any lane build started. This suite is that
 * backstop for M5. It is deliberately **RED on arrival** for the Home-repoint
 * groups and stays RED, shrinking one assertion at a time, until Step 115
 * lands.
 *
 * Five independently falsifiable groups, matching the plan's own (a)-(e):
 *
 *   (a) `PaneId`'s final union includes `"home"` (text-scanned from
 *       `types.ts` -- never a literal type assertion, for the same reason
 *       `m4DissolutionCensus.test.tsx`/`crossLaneLinkCensus.test.ts` already
 *       established: comparing a not-yet-valid literal against `PaneId` via a
 *       typed equality check would fail `tsc --noEmit` for the *entire
 *       project* the moment this file lands, long before Step 115 adds the
 *       member) -- un-REDs at Step 115.
 *   (b) `DEFAULT_PANE` is `"home"`, no longer the M1 interim `"tend"`
 *       stand-in, and the bare URL (no `?pane=`, no `?lane=`) resolves there
 *       through it -- un-REDs at Step 115.
 *   (c) `PANE_BY_PARAM.get("home")` is `"home"`, no longer `"tend"`, and the
 *       `?lane=home` deep-link form (`resolveLaneFocus`) resolves the same
 *       way -- un-REDs at Step 115. Every *other* legacy key's resolution is
 *       asserted unchanged -- additive-only regression on the repoint,
 *       mirroring every prior milestone's own additive-only checks
 *       (`crossLaneLinkCensus.test.ts`'s own such group) -- already green.
 *   (d) the final six-lane surface renders end-to-end: `ImproveLane`/
 *       `TendLane`/`LearnLane`/`AnswerLane`/`FillLane` are regression-only
 *       (unaffected by this wave, already green); `HomeLane` un-REDs at Step
 *       113. The nav's Home tab un-REDs at Step 115; the nav's five existing
 *       tabs are regression-only, already green.
 *   (e) the promoted cross-lane-navigation census: a source-scan across
 *       every `.tsx` file under `dashboard/src/lanes/**` asserting
 *       `onOpen`-shaped props appear **only** inside `dashboard/src/lanes/
 *       home/**` -- extending M3's Step 83 one-time assertion and every
 *       per-lane negative check since (`ProveStage`, `IngestGateStage`,
 *       others) into one permanent, named regression net now that Home is
 *       the invariant's real occupant, not a placeholder. `lanes/home/`
 *       doesn't exist on disk yet, so this is **already green, vacuously**
 *       -- nothing yet exists to violate it, and it stays green once
 *       `HomeLane`'s own `onOpenLane` prop lands inside `lanes/home/`
 *       (Step 113), because that subtree is the one place this check
 *       permits it.
 *
 * `HomeLane` does not exist on disk yet, so it is loaded through a
 * non-literal dynamic `import()` -- the same device `m4DissolutionCensus.
 * test.tsx` used for `LearnLane`/`FillLane` before their own steps landed: a
 * literal `import { HomeLane } from "../lanes/home/HomeLane"` would fail
 * `tsc --noEmit` for the whole project the instant this file lands.
 *
 * Load-bearing assumption (full reasoning in
 * `LEARNINGS_test-engineer_step112.md`): `HomeLane`'s smoke-render props
 * (`client`, `vault`, `onOpenLane`) are a best-effort guess read off Step
 * 113's own implementation text (`client.wikiStatus("", vault, "attention")`
 * on mount; `onOpenLane: (lane: PaneId) => void` passed down from `App.tsx`),
 * not real code -- leaning on this codebase's established cross-cutting
 * invariant that every pane/lane tolerates `client: null` gracefully.
 * Whichever implementer lands Step 113 wins on conflict; if the real props
 * differ enough to throw at render, that one sub-assertion stays RED past
 * its named un-RED step and names exactly which prop assumption to revisit
 * -- the backstop doing its job either way.
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` are loaded via
 * a dynamic `import()` with a variable specifier, the same technique
 * `crossLaneLinkCensus.test.ts`/`m4DissolutionCensus.test.tsx` already use.
 */

interface FsModule {
  readdirSync(path: string): string[];
  statSync(path: string): { isDirectory(): boolean };
  readFileSync(path: string, encoding: string): string;
  existsSync(path: string): boolean;
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

let fsModule: FsModule;
let pathModule: PathModule;
let srcDir: string;
let appSource: string;
let typesSource: string;

beforeAll(async () => {
  fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  srcDir = pathModule.join(testDir, "..");
  appSource = fsModule.readFileSync(
    pathModule.join(srcDir, "App.tsx"),
    "utf-8",
  );
  typesSource = fsModule.readFileSync(
    pathModule.join(srcDir, "types.ts"),
    "utf-8",
  );
});

afterEach(cleanup);

/** Walks `dir`, skipping `__tests__`, returning every `.ts`/`.tsx` file. */
function collectSourceFiles(dir: string): string[] {
  if (!fsModule.existsSync(dir)) return [];
  const entries = fsModule.readdirSync(dir);
  const files: string[] = [];
  for (const entry of entries) {
    if (entry === "__tests__") continue;
    const full = pathModule.join(dir, entry);
    if (fsModule.statSync(full).isDirectory()) {
      files.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

describe("(a) PaneId reaches its final M5 union (un-REDs at Step 115)", () => {
  function paneIdUnionMembers(): string[] {
    const match = typesSource.match(/export type PaneId = ([^;]+);/);
    if (!match) {
      throw new Error("PaneId type alias not found in types.ts");
    }
    return match[1]
      .split("|")
      .map((entry) => entry.trim().replace(/^"|"$/g, ""));
  }

  it("PaneId includes home", () => {
    expect(paneIdUnionMembers()).toContain("home");
  });
});

describe("(b) DEFAULT_PANE lands on home, not the M1 interim tend stand-in (un-REDs at Step 115)", () => {
  it("DEFAULT_PANE is home", () => {
    expect(DEFAULT_PANE).toBe("home");
  });

  it("the bare URL (no ?pane=, no ?lane=) resolves to home via DEFAULT_PANE", () => {
    expect(resolvePane(null)).toBe("home");
  });
});

describe("(c) PANE_BY_PARAM repoints home to itself, not tend", () => {
  describe("target-state repoint (un-REDs at Step 115)", () => {
    it("?pane=home now resolves to home, not tend", () => {
      expect(PANE_BY_PARAM.get("home")).toBe("home");
    });

    it("?lane=home (the open_dashboard deep-link form) resolves the same way", () => {
      expect(resolveLaneFocus("home", "")).toBe("home");
    });
  });

  describe("every other legacy key's resolution is unaffected by the home repoint (regression, already green)", () => {
    const UNCHANGED: ReadonlyArray<readonly [string, string]> = [
      ["datasets", "improve"],
      ["golden", "improve"],
      ["ingest", "learn"],
      ["loop", "improve"],
      ["ask", "answer"],
      ["arena", "improve"],
      ["sources", "fill"],
      ["notes", "tend"],
      ["learn", "learn"],
      ["answer", "answer"],
      ["improve", "improve"],
      ["fill", "fill"],
      ["tend", "tend"],
    ];

    it.each(UNCHANGED)("?pane=%s still resolves to %s", (param, pane) => {
      expect(PANE_BY_PARAM.get(param)).toBe(pane);
    });
  });
});

/**
 * Anchored on the `<nav class="pane-tabs" ...> ... </nav>` block
 * specifically (not a bare label match), mirroring
 * `crossLaneLinkCensus.test.ts`/`m4DissolutionCensus.test.tsx`'s own helper
 * -- a label appearing elsewhere on the page cannot produce a false
 * pass/fail.
 */
function paneTabsBlock(): string {
  const start = appSource.indexOf('<nav class="pane-tabs"');
  const end = appSource.indexOf("</nav>", start);
  return appSource.slice(start, end);
}

describe("(d) the final six-lane surface renders end-to-end", () => {
  const HOME_LANE_MODULE_PATH = "../lanes/home/HomeLane";

  interface HomeLaneProps {
    client: null;
    vault: string;
    onOpenLane: (lane: PaneId) => void;
  }
  type HomeLaneComponent = (props: Partial<HomeLaneProps>) => JSX.Element;

  function expectNonEmptyRender(container: { textContent: string | null }) {
    expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  }

  describe("component-level smoke render", () => {
    it("renders ImproveLane (regression -- unaffected by this wave, already green)", () => {
      const { container } = render(
        <ImproveLane
          client={null}
          topic="t"
          vault="v"
          status={null}
          metrics={null}
          obsidianCtx={{}}
        />,
      );
      expectNonEmptyRender(container);
    });

    it("renders TendLane (regression -- unaffected by this wave, already green)", () => {
      const { container } = render(
        <TendLane client={null} vault="v" topic="t" obsidianCtx={{}} />,
      );
      expectNonEmptyRender(container);
    });

    it("renders LearnLane (regression -- unaffected by this wave, already green)", () => {
      const { container } = render(
        <LearnLane client={null} topic="t" vault="v" obsidianCtx={{}} />,
      );
      expectNonEmptyRender(container);
    });

    it("renders AnswerLane (regression -- unaffected by this wave, already green)", () => {
      const { container } = render(
        <AnswerLane
          client={null}
          topic="t"
          vault="v"
          obsidianCtx={{}}
          status={null}
        />,
      );
      expectNonEmptyRender(container);
    });

    it("renders FillLane (regression -- unaffected by this wave, already green)", () => {
      const { container } = render(
        <FillLane client={null} topic="t" vault="v" status={null} />,
      );
      expectNonEmptyRender(container);
    });

    it("renders HomeLane (un-REDs at Step 113)", async () => {
      const { HomeLane } = (await import(HOME_LANE_MODULE_PATH)) as {
        HomeLane: HomeLaneComponent;
      };
      const { container } = render(
        <HomeLane client={null} vault="v" onOpenLane={() => {}} />,
      );
      expectNonEmptyRender(container);
    });
  });

  describe("nav shows the Home tab (un-REDs at Step 115)", () => {
    it("nav renders a Home tab", () => {
      expect(paneTabsBlock()).toMatch(/>\s*Home\s*[<{]/);
    });
  });

  describe("nav still shows the five existing lane tabs (regression, already green)", () => {
    it.each(["Improve", "Tend", "Learn", "Answer", "Fill"])(
      "nav renders a %s tab",
      (label) => {
        expect(paneTabsBlock()).toMatch(new RegExp(`>\\s*${label}\\s*[<{]`));
      },
    );
  });
});

describe("(e) the promoted cross-lane-navigation census (already green -- nothing yet to violate)", () => {
  /**
   * `onOpenReject` (`lanes/fill/QueueStage.tsx`) is a same-file dialog
   * toggle, not cross-lane navigation -- the exact false positive
   * `crossLaneLinkCensus.test.ts`'s own docstring already flagged as an
   * imprecision in the design doc's literal "bare `onOpen`" wording. Carried
   * forward here as a permanent, named exclusion rather than re-discovering
   * the same collision silently.
   */
  const KNOWN_NON_NAVIGATIONAL_EXCEPTIONS = ["onOpenReject"];

  function onOpenPropNames(file: string): string[] {
    const content = fsModule.readFileSync(file, "utf-8");
    const matches = content.match(/\bonOpen[A-Z]\w*\b/g) ?? [];
    return matches.filter(
      (name) => !KNOWN_NON_NAVIGATIONAL_EXCEPTIONS.includes(name),
    );
  }

  it("no lane outside lanes/home declares an onOpen-shaped navigation prop", () => {
    const lanesDir = pathModule.join(srcDir, "lanes");
    const files = collectSourceFiles(lanesDir).filter(
      (file) => file.endsWith(".tsx") && !file.includes("/lanes/home/"),
    );
    const offenders: string[] = [];
    for (const file of files) {
      for (const name of onOpenPropNames(file)) {
        offenders.push(`${file}: ${name}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
