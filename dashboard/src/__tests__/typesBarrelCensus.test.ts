import { describe, expect, it, beforeAll } from "vitest";

/**
 * Pins Step 117's `types.ts` split (`td-057`'s types half) so it cannot
 * silently regrow. This is a **regression census over already-landed code**,
 * not a paired BDD step -- Step 117 shipped 120 declarations out of
 * `dashboard/src/types.ts` into six `lanes/<lane>/types.ts` modules behind a
 * type-only barrel (see `LEARNINGS_implementer_step117.md`'s split-map
 * table); this suite is the backstop that keeps that map from drifting.
 * Green on first run is therefore expected, not a paired-step race.
 *
 * Four independently falsifiable groups:
 *
 *   (1) the barrel is `export type` only, never `export *` -- under
 *       `isolatedModules: true` an `export *` would force a real runtime
 *       module edge and reopen the `types.ts` <-> `lanes/home/types.ts`
 *       cycle at the JS level instead of the type level.
 *   (2) the root file's declaration count stays at its floor (6: `PaneId`,
 *       `AvailableVault`, `LlmAvailability`, `LaneRailStageState`,
 *       `LaneRailStageStatus`, `WikiStatus`) -- a new per-lane type added
 *       directly to the root instead of its lane fails this.
 *   (3) every lane types file exists, and every one of the 120 moved
 *       declarations is both declared in its lane file and re-exported from
 *       the barrel -- so a future accidental barrel removal (or a lane-file
 *       deletion) fails loudly here rather than silently breaking every
 *       consumer at once.
 *   (4) every declaration is imported only via a sanctioned path: the root
 *       barrel (`"./types"` / `"../types"` / `"../../types"`, depending on
 *       depth) from anywhere in `dashboard/src`, or the lane's own
 *       `"./lanes/<lane>/types"` path -- and only from `types.ts` itself.
 *       No surviving file reaches into a lane's types module directly,
 *       matching the "no import site changes" guarantee Step 117 made.
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
let typesSource: string;

beforeAll(async () => {
  fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  srcDir = pathModule.join(testDir, "..");
  typesSource = fsModule.readFileSync(
    pathModule.join(srcDir, "types.ts"),
    "utf-8",
  );
});

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

// ---------------------------------------------------------------------------
// (1) The barrel is `export type` only, never `export *`.
// ---------------------------------------------------------------------------

describe("the root barrel never uses export * (erasure must be guaranteed, not hoped for)", () => {
  it("types.ts contains no `export * from` statement", () => {
    // Anchored on the full re-export shape (`export * from "..."`), not a
    // bare `export *` substring -- the file's own header docblock mentions
    // "never `export *`" in prose, which a bare-substring check would
    // misfire on.
    expect(typesSource).not.toMatch(/export\s*\*\s*from/);
  });

  it("types.ts contains no non-type re-export (`export { ... } from`)", () => {
    // A bare `export {` (no `type` keyword) forces a real runtime module
    // edge under `isolatedModules`; `export type {` is the only form Step
    // 117 used and the only form that erases at transpile time.
    expect(typesSource.match(/export\s*\{/g)).toBeNull();
  });

  const LANES_WITH_REEXPORTS = [
    "improve",
    "tend",
    "learn",
    "answer",
    "fill",
    "home",
  ] as const;

  it.each(LANES_WITH_REEXPORTS)(
    "the %s re-export block is introduced by `export type {`",
    (lane) => {
      const pattern = new RegExp(
        `export type \\{[\\s\\S]*?\\} from "\\./lanes/${lane}/types";`,
      );
      expect(typesSource).toMatch(pattern);
    },
  );
});

// ---------------------------------------------------------------------------
// (2) The root file's declaration count stays at its floor.
// ---------------------------------------------------------------------------

describe("dashboard/src/types.ts keeps exactly the six cross-cutting declarations", () => {
  const ROOT_DECLARATIONS = [
    "PaneId",
    "AvailableVault",
    "LlmAvailability",
    "LaneRailStageState",
    "LaneRailStageStatus",
    "WikiStatus",
  ] as const;

  function rootDeclarationNames(): string[] {
    // `export type {` (a barrel re-export header) never matches this pattern
    // -- the character after "export type " is `{`, not a name -- so this
    // only catches genuine root-level `export type X = ...` / `export
    // interface X { ... }` declarations, never a re-export block.
    const matches = [
      ...typesSource.matchAll(/^export (?:type|interface) (\w+)/gm),
    ];
    return matches.map((match) => match[1]);
  }

  it("declares exactly six root-level types (a new per-lane type belongs in its lane, not here)", () => {
    expect(rootDeclarationNames()).toHaveLength(ROOT_DECLARATIONS.length);
  });

  it("the six declarations are exactly PaneId/AvailableVault/LlmAvailability/LaneRailStageState/LaneRailStageStatus/WikiStatus", () => {
    expect(rootDeclarationNames().sort()).toEqual(
      [...ROOT_DECLARATIONS].sort(),
    );
  });
});

// ---------------------------------------------------------------------------
// (3) Every lane types file exists; every moved declaration round-trips
//     through both its lane file and the barrel.
// ---------------------------------------------------------------------------

/**
 * The exact Step 117 split map, transcribed from the landed
 * `dashboard/src/types.ts` barrel and cross-checked against each
 * `lanes/<lane>/types.ts` file's own declarations (120 total: improve 46,
 * tend 37, fill 26, home 6, learn 4, answer 1). Hardcoded rather than
 * derived from the barrel itself -- deriving the expected list from the same
 * file being pinned would let a coordinated removal (barrel entry + lane
 * declaration removed together) pass silently.
 */
