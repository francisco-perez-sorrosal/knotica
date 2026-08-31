import type { App } from "@modelcontextprotocol/ext-apps";
import { beforeAll, describe, expect, it } from "vitest";

import { BridgeToolClient } from "../toolClient";
import { answerToolCalls } from "../lanes/answer/client";
import { fillToolCalls } from "../lanes/fill/client";
import { homeToolCalls } from "../lanes/home/client";
import { improveToolCalls } from "../lanes/improve/client";
import { learnToolCalls } from "../lanes/learn/client";
import { tendToolCalls } from "../lanes/tend/client";

/**
 * Pins Step 119's `toolClient.ts` split (`td-057`'s client half) so the
 * composed `ToolClient`'s method set cannot silently regrow, shrink, or
 * migrate to the wrong lane. This is a **regression census over
 * already-landed code**, not a paired BDD step -- Step 119 shipped 48 of
 * `ToolClient`'s 51 concrete tool-call methods out of
 * `dashboard/src/toolClient.ts` into six `lanes/<lane>/client.ts` groups
 * installed onto one prototype (see `LEARNINGS_implementer_step119.md`'s
 * split-map table); this suite is the backstop that keeps that map from
 * drifting. Green on first run is therefore expected, not a paired-step race.
 *
 * Four independently falsifiable groups:
 *
 *   (1) the composed client's tool-call method set -- walked off the live
 *       prototype chain via `Object.getOwnPropertyNames`, never a
 *       hand-inspected list of the current code -- is exactly the pre-split
 *       51-name fixture below, transcribed from `git show
 *       HEAD:dashboard/src/toolClient.ts` (the state before Step 119 ran). A
 *       method dropped, renamed, or silently reassigned fails this even
 *       though nothing else in the suite happens to call it.
 *   (2) every one of the six lane groups declares exactly its assigned
 *       method set, and every one of those methods is a real function on the
 *       composed prototype -- so a lane-to-lane swap (same aggregate count,
 *       wrong owner) fails here even where (1) alone would not notice.
 *   (3) the three methods with no lane home (`vaultUse`, `vaultCreate`,
 *       `createTopic`) stay off every lane group, and still dispatch through
 *       the shell with their original wire shape.
 *   (4) `dashboard/src/toolClient.ts` stays under the plan's 400-line
 *       ceiling, and no `lanes/<lane>/client.ts` imports another lane's
 *       `client.ts` -- the two structural guarantees the split rested on.
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` are loaded via
 * a dynamic `import()` with a variable specifier, the same technique
 * `typesBarrelCensus.test.ts` already uses.
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

let fsModule: FsModule;
let pathModule: PathModule;
let srcDir: string;
let toolClientSource: string;

beforeAll(async () => {
  fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  srcDir = pathModule.join(testDir, "..");
  toolClientSource = fsModule.readFileSync(
    pathModule.join(srcDir, "toolClient.ts"),
    "utf-8",
  );
});

// ---------------------------------------------------------------------------
// The pre-split fixture. Hardcoded, not derived from the tree this suite
// pins -- transcribed from `git show HEAD:dashboard/src/toolClient.ts` (the
// state before Step 119 ran) and cross-checked against
// `LEARNINGS_implementer_step119.md`'s own split-map table.
// ---------------------------------------------------------------------------

/** The 48 methods Step 119 moved out of the shell, by destination lane. */
const MOVED_METHODS: Record<string, readonly string[]> = {
  improve: [
    "metricsRead",
    "arenaStatus",
    "arenaHistory",
    "compileStatus",
    "compileRun",
    "compilePromote",
    "goldenReviewLoad",
    "goldenReviewSave",
    "datasetsInventory",
    "datasetsRecords",
    "datasetsBootstrap",
    "datasetsBootstrapTrain",
    "datasetsFreeze",
    "loopRunOnce",
    "loopSetBaseline",
    "loopBaselinePolicy",
    "loopRebaseline",
    "baselineProbe",
    "loopCadence",
    "loopRunEval",
    "branchScoreboard",
    "branchPromote",
    "branchDelete",
    "promptDiff",
  ],
  tend: [
    "doctorRun",
    "doctorRepair",
    "vaultLint",
    "vaultMetadataTree",
    "okfCheck",
    "okfRepair",
    "notesList",
    "notesRead",
    "notesDrift",
    "notesReanchor",
    "notesDetach",
    "notesPromote",
    "notesArchive",
  ],
  fill: [
    "suggestionsRead",
    "gapsRead",
    // Post-split addition (gap dismissal affordance): the census counts it as
    // a moved-set member so groups (1)/(2) keep gating the live surface; the
    // Step 119 transcription proper was the five methods around it.
    "reviewGap",
    "gapfillDiscover",
    "suggestionsReview",
    "sessionStatus",
  ],
  answer: ["query", "curateExample", "noteCapture", "gapReport"],
  learn: ["ingestActivityRead"],
  home: ["wikiStatus"],
};

