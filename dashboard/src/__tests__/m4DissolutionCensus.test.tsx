import { cleanup, render } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// `ImproveLane`'s `observe` stage pulls in `uplot`, which calls
// `window.matchMedia` at import time -- unimplemented by jsdom. Mocked the
// same way `paneSmoke.test.tsx`/`ObserveStage.test.tsx` already do; `vi.mock`
// is hoisted above these imports by Vitest.
vi.mock("uplot", () => ({ default: class {} }));

import { AnswerLane } from "../lanes/answer/AnswerLane";
import { ImproveLane } from "../lanes/improve/ImproveLane";
import { TendLane } from "../lanes/tend/TendLane";
import { resolvePane } from "../paneRouting";

/**
 * The **whole target state** of the M4 dissolution wave (`IMPLEMENTATION_PLAN.md`
 * Step 91), written before any of Steps 92-104 land -- the M3 lesson applied
 * directly: M3's own post-hoc census (Step 80) caught a missed batch only
 * because it asserted the *whole* target state, not one lane at a time. This
 * suite is that backstop for M4. It is deliberately **RED on arrival** and
 * stays RED, shrinking one assertion at a time, until Step 104 lands.
 *
 * Five independently falsifiable groups, matching the plan's own (a)-(e):
 *
 *   (a) `PaneId`'s final union shape (text-scanned from `types.ts` -- never a
 *       literal type assertion. Comparing a not-yet-valid literal like
 *       `"learn"` against `PaneId` via `as PaneId`/a typed equality check
 *       would fail `tsc --noEmit` for the *entire project* the moment this
 *       file lands, long before Step 102 adds those members. A source-text
 *       regex sidesteps the type system entirely, exactly as
 *       `crossLaneLinkCensus.test.ts`'s own `PANE_BY_PARAM`-values check
 *       avoided the same trap for the M3 removal phase).
 *         - inclusion of `learn`/`answer`/`fill` -- un-REDs at Step 102
 *         - exclusion of `ingest`/`ask`/`sources` -- un-REDs at Step 104
 *   (b) the three dissolved files are gone from disk and unreferenced by any
 *       survivor -- un-REDs at Step 104. Same absence + no-import-survives
 *       technique `crossLaneLinkCensus.test.ts` used for the eight
 *       `ImproveLane`/`TendLane`-absorbed panes.
 *   (c) the legacy `?pane=ingest|ask|sources` keys repoint to
 *       `learn`/`answer`/`fill` respectively -- un-REDs at Step 104 (Step 102
 *       is additive-only by design and leaves the legacy keys self-mapped).
 *   (d) the final five-pane surface renders end-to-end: `ImproveLane`/
 *       `TendLane` are regression-only (unaffected by this wave, already
 *       green); `AnswerLane` is already green (Step 94 landed); `LearnLane`
 *       un-REDs at Step 92; `FillLane` un-REDs at Step 100. The nav's three
 *       new tabs un-RED at Step 102; the nav's three retired tabs un-RED at
 *       Step 104.
 *   (e) `LearnLane`'s subtree embeds exactly one `HandoffStage` dispatching
 *       `/knotica:ingest` (un-REDs at Step 92); `FillLane`'s subtree embeds
 *       exactly one, dispatching `/knotica:fill` (un-REDs at Step 98, when
 *       `IngestGateStage.tsx` -- not `FillLane.tsx` itself -- adds the tag);
 *       `AnswerLane`'s subtree embeds zero (already green -- its `react`
 *       stage terminates in-lane, no handoff needed).
 *
 * `LearnLane`/`FillLane` do not exist on disk yet, so they are loaded through
 * a non-literal dynamic `import()` -- the same device `QueueStage.test.tsx`/
 * `hostCapabilities.test.ts` used for their own not-yet-existing modules: a
 * literal `import { LearnLane } from "../lanes/learn/LearnLane"` would fail
 * `tsc --noEmit` for the whole project the instant this file lands.
 *
 * Load-bearing assumption (full reasoning in
 * `LEARNINGS_test-engineer_step91.md`): `LearnLane`/`FillLane`'s smoke-render
 * props are a best-effort superset guess (`LearnLane` mirrors the
 * `IngestPane` props it absorbs; `FillLane` mirrors `QueueStage`'s), leaning
 * on this codebase's established cross-cutting invariant that every pane/lane
 * tolerates `client: null` and nullable `status`/`obsidianCtx` gracefully.
 * Whichever implementer lands Steps 92/100 wins on conflict; if the real
 * props differ enough to throw at render, that sub-assertion stays RED and
 * names exactly which prop assumption to revisit -- the backstop doing its
 * job either way.
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` are loaded via
 * a dynamic `import()` with a variable specifier, the same technique
 * `crossLaneLinkCensus.test.ts` already uses.
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

describe("(a) PaneId reaches its final M4 union", () => {
  function paneIdUnionMembers(): string[] {
    const match = typesSource.match(/export type PaneId = ([^;]+);/);
    if (!match) {
      throw new Error("PaneId type alias not found in types.ts");
    }
    return match[1]
      .split("|")
      .map((entry) => entry.trim().replace(/^"|"$/g, ""));
  }

  describe("target-state additions (un-REDs at Step 102)", () => {
    it.each(["learn", "answer", "fill"])("PaneId includes %s", (member) => {
      expect(paneIdUnionMembers()).toContain(member);
    });
  });

  describe("target-state removals (un-REDs at Step 104)", () => {
    it.each(["ingest", "ask", "sources"])(
      "PaneId no longer includes %s",
      (member) => {
        expect(paneIdUnionMembers()).not.toContain(member);
      },
    );
  });
});

describe("(b) the three dissolved pane files are gone and unreferenced (un-REDs at Step 104)", () => {
  const DISSOLVED_FILES = [
    "IngestPane.tsx",
    "SourcesPane.tsx",
    "AskPane.tsx",
  ] as const;

  describe("absence from disk", () => {
    it.each(DISSOLVED_FILES)("%s no longer exists in dashboard/src", (name) => {
      expect(fsModule.existsSync(pathModule.join(srcDir, name))).toBe(false);
    });
  });

  describe("no survivor imports them", () => {
    function survivors(): string[] {
      return collectSourceFiles(srcDir).filter(
        (file) => !DISSOLVED_FILES.some((name) => file.endsWith(`/${name}`)),
      );
    }

    it.each(DISSOLVED_FILES)(
      "nothing imports %s once it is deleted",
      (name) => {
        const moduleName = name.replace(/\.tsx?$/, "");
        const importPattern = new RegExp(
          `from\\s+["'](?:\\./|\\.\\./)+${moduleName}["']`,
        );
        const offenders = survivors().filter((file) =>
          importPattern.test(fsModule.readFileSync(file, "utf-8")),
        );
        expect(offenders).toEqual([]);
      },
    );
  });
});

describe("(c) legacy ?pane= keys repoint to their lane replacements (un-REDs at Step 104)", () => {
  const REPOINTED: ReadonlyArray<readonly [string, string]> = [
    ["ingest", "learn"],
    ["ask", "answer"],
    ["sources", "fill"],
  ];

  it.each(REPOINTED)("?pane=%s now resolves to %s", (param, pane) => {
    expect(resolvePane(param)).toBe(pane);
  });
});

/**
 * Anchored on the `<nav class="pane-tabs" ...> ... </nav>` block specifically
 * (not a bare label match), mirroring `crossLaneLinkCensus.test.ts`'s own
 * helper -- a label appearing elsewhere on the page cannot produce a false
 * pass/fail.
 */
