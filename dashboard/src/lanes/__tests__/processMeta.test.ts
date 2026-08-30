import { beforeAll, describe, expect, it } from "vitest";

import { toolCallMethodNames } from "../../__tests__/helpers/clientSurface";
import { LANES, LANE_STAGES } from "../../processModel";
import { PROCESS_META, resolveNextAnchor } from "../processMeta";
import type { ProcessAnchor, ProcessId, ProcessMeta } from "../processMeta";

/**
 * The census that keeps the six-phase lifecycle contract from decaying into a
 * habit (`processMeta.ts`'s docblock states the contract itself).
 *
 * **Choice of mechanism.** Two alternatives were rejected. A `data-testid`
 * convention on every trigger is opt-out by omission — `ArmedButton`'s
 * `testId` prop is optional, so a control disables the check by not typing
 * something, and an enforcement mechanism a developer switches off by
 * omission is not enforcement. An AST walk over JSX needs a parser dependency
 * the dashboard does not carry, and `<button>` appears ~93 times of which
 * most are cancels, filters and tabs — a false-positive rate that buys
 * suppressions, and suppressions are how a census rots.
 *
 * What is used instead: the dashboard has exactly one route to the server (a
 * `ToolClient` method — `dashboard/CLAUDE.md`, *Talking to the server*). A
 * process cannot exist without one, so a census closed over that surface is
 * complete **by construction** rather than by diligence.
 *
 * Groups landing here, each naming a distinct way the engraving can rot:
 *
 *   G1  registry completeness against the client surface
 *   G4  phase completeness and the cross-field invariants
 *   G5  anchor validity against the generated process model
 *   G6  Home acts on nothing
 *   G7  copy uniqueness
 *
 *   G2  trigger routing — every row is wired, every marker is a real id
 *   G3  raw-trigger interdiction — the teeth
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` are loaded via
 * a dynamic `import()` with a variable specifier, the same technique
 * `crossLaneLinkCensus.test.ts` and `toolNameRegistryCensus.test.ts` use.
 */