/** The 3 methods that stayed in the shell -- there is no vault lane, and
 * inventing one to hold them would reopen the catch-all shape `td-057`
 * closes. */
const SHELL_METHODS = ["vaultUse", "vaultCreate", "createTopic"] as const;

/** Transport methods every mount (`HttpToolClient`/`BridgeToolClient`)
 * implements differently -- part of `ToolClient` but outside the
 * redistribution this split concerns, so excluded from the census below. */
const TRANSPORT_METHODS = [
  "call",
  "sendMessage",
  "updateModelContext",
  "close",
];

const PRE_SPLIT_TOOL_CALL_METHODS: readonly string[] = [
  ...SHELL_METHODS,
  ...Object.values(MOVED_METHODS).flat(),
];

const LANE_GROUPS: Record<string, Record<string, unknown>> = {
  improve: improveToolCalls as unknown as Record<string, unknown>,
  tend: tendToolCalls as unknown as Record<string, unknown>,
  fill: fillToolCalls as unknown as Record<string, unknown>,
  answer: answerToolCalls as unknown as Record<string, unknown>,
  learn: learnToolCalls as unknown as Record<string, unknown>,
  home: homeToolCalls as unknown as Record<string, unknown>,
};

/** A recording fake host: records every call, replies with a queued payload. */
function fakeApp(): {
  app: App;
  calls: { name: string; args: Record<string, unknown> }[];
} {
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const app = {
    async callServerTool(request: {
      name: string;
      arguments?: Record<string, unknown>;
    }) {
      calls.push({ name: request.name, args: request.arguments ?? {} });
      return { structuredContent: {} };
    },
  } as unknown as App;
  return { app, calls };
}

/** Every own, function-valued property along `instance`'s prototype chain,
 * stopping at `Object.prototype`. */
function collectPrototypeMethodNames(instance: object): string[] {
  const names = new Set<string>();
  let proto: object | null = Object.getPrototypeOf(instance);
  while (proto && proto !== Object.prototype) {
    for (const name of Object.getOwnPropertyNames(proto)) {
      if (name === "constructor") continue;
      const descriptor = Object.getOwnPropertyDescriptor(proto, name);
      if (typeof descriptor?.value === "function") names.add(name);
    }
    proto = Object.getPrototypeOf(proto);
  }
  return [...names];
}

/** Shared read-only reflection subject -- safe to reuse: no test below mutates it. */
let censusClient: BridgeToolClient;

beforeAll(() => {
  censusClient = new BridgeToolClient(fakeApp().app);
});

describe("the fixture's own bookkeeping matches the recorded split plus later additions", () => {
  it("49 lane methods (48 moved + reviewGap) plus 3 shell methods totals 52", () => {
    expect(PRE_SPLIT_TOOL_CALL_METHODS).toHaveLength(52);
  });
});

// ---------------------------------------------------------------------------
// (1) The composed client's tool-call method set is byte-identical to the
//     pre-split fixture.
// ---------------------------------------------------------------------------

