import { describe, expect, it, beforeAll } from "vitest";

import { PANE_BY_PARAM, resolveLaneFocus, resolvePane } from "../paneRouting";
import type { PaneId } from "../types";

/**
 * The removal phase of the `ImproveLane`/`TendLane` dissolution
 * (`INTERFACE_DESIGN.md §2.0`) — this file's own enumeration is what named
 * the files to delete.
 *
 * **All eight pane modules are deleted**: `VaultPane.tsx`, `LoopPane.tsx`,
 * `CompilePanel.tsx`, `ScoreboardPanel.tsx`, `ArenaPane.tsx`,
 * `DatasetsPane.tsx`, `NotesPane.tsx`, `NotesDriftView.tsx`. The first six
 * were absorbed by `TendLane` and `ImproveLane`'s six stages; the last two
 * by `DriftStage`, which landed later than this suite was first written and
 * unblocked the two `it.skip`s that used to guard them.
 *
 * **`NotePromoteDialog.tsx` is deliberately absent from every list here**,
 * as is the extracted `notePresentation.tsx`. Both are *reused* by
 * `DriftStage.tsx` rather than dissolved — a module a survivor still imports
 * did not die, it moved conceptually. Asserting their absence would delete a
 * live dependency.
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` are loaded via
 * a dynamic `import()` with a variable specifier, the same technique
 * `ProveStage.test.tsx` and `paneRouting.improveTend.test.ts` already use.
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
});

/** Walks `dashboard/src`, skipping `__tests__`, returning every `.ts`/`.tsx` file. */
function collectSourceFiles(dir: string): string[] {
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

const DISSOLVED_FILES = [
  "VaultPane.tsx",
  "LoopPane.tsx",
  "CompilePanel.tsx",
  "ScoreboardPanel.tsx",
  "ArenaPane.tsx",
  "DatasetsPane.tsx",
  "NotesPane.tsx",
  "NotesDriftView.tsx",
] as const;

describe("every dissolved pane module is gone from dashboard/src", () => {
  it.each(DISSOLVED_FILES)("%s no longer exists in dashboard/src", (name) => {
    expect(fsModule.existsSync(pathModule.join(srcDir, name))).toBe(false);
  });
});

describe("no surviving file imports a dissolved pane module by its module specifier", () => {
  function survivors(): string[] {
    return collectSourceFiles(srcDir).filter(
      (file) => !DISSOLVED_FILES.some((name) => file.endsWith(`/${name}`)),
    );
  }

  it.each(DISSOLVED_FILES)("nothing imports %s once it is deleted", (name) => {
    const moduleName = name.replace(/\.tsx?$/, "");
    const importPattern = new RegExp(
      `from\\s+["'](?:\\./|\\.\\./)+${moduleName}["']`,
    );
    const offenders = survivors().filter((file) =>
      importPattern.test(fsModule.readFileSync(file, "utf-8")),
    );
    expect(offenders).toEqual([]);
  });
});

/**
 * `INTERFACE_DESIGN.md §2.0` clause 3 names exactly four retiring cross-lane
 * prop identifiers (`onOpenArena`/`onOpenAsk`/`onOpenVault` on `LoopPane`,
 * `onOpenAsk`/`onOpenLoop` on `ArenaPane` — the ask pane's own `onOpenLoop`/
 * `onOpenArena` went earlier, with its cross-lane banners). The census below
 * matches those four names precisely rather than the bare `onOpen` substring
 * the design doc's own "mechanical check" line uses — a bare substring match
 * collides with the suggestion queue's unrelated internal `onOpenReject` prop
 * (a same-file dialog toggle, not cross-lane navigation, and still live in
 * `lanes/fill/QueueStage.tsx`), which would false-positive forever. Flagged
 * as an imprecision in the design doc's literal wording, not guessed past
 * silently.
 */
const CROSS_LANE_PROPS = [
  "onOpenArena",
  "onOpenAsk",
  "onOpenVault",
  "onOpenLoop",
] as const;

describe("no survivor outside App.tsx/lanes declares a retiring cross-lane onOpen* prop (§2.0 clause 3)", () => {
  it("finds zero occurrences of the four retiring prop names", () => {
    const files = collectSourceFiles(srcDir).filter(
      (file) => !file.endsWith("/App.tsx") && !file.includes("/lanes/"),
    );
    const offenders: string[] = [];
    for (const file of files) {
      const content = fsModule.readFileSync(file, "utf-8");
      for (const prop of CROSS_LANE_PROPS) {
        if (new RegExp(`\\b${prop}\\b`).test(content)) {
          offenders.push(`${file}: ${prop}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("every legacy ?pane= value degrades to the lane that absorbed it (REQ-11b)", () => {
  const REPOINTED: ReadonlyArray<readonly [string, PaneId]> = [
    ["loop", "improve"],
    ["vault", "tend"],
    ["arena", "improve"],
    ["datasets", "improve"],
    ["golden", "improve"],
    ["notes", "tend"],
  ];

  it.each(REPOINTED)("?pane=%s now resolves to %s", (param, pane) => {
    expect(resolvePane(param)).toBe(pane);
  });

  it("?pane=home self-maps now that Home is a lane, no longer parked on tend", () => {
    // `home` was the one lane key this table used to carry: with no Home lane
    // to open, it was parked on the interim default. It is a destination now,
    // not a repoint.
    expect(resolvePane("home")).toBe("home" as PaneId);
  });

  it("the bare deep link (no ?pane=) lands on home, mirroring ?pane=home", () => {
    expect(resolvePane(null)).toBe("home" as PaneId);
  });

  it("an unrecognised value degrades to the current default (home), not the retired vault default", () => {
    expect(resolvePane("not-a-real-pane")).toBe("home" as PaneId);
  });
});

describe("pre-existing topical and new-lane resolutions are unaffected by the removal phase", () => {
  const UNCHANGED: ReadonlyArray<readonly [string, PaneId]> = [
    ["learn", "learn"],
    ["answer", "answer"],
    ["fill", "fill"],
    ["improve", "improve"],
    ["tend", "tend"],
  ];

  it.each(UNCHANGED)("still resolves %s to %s", (param, pane) => {
    expect(resolvePane(param)).toBe(pane);
  });
});

describe("PANE_BY_PARAM never maps a key to a retired PaneId once the removal phase lands", () => {
  it("every value in the allowlist is a surviving pane", () => {
    const retired = new Set([
      "vault",
      "loop",
      "arena",
      "datasets",
      "golden",
      "notes",
      "ask",
      "ingest",
      "sources",
    ]);
    const offendingValues = [...PANE_BY_PARAM.values()].filter((value) =>
      retired.has(value),
    );
    expect(offendingValues).toEqual([]);
  });
});

describe("PANE_BY_LANE_FOCUS's qualified pane-opening entries are gone -- focus only expands a stage now", () => {
  it("improve:heal falls through to the bare improve lane, not the retired arena pane", () => {
    expect(resolveLaneFocus("improve", "heal")).toBe("improve" as PaneId);
  });

  it("improve:instrument falls through to the bare improve lane, not the retired datasets pane", () => {
    expect(resolveLaneFocus("improve", "instrument")).toBe("improve" as PaneId);
  });

  it("tend:drift falls through to the bare tend lane, not the retired notes pane", () => {
    expect(resolveLaneFocus("tend", "drift")).toBe("tend" as PaneId);
  });
});

/**
 * Anchored on the `<nav class="pane-tabs" ...> ... </nav>` block specifically
 * (not a bare label match) so a label appearing elsewhere on the page (e.g. a
 * heading) cannot produce a false pass/fail.
 */
function paneTabsBlock(): string {
  const start = appSource.indexOf('<nav class="pane-tabs"');
  const end = appSource.indexOf("</nav>", start);
  return appSource.slice(start, end);
}

describe("the pane-tabs nav shows lanes, not the tabs the dissolution retired", () => {
  it.each(["Vault", "Loop", "Notes", "Arena", "Datasets"])(
    "no longer renders a %s tab",
    (label) => {
      expect(paneTabsBlock()).not.toMatch(new RegExp(`>\\s*${label}\\s*[<{]`));
    },
  );

  it.each(["Improve", "Tend"])("still renders the %s tab", (label) => {
    expect(paneTabsBlock()).toMatch(new RegExp(`>\\s*${label}\\s*[<{]`));
  });
});

describe("the relocated gate-note sentence exists exactly once now that VaultPane.tsx is gone", () => {
  const GATE_NOTE_SENTENCE =
    "Read-only here. Gating a candidate is billed and two-phase, and lives on";

  it("appears in exactly one file under dashboard/src", () => {
    const matches = collectSourceFiles(srcDir).filter((file) =>
      fsModule.readFileSync(file, "utf-8").includes(GATE_NOTE_SENTENCE),
    );
    expect(matches).toHaveLength(1);
  });
});