function paneTabsBlock(): string {
  const start = appSource.indexOf('<nav class="pane-tabs"');
  const end = appSource.indexOf("</nav>", start);
  return appSource.slice(start, end);
}

describe("(d) the final five-pane surface renders end-to-end", () => {
  const LEARN_LANE_MODULE_PATH = "../lanes/learn/LearnLane";
  const FILL_LANE_MODULE_PATH = "../lanes/fill/FillLane";

  interface LaneComponentProps {
    client: null;
    topic: string;
    vault: string;
    obsidianCtx: Record<string, never>;
    status: null;
    onStatusRefresh: () => void;
  }
  type LaneComponent = (props: Partial<LaneComponentProps>) => JSX.Element;

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

    it("renders AnswerLane (green since Step 94)", () => {
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

    it("renders LearnLane (un-REDs at Step 92)", async () => {
      const { LearnLane } = (await import(LEARN_LANE_MODULE_PATH)) as {
        LearnLane: LaneComponent;
      };
      const { container } = render(
        <LearnLane client={null} topic="t" vault="v" obsidianCtx={{}} />,
      );
      expectNonEmptyRender(container);
    });

    it("renders FillLane (un-REDs at Step 100)", async () => {
      const { FillLane } = (await import(FILL_LANE_MODULE_PATH)) as {
        FillLane: LaneComponent;
      };
      const { container } = render(
        <FillLane client={null} topic="t" vault="v" status={null} />,
      );
      expectNonEmptyRender(container);
    });
  });

  describe("nav shows the three new lane tabs (un-REDs at Step 102)", () => {
    it.each(["Learn", "Answer", "Fill"])("nav renders a %s tab", (label) => {
      expect(paneTabsBlock()).toMatch(new RegExp(`>\\s*${label}\\s*[<{]`));
    });
  });

  describe("nav retires the three old tabs (un-REDs at Step 104)", () => {
    it.each(["Ask", "Sources", "Ingest"])(
      "nav no longer renders a %s tab",
      (label) => {
        expect(paneTabsBlock()).not.toMatch(
          new RegExp(`>\\s*${label}\\s*[<{]`),
        );
      },
    );
  });
});

describe("(e) LearnLane/FillLane each embed exactly one HandoffStage; AnswerLane embeds none", () => {
  /** Collects every `<HandoffStage ... />` JSX tag under `lanes/<laneName>/`. */
  function handoffTagsUnder(laneName: string): string[] {
    const laneDir = pathModule.join(srcDir, "lanes", laneName);
    const tags: string[] = [];
    for (const file of collectSourceFiles(laneDir)) {
      const content = fsModule.readFileSync(file, "utf-8");
      const matches = content.match(/<HandoffStage[\s\S]*?\/>/g) ?? [];
      tags.push(...matches);
    }
    return tags;
  }

  it("lanes/learn embeds exactly one HandoffStage, dispatching /knotica:ingest (un-REDs at Step 92)", () => {
    const tags = handoffTagsUnder("learn");
    expect(tags).toHaveLength(1);
    expect(tags[0]).toMatch(/command="ingest"/);
  });

  it("lanes/fill embeds exactly one HandoffStage, dispatching /knotica:fill (un-REDs at Step 98)", () => {
    const tags = handoffTagsUnder("fill");
    expect(tags).toHaveLength(1);
    expect(tags[0]).toMatch(/command="fill"/);
  });

  it("lanes/answer embeds zero HandoffStage instances (already green)", () => {
    expect(handoffTagsUnder("answer")).toHaveLength(0);
  });
});