const MOVED_TYPES: Record<string, readonly string[]> = {
  improve: [
    "GateState",
    "LoopStage",
    "ArenaStage",
    "DatasetRole",
    "DatasetFileRow",
    "DatasetsInventory",
    "DatasetRecords",
    "DatasetsBootstrapResult",
    "DatasetsBootstrapTrainResult",
    "DatasetsFreezeResult",
    "MetricsRecord",
    "LoopRunnerLiveness",
    "ExampleOutcome",
    "LoopProgress",
    "CompileStage",
    "CompileHistoryEntry",
    "CompileStatus",
    "CompileRunResult",
    "CompilePromoteResult",
    "ScoreboardEntryKind",
    "ScoreboardEntry",
    "BaselineMeta",
    "BranchScoreboard",
    "BranchDeleteResult",
    "ArenaVariant",
    "ArenaStatus",
    "ArenaHistory",
    "MetricsWindow",
    "GoldenCandidate",
    "GoldenPageInfo",
    "GoldenReview",
    "GoldenSaveResult",
    "LoopHoldPreview",
    "LoopOnceResult",
    "LoopPendingCandidate",
    "LoopSetBaselineResult",
    "LoopBaselinePolicyResult",
    "LoopRebaselineResult",
    "LoopCadenceConfig",
    "LoopRunEvalResult",
    "BaselineProbeResult",
    "PromptDiffLineType",
    "PromptDiffLine",
    "PromptDiffHunk",
    "PromptDiffResult",
    "PromptDiffMode",
  ],
  tend: [
    "DoctorCheck",
    "DoctorFixGuidance",
    "DoctorReport",
    "DirtyEntry",
    "DoctorRepairResult",
    "LintViolation",
    "VaultLintResult",
    "OkfCheckResult",
    "OkfRepairResult",
    "MetadataNodeKind",
    "MetadataTreeNode",
    "VaultMetadataTree",
    "NoteIntent",
    "NoteIntentFilter",
    "AnchorFidelity",
    "AnchorStatus",
    "AnchorStatusFilter",
    "AnchorProjectionStatus",
    "NoteAnchor",
    "NoteRecord",
    "NoteReadResult",
    "NotesListResult",
    "NotesStatusSummary",
    "NoteCaptureAlternative",
    "NoteCaptureResult",
    "NoteDriftAlternative",
    "NoteDrift",
    "NoteDriftItem",
    "NotesDriftResult",
    "NoteAction",
    "NoteDecisionEnvelope",
    "NoteAnchorActionResult",
    "NoteArchiveActionResult",
    "NotePromoteTrainsetResult",
    "NotePromoteGapResult",
    "PromoteTarget",
    "NotePromoteActionResult",
  ],
  learn: ["ActivityWorkflow", "IngestEvent", "IngestRun", "IngestActivity"],
  answer: ["QueryAnswer"],
  fill: [
    "SuggestionStatusSummary",
    "GapOrigin",
    "GapStatusSummary",
    "ReputabilityTier",
    "SuggestionReputability",
    "SuggestionCandidate",
    "SuggestionStatus",
    "GateOutcomeVerdict",
    "GateOutcomeRegressedQuestion",
    "GateOutcome",
    "SuggestionRecord",
    "SuggestionsStatusFilter",
    "SuggestionsReadResult",
    "GapStatus",
    "GapsStatusFilter",
    "GapFaultClass",
    "GapRecord",
    "GapfillDiscoverResult",
    "GapReportResult",
    "GapsReadResult",
    "SuggestionAction",
    "SuggestionReviewResult",
    "SessionState",
    "SessionNextActor",
    "SessionGateOutcome",
    "SessionStatus",
  ],
  home: [
    "StatusView",
    "AttentionSuggestions",
    "AttentionTopicRow",
    "AttentionStatus",
    "AttentionUrgency",
    "AttentionRow",
  ],
};