interface FsModule {
  readdirSync(path: string): string[];
  statSync(path: string): { isDirectory(): boolean };
  readFileSync(path: string, encoding: string): string;
}
interface PathModule {
  dirname(path: string): string;
  join(...parts: string[]): string;
  relative(from: string, to: string): string;
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

beforeAll(async () => {
  fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  srcDir = pathModule.join(testDir, "..", "..");
});

/** Walks `dashboard/src`, skipping `__tests__`, returning every `.ts`/`.tsx` file. */
function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of fsModule.readdirSync(dir)) {
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

/**
 * Source text with comments removed, because the source scans below are
 * looking for *code* and a comment is prose.
 *
 * This is not tidiness. `query` is a registered client method and also an
 * ordinary English word: `LaneCardGrid.tsx` says "accessible-name query
 * (`HomeLane.test.tsx`...)" in a docblock, which `\bquery\s*\(` matches
 * exactly as if it were a call. A census that fails on a sentence teaches
 * people to reword sentences, and a census people work around has stopped
 * being one.
 *
 * The line-comment rule skips a `//` preceded by `:` so a URL inside a string
 * survives; the block-comment rule is non-greedy so adjacent docblocks are not
 * swallowed whole.
 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function sources(): { path: string; text: string }[] {
  return collectSourceFiles(srcDir).map((path) => ({
    path: pathModule.relative(srcDir, path),
    text: stripComments(fsModule.readFileSync(path, "utf-8")),
  }));
}

// ---------------------------------------------------------------------------
// G1 — registry completeness against the client surface.
// ---------------------------------------------------------------------------

/**
 * Client methods that only read. A read is not a process: there is nothing to
 * approve, nothing to preview, and no outcome beyond the data itself.
 *
 * `doctorRun` is the one entry classified **as wired** rather than by
 * signature — it carries a `fix` flag the server would act on, and the
 * dashboard's single call site (`TendLane.tsx`) passes only the vault, so the
 * flag is always false. If a caller ever passes `fix: true`, this row is a
 * lie and the method belongs in the registry.
 */
const READ_ONLY_CLIENT_METHODS: readonly string[] = [
  "wikiStatus",
  "ingestActivityRead",
  "metricsRead",
  "arenaStatus",
  "arenaHistory",
  "compileStatus",
  "goldenReviewLoad",
  "datasetsInventory",
  "datasetsRecords",
  "branchScoreboard",
  "promptDiff",
  "suggestionsRead",
  "gapsRead",
  "sessionStatus",
  "doctorRun",
  "vaultLint",
  "vaultMetadataTree",
  "okfCheck",
  "notesList",
  "notesRead",
  "notesDrift",
];

/**
 * Live client surface reaching a live server action with **no way for a user
 * to trigger it** — zero non-test call sites anywhere in `dashboard/src`.
 *
 * This fixture is the finding, not a suppression: five invisible processes
 * made visible and reviewable, which is the contract's own Surface phase
 * applied to the codebase. They deliberately get no registry row — a `why`
 * for a control nobody can click is fiction — and they are deliberately not
 * deleted here either; removing client surface is a separate decision from
 * engraving the lifecycle onto the surface that stays.
 *
 * `doctorRepair` was found by this census, not by the audit that preceded it:
 * Tend's doctor card offers the *CLI* repair command as copyable text and
 * never calls the client method, so a `tend.doctor_repair` row would have
 * described a control the dashboard does not have. The four `loop`/`baseline`
 * methods were already known.
 */
const UNWIRED_CLIENT_METHODS: readonly string[] = [
  "loopSetBaseline",
  "loopRebaseline",
  "loopBaselinePolicy",
  "baselineProbe",
  "doctorRepair",
];

/**
 * Mutating or billing methods that have a wired trigger but no lifecycle copy
 * yet — the migration's remaining voids, named one by one.
 *
 * **This fixture must reach empty.** It exists because two census groups have
 * to hold simultaneously at every commit during the migration: G1 (every
 * client method is accounted for) and G2 (every registered process is
 * actually wired). A registry that declared all its rows up front would
 * satisfy the first and fail the second for the whole migration; a registry
 * that grew silently would satisfy the second and lose the first. Naming the
 * remainder keeps both true and keeps the size of the void in one countable
 * place instead of in a plan document.
 */
const AWAITING_LIFECYCLE_CLIENT_METHODS: readonly string[] = [
  "compileRun",
  "compilePromote",
  "goldenReviewSave",
  "datasetsBootstrap",
  "datasetsBootstrapTrain",
  "datasetsFreeze",
  "loopCadence",
  "branchPromote",
  "branchDelete",
  "createTopic",
  "vaultCreate",
  "vaultUse",
];

const PROCESS_IDS = Object.keys(PROCESS_META) as ProcessId[];
const ROWS: ReadonlyArray<readonly [ProcessId, ProcessMeta]> = PROCESS_IDS.map(
  (id) => [id, PROCESS_META[id]] as const,
);

function registeredClientMethods(): string[] {
  const named = ROWS.map(([, meta]) => meta.clientMethod).filter(
    (name) => name !== null,
  );
  return [...new Set<string>(named)];
}

/** Every anchor a row can send the user to, including branch and fallback. */
function anchorsOf(meta: ProcessMeta): ProcessAnchor[] {
  if (meta.next.kind === "terminal") return [];
  if (meta.next.kind === "always") return [meta.next.go];
  return [...meta.next.branches.map((branch) => branch.go), meta.next.fallback];
}

describe("G1 — every client method is accounted for exactly once", () => {
  const partitions: ReadonlyArray<readonly [string, readonly string[]]> = [
    ["registry", registeredClientMethods()],
    ["read-only", READ_ONLY_CLIENT_METHODS],
    ["unwired", UNWIRED_CLIENT_METHODS],
    ["awaiting lifecycle", AWAITING_LIFECYCLE_CLIENT_METHODS],
  ];

  it("partitions the whole client surface — nothing unclassified", () => {
    const classified = partitions.flatMap(([, names]) => names).sort();
    expect(classified).toEqual([...toolCallMethodNames()].sort());
  });

  it("classifies each method once — the partitions are pairwise disjoint", () => {
    const seen = new Map<string, string>();
    const collisions: string[] = [];
    for (const [label, names] of partitions) {
      for (const name of names) {
        const previous = seen.get(name);
        if (previous) collisions.push(`${name}: ${previous} + ${label}`);
        else seen.set(name, label);
      }
    }
    expect(collisions).toEqual([]);
  });

  it("names no method the client does not have", () => {
    const surface = new Set(toolCallMethodNames());
    const phantom = partitions
      .flatMap(([label, names]) =>
        names.filter((name) => !surface.has(name)).map((n) => `${label}: ${n}`),
      )
      .sort();
    expect(phantom).toEqual([]);
  });

  it("gives every non-handoff process a client method", () => {
    const missing = ROWS.filter(
      ([, meta]) => meta.dispatch === "client" && meta.clientMethod === null,
    ).map(([id]) => id);
    expect(missing).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// G4 — phase completeness and the cross-field invariants.
// ---------------------------------------------------------------------------

describe("G4 — every row answers all six questions", () => {
  it.each(ROWS)("%s states a title, a cause and an effect", (_id, meta) => {
    expect(meta.title.trim()).not.toBe("");
    expect(meta.why.trim()).not.toBe("");
    expect(meta.willDo.trim()).not.toBe("");
  });

  it.each(ROWS)("%s writes why/willDo as sentences", (_id, meta) => {
    expect(meta.why.trim().endsWith(".")).toBe(true);
    expect(meta.willDo.trim().endsWith(".")).toBe(true);
  });

  /**
   * The house rule from `dashboard/CLAUDE.md` — "billed actions are two-phase,
   * a single click must never bill" — machine-checked for the first time.
   * `acknowledged` is the one legal single-click billed mode and it is a named
   * exception, not a default: the next assertion makes it pay for itself.
   */
  it("never bills without a preview", () => {
    const offenders = ROWS.filter(
      ([, meta]) =>
        meta.spend === "billed" &&
        !["nonce", "armed", "acknowledged"].includes(meta.previewMode),
    ).map(([id]) => id);
    expect(offenders).toEqual([]);
  });

  it("makes an acknowledged single-click spend state its cost in willDo", () => {
    const offenders = ROWS.filter(
      ([, meta]) =>
        meta.previewMode === "acknowledged" &&
        !/costs? tokens|bills\b/i.test(meta.willDo),
    ).map(([id]) => id);
    expect(offenders).toEqual([]);
  });

  /**
   * Once a payload leaves for another agent's turn the dashboard has no
   * channel into the work, so it may not claim in-process progress and may not
   * claim completion.
   */
  it("never lets a handoff claim progress it cannot see", () => {
    const offenders = ROWS.filter(
      ([, meta]) =>
        meta.dispatch === "handoff" &&
        (meta.progressMode !== "external" || meta.outcomeMode !== "external"),
    ).map(([id]) => id);
    expect(offenders).toEqual([]);
  });

  it("never lets a silent re-render pass as an outcome", () => {
    const offenders = ROWS.filter(
      ([, meta]) =>
        meta.outcomeMode === "refresh" && !meta.outcomeFallback?.trim(),
    ).map(([id]) => id);
    expect(offenders).toEqual([]);
  });

  it("gives every conditional next at least two distinct branches", () => {
    const offenders = ROWS.filter(([, meta]) => {
      if (meta.next.kind !== "conditional") return false;
      const whens = meta.next.branches.map((branch) => branch.when);
      return whens.length < 2 || new Set(whens).size !== whens.length;
    }).map(([id]) => id);
    expect(offenders).toEqual([]);
  });

  it("lands an unrecognised discriminant on the fallback, never on nothing", () => {
    const conditional = ROWS.filter(
      ([, meta]) => meta.next.kind === "conditional",
    );
    expect(conditional.length).toBeGreaterThan(0);
    for (const [, meta] of conditional) {
      expect(resolveNextAnchor(meta.next, "a-verdict-this-build-cannot-know"))
        .not.toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// G5 — anchor validity against the generated process model.
// ---------------------------------------------------------------------------

describe("G5 — no process points at a destination the model does not declare", () => {
  function stageIds(lane: string): string[] {
    return (LANE_STAGES[lane] ?? []).map((stage) => stage.id);
  }

  it.each(ROWS)("%s lives in a lane the model declares", (_id, meta) => {
    expect(LANES).toContain(meta.lane);
    if (meta.stage !== null) expect(stageIds(meta.lane)).toContain(meta.stage);
  });

  it("sends every follow-up to a lane and stage the model declares", () => {
    const offenders: string[] = [];
    for (const [id, meta] of ROWS) {
      for (const anchor of anchorsOf(meta)) {
        if (!LANES.includes(anchor.lane)) {
          offenders.push(`${id} -> lane ${anchor.lane}`);
          continue;
        }
        if (anchor.stage !== null && !stageIds(anchor.lane).includes(anchor.stage)) {
          offenders.push(`${id} -> ${anchor.lane}:${anchor.stage}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("gives every anchor a reason to go there", () => {
    const offenders: string[] = [];
    for (const [id, meta] of ROWS) {
      for (const anchor of anchorsOf(meta)) {
        if (!anchor.why.trim().endsWith(".")) {
          offenders.push(`${id} -> ${anchor.lane}:${anchor.stage ?? "(lane)"}`);
        }
      }
      if (meta.next.kind === "terminal" && !meta.next.why.trim()) {
        offenders.push(`${id} (terminal)`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// G6 — Home acts on nothing.
// ---------------------------------------------------------------------------

describe("G6 — Home routes, every other lane acts", () => {
  it("registers no process whose trigger lives on Home", () => {
    expect(ROWS.filter(([, meta]) => meta.lane === "home").map(([id]) => id)).toEqual(
      [],
    );
  });

  it("never sends a follow-up to a stage Home does not have", () => {
    const offenders: string[] = [];
    for (const [id, meta] of ROWS) {
      for (const anchor of anchorsOf(meta)) {
        if (anchor.lane === "home" && anchor.stage !== null) {
          offenders.push(`${id} -> home:${anchor.stage}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// G7 — copy uniqueness. Copy-paste is how a registry becomes decorative.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// G2 — trigger routing. Orphan copy is as bad as no copy.
// ---------------------------------------------------------------------------

/** `process="improve.gate_candidate"` and its braced form. */
const PROCESS_MARKER = /process=\{?"([^"]+)"\}?/g;

function markersInSource(): { path: string; id: string }[] {
  return sources().flatMap(({ path, text }) =>
    [...text.matchAll(PROCESS_MARKER)].map((match) => ({ path, id: match[1] })),
  );
}

describe("G2 — every registered process is wired, every marker is a real id", () => {
  it("names no process the registry does not declare", () => {
    const known = new Set<string>(PROCESS_IDS);
    const unknown = [
      ...new Set(
        markersInSource()
          .filter((marker) => !known.has(marker.id))
          .map((marker) => `${marker.path}: ${marker.id}`),
      ),
    ].sort();
    expect(unknown).toEqual([]);
  });

  it("leaves no registry row unwired — copy with no trigger is a lie by omission", () => {
    const wired = new Set(markersInSource().map((marker) => marker.id));
    const orphans = ROWS.filter(
      ([id, meta]) => meta.dispatch !== "cli" && !wired.has(id),
    ).map(([id]) => id);
    expect(orphans).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// G3 — raw-trigger interdiction. The teeth.
// ---------------------------------------------------------------------------

/**
 * The seven modules that *define* the client surface. A definition is not a
 * trigger; interdicting them would forbid the client from existing.
 */
const CLIENT_DEFINITION_SOURCES: readonly string[] = [
  "toolClient.ts",
  "lanes/home/client.ts",
  "lanes/learn/client.ts",
  "lanes/answer/client.ts",
  "lanes/improve/client.ts",
  "lanes/fill/client.ts",
  "lanes/tend/client.ts",
];

describe("G3 — a registered process is never triggered without its lifecycle copy", () => {
  /**
   * The check is *file*-level, not *control*-level: a file that calls a
   * registered client method must declare which process it is running. The
   * design proposed proxying that through an import of `processMeta`; the
   * `process="<id>"` marker is used instead because it is strictly tighter
   * (the file names the process, not merely the module) and because it does
   * not need widening each time a new lifecycle composition lands.
   *
   * Stated rather than oversold: a file legitimately rendering one process
   * could add a raw trigger for a second and still pass. G2 catches the case
   * where that second process is never wired anywhere; the residual is
   * "wired twice in one file, once correctly" — a two-step mistake instead of
   * a one-step one, and a visible one in review. Closing it fully needs a JSX
   * AST walk and a parser dependency the dashboard does not carry.
   */
  it("finds no call site outside a file that declares its process", () => {
    const methods = registeredClientMethods();
    const exempt = new Set(CLIENT_DEFINITION_SOURCES);
    const offenders: string[] = [];

    for (const { path, text } of sources()) {
      if (exempt.has(path)) continue;
      const declares = PROCESS_MARKER.test(text);
      PROCESS_MARKER.lastIndex = 0;
      if (declares) continue;
      for (const method of methods) {
        if (new RegExp(`\\b${method}\\s*\\(`).test(text)) {
          offenders.push(`${path}: ${method}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("G7 — no two processes share their copy", () => {
  it("gives each process its own why", () => {
    const whys = ROWS.map(([, meta]) => meta.why);
    expect(new Set(whys).size).toBe(whys.length);
  });

  it("gives each process its own willDo", () => {
    const willDos = ROWS.map(([, meta]) => meta.willDo);
    expect(new Set(willDos).size).toBe(willDos.length);
  });
});