describe("the composed ToolClient exposes exactly the declared method set", () => {
  it("carries every declared tool-call method and no others", () => {
    const toolCallMethods = collectPrototypeMethodNames(censusClient).filter(
      (name) => !TRANSPORT_METHODS.includes(name),
    );
    expect(toolCallMethods.sort()).toEqual(
      [...PRE_SPLIT_TOOL_CALL_METHODS].sort(),
    );
  });
});

// ---------------------------------------------------------------------------
// (2) Every lane group declares exactly its assigned methods, and every one
//     of those methods is a real function on the composed prototype.
// ---------------------------------------------------------------------------

describe("every lane group installs exactly its assigned methods", () => {
  for (const [lane, names] of Object.entries(MOVED_METHODS)) {
    describe(`lanes/${lane}/client.ts`, () => {
      it(`declares exactly its ${names.length} assigned method(s), no more and no fewer`, () => {
        expect(Object.keys(LANE_GROUPS[lane]).sort()).toEqual(
          [...names].sort(),
        );
      });

      it.each(names)(
        "installs %s as a function on the composed prototype",
        (name) => {
          expect(
            typeof (censusClient as unknown as Record<string, unknown>)[name],
          ).toBe("function");
        },
      );
    });
  }
});

// ---------------------------------------------------------------------------
// (3) The three methods with no lane home stay off every lane group, and
//     still dispatch through the shell with their original wire shape.
// ---------------------------------------------------------------------------

describe("the three shell methods stay off every lane's tool-call group", () => {
  it.each(SHELL_METHODS)(
    "%s is not declared by any of the six lane tool-call groups",
    (name) => {
      const owners = Object.entries(LANE_GROUPS)
        .filter(([, group]) =>
          Object.prototype.hasOwnProperty.call(group, name),
        )
        .map(([lane]) => lane);
      expect(owners).toEqual([]);
    },
  );
});

describe("the three shell methods still dispatch through the shell with their original wire shape", () => {
  it("vaultUse sends the vault dispatcher's use action", async () => {
    const { app, calls } = fakeApp();
    const client = new BridgeToolClient(app);

    await client.vaultUse("research");

    expect(calls).toEqual([
      { name: "vault", args: { action: "use", name: "research" } },
    ]);
  });

  it("vaultCreate sends the vault dispatcher's create action", async () => {
    const { app, calls } = fakeApp();
    const client = new BridgeToolClient(app);

    await client.vaultCreate("research", "/vaults/research");

    expect(calls).toEqual([
      {
        name: "vault",
        args: {
          action: "create",
          name: "research",
          path: "/vaults/research",
          topic: "",
          make_default: true,
        },
      },
    ]);
  });

  it("createTopic sends the learn lane's create_topic action", async () => {
    const { app, calls } = fakeApp();
    const client = new BridgeToolClient(app);

    await client.createTopic("physics");

    expect(calls).toEqual([
      {
        name: "learn",
        args: {
          action: "create_topic",
          topic: "physics",
          description: "",
          vault: "",
        },
      },
    ]);
  });
});

// ---------------------------------------------------------------------------
// (4) The two structural guarantees the split's design rested on.
// ---------------------------------------------------------------------------

describe("dashboard/src/toolClient.ts stays under the plan's 400-line ceiling", () => {
  it("is under 400 lines", () => {
    const lineCount = toolClientSource.trimEnd().split("\n").length;
    expect(lineCount).toBeLessThan(400);
  });
});

describe("no lane's client.ts imports another lane's client.ts", () => {
  const LANES = Object.keys(MOVED_METHODS);

  it.each(LANES)(
    "lanes/%s/client.ts does not import from any other lane's client module",
    (lane) => {
      const source = fsModule.readFileSync(
        pathModule.join(srcDir, "lanes", lane, "client.ts"),
        "utf-8",
      );
      const crossLaneImports = [
        ...source.matchAll(
          /from\s+["'](?:\.\.\/)+lanes\/([a-z]+)\/client["']/g,
        ),
      ]
        .map((match) => match[1])
        .filter((importedLane) => importedLane !== lane);

      expect(crossLaneImports).toEqual([]);
    },
  );
});