const TOTAL_MOVED_DECLARATIONS = Object.values(MOVED_TYPES).reduce(
  (sum, names) => sum + names.length,
  0,
);

describe("every lane types file exists", () => {
  it.each(Object.keys(MOVED_TYPES))(
    "dashboard/src/lanes/%s/types.ts exists on disk",
    (lane) => {
      expect(
        fsModule.existsSync(pathModule.join(srcDir, "lanes", lane, "types.ts")),
      ).toBe(true);
    },
  );
});

describe("every moved declaration is importable from its per-lane path (round-trip half 1)", () => {
  for (const [lane, names] of Object.entries(MOVED_TYPES)) {
    describe(`lanes/${lane}/types.ts`, () => {
      let laneSource: string;

      beforeAll(() => {
        laneSource = fsModule.readFileSync(
          pathModule.join(srcDir, "lanes", lane, "types.ts"),
          "utf-8",
        );
      });

      it.each(names)("declares %s", (name) => {
        const declared = new RegExp(
          `^export (?:interface|type) ${name}\\b`,
          "m",
        );
        expect(laneSource).toMatch(declared);
      });
    });
  }
});

describe("every moved declaration is still importable from the root barrel (round-trip half 2)", () => {
  for (const [lane, names] of Object.entries(MOVED_TYPES)) {
    describe(`the ${lane} re-export block`, () => {
      let block: string;

      beforeAll(() => {
        const pattern = new RegExp(
          `export type \\{([\\s\\S]*?)\\} from "\\./lanes/${lane}/types";`,
        );
        const match = typesSource.match(pattern);
        if (!match) {
          throw new Error(
            `No "export type { ... } from "./lanes/${lane}/types"" block found in types.ts`,
          );
        }
        block = match[1];
      });

      it.each(names)("re-exports %s", (name) => {
        expect(block).toMatch(new RegExp(`\\b${name}\\b`));
      });
    });
  }
});

describe("the census's own bookkeeping matches Step 117's recorded total", () => {
  it("120 moved declarations across the six lanes", () => {
    expect(TOTAL_MOVED_DECLARATIONS).toBe(120);
  });
});

// ---------------------------------------------------------------------------
// (4) Declarations are imported only via sanctioned paths.
// ---------------------------------------------------------------------------

describe("no survivor other than the root types.ts imports a lane types module directly", () => {
  it('finds zero direct `from "<relative>/lanes/<lane>/types"` imports outside dashboard/src/types.ts', () => {
    const rootTypesPath = pathModule.join(srcDir, "types.ts");
    const directImportPattern =
      /from\s+["'](?:\.\.?\/)+lanes\/[a-z]+\/types["']/;
    const offenders = collectSourceFiles(srcDir)
      .filter((file) => file !== rootTypesPath)
      .filter((file) =>
        directImportPattern.test(fsModule.readFileSync(file, "utf-8")),
      );
    expect(offenders).toEqual([]);
  });
});

describe("every consumer that references a moved type imports it through the root barrel", () => {
  it('no source file references a moved type without importing "./types" (at any relative depth)', () => {
    // Exclude the declaration sites themselves: the root barrel (which
    // legitimately imports a few lane types directly for its own
    // cross-cutting shapes) and each lane's own types.ts (which declares
    // these names rather than importing them -- a file's own declaration of
    // `GateState` is not a "reference" this check should flag).
    const declarationFiles = new Set([
      pathModule.join(srcDir, "types.ts"),
      ...Object.keys(MOVED_TYPES).map((lane) =>
        pathModule.join(srcDir, "lanes", lane, "types.ts"),
      ),
    ]);
    const barrelImportPattern = /from\s+["'](?:\.\.?\/)+types["']/;
    const allMovedNames = Object.values(MOVED_TYPES).flat();
    // Comments are stripped before matching, the same way `processMeta.test.ts`
    // strips them for its own source scan: a docblock that *names* a type while
    // explaining a field's vocabulary is prose, not a reference, and needs no
    // import. Without this the census fails on a sentence, which teaches people
    // to reword sentences instead of fixing imports.
    const offenders = collectSourceFiles(srcDir)
      .filter((file) => !declarationFiles.has(file))
      .filter((file) => {
        const raw = fsModule.readFileSync(file, "utf-8");
        const content = raw
          .replace(/\/\*[\s\S]*?\*\//g, " ")
          .replace(/(^|[^:])\/\/.*$/gm, "$1");
        const referencesMovedType = allMovedNames.some((name) =>
          new RegExp(`\\b${name}\\b`).test(content),
        );
        return referencesMovedType && !barrelImportPattern.test(content);
      });
    expect(offenders).toEqual([]);
  });
});
