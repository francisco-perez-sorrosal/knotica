import type { App } from "@modelcontextprotocol/ext-apps";
import { beforeAll, describe, expect, it } from "vitest";

import { LANES } from "../processModel";
import { BridgeToolClient } from "../toolClient";
import {
  TRANSPORT_METHODS,
  collectPrototypeMethodNames,
} from "./helpers/clientSurface";

/**
 * The net that was missing when the M1 lane rename shipped.
 *
 * Every dashboard suite mocks at the client boundary -- a fake `App` records
 * `{name, args}` and replies with a canned payload -- so each one proves the
 * *shape* of a call and none of them proves the *name*. When the rename
 * removed ~20 operator-tier flat tools (they became lane-dispatcher actions),
 * the whole suite stayed green while Home's first real poll returned
 * `Unknown tool: metrics_read` in a browser. A shape assertion cannot fail on
 * a name the server does not have; only a census against the registry can.
 *
 * Authority for `REGISTERED_TOOLS` is `docs/reference.md` -- its Tier-1 tool
 * table, its `vault`/`open_dashboard` entries, and its lane-dispatcher table.
 * Its "Operator verbs (lane actions only)" table is the *complement*: those
 * names are reachable as `<lane> action=<verb>` and are **not** registrations,
 * which is exactly the distinction the defect erased.
 *
 * The census discipline: a rename lands here or fails here. Adding a tool
 * means adding its name to `REGISTERED_TOOLS`; removing one means deleting it
 * and watching this suite name every client call still pointing at it.
 *
 * Three independently falsifiable groups:
 *
 *   (1) every tool name the client family reaches the transport with at
 *       runtime -- collected by driving all 51 tool-call methods through a
 *       recording fake host -- is in `REGISTERED_TOOLS`. This is the group
 *       that would have caught the defect, and it resolves the `LANE`
 *       indirection for real rather than trusting the source text.
 *   (2) every tool-name string literal passed as `this.call`'s first argument
 *       in the seven client sources is in `REGISTERED_TOOLS` -- a source scan,
 *       so a name only reachable down a branch (1) does not exercise is still
 *       covered.
 *   (3) the six lane names inside `REGISTERED_TOOLS` are exactly `LANES` from
 *       the generated `processModel.ts` mirror, so a lane renamed in
 *       `core/process_model.py` cannot leave this fixture quietly stale.
 *
 * The prototype walk itself lives in `helpers/clientSurface.ts`, shared with
 * `lanes/__tests__/processMeta.test.ts` — both censuses are closed over the
 * same client surface, and two copies of the walk is exactly how the second
 * one goes stale.
 *
 * `@types/node` is not a project dependency; `fs`/`path`/`url` are loaded via
 * a dynamic `import()` with a variable specifier, the same technique
 * `toolClientMethodCensus.test.ts` and `crossLaneLinkCensus.test.ts` use.
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

// ---------------------------------------------------------------------------
// The registry. Transcribed from `docs/reference.md`; cross-checked against
// `asyncio.run(build_server().list_tools())`, which returns exactly these 21.
// ---------------------------------------------------------------------------

/** The 13 Tier-1 conversational tools the client-as-brain calls mid-turn. */
const FLAT_TOOLS = [
  "search",
  "read_page",
  "list_topics",
  "list_links",
  "read_protocol",
  "write_page",
  "store_source",
  "query",
  "wiki_status",
  "curate_example",
  "gap_report",
  "note_capture",
  "ingest_progress",
] as const;

/** The two unlaned Tier-2 tools -- neither touches wiki content. */
const INFRASTRUCTURE_TOOLS = ["vault", "open_dashboard"] as const;

/** The six generated lane dispatchers. Pinned against `LANES` by group (3). */
const LANE_TOOLS = [
  "home",
  "learn",
  "answer",
  "improve",
  "fill",
  "tend",
] as const;

const REGISTERED_TOOLS: readonly string[] = [
  ...FLAT_TOOLS,
  ...INFRASTRUCTURE_TOOLS,
  ...LANE_TOOLS,
];

/** The seven modules that hold every `this.call` site in the dashboard. */
const CLIENT_SOURCES = [
  "toolClient.ts",
  "lanes/home/client.ts",
  "lanes/learn/client.ts",
  "lanes/answer/client.ts",
  "lanes/improve/client.ts",
  "lanes/fill/client.ts",
  "lanes/tend/client.ts",
];

let fsModule: FsModule;
let pathModule: PathModule;
let srcDir: string;

beforeAll(async () => {
  fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  const testDir = pathModule.dirname(urlModule.fileURLToPath(import.meta.url));
  srcDir = pathModule.join(testDir, "..");
});

/** A recording fake host: keeps every call, replies with an empty payload. */
function recordingApp(): {
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

// ---------------------------------------------------------------------------
// (1) Runtime census: what actually reaches the transport.
// ---------------------------------------------------------------------------

describe("every tool name the client reaches the transport with is registered", () => {
  it("drives all 51 tool-call methods and sends only registered tool names", async () => {
    const { app, calls } = recordingApp();
    const client = new BridgeToolClient(app);
    const methods = collectPrototypeMethodNames(client).filter(
      (name) => !TRANSPORT_METHODS.includes(name),
    );
    const invoke = client as unknown as Record<string, () => Promise<unknown>>;

    await Promise.all(methods.map((name) => invoke[name]()));

    const unregistered = [
      ...new Set(
        calls
          .map((call) => call.name)
          .filter((name) => !REGISTERED_TOOLS.includes(name)),
      ),
    ].sort();
    expect(unregistered).toEqual([]);
  });

  it("sends one call per tool-call method -- none silently skipped", async () => {
    const { app, calls } = recordingApp();
    const client = new BridgeToolClient(app);
    const methods = collectPrototypeMethodNames(client).filter(
      (name) => !TRANSPORT_METHODS.includes(name),
    );
    const invoke = client as unknown as Record<string, () => Promise<unknown>>;

    await Promise.all(methods.map((name) => invoke[name]()));

    expect(calls).toHaveLength(methods.length);
  });

  it("routes every lane-action call through its lane dispatcher with an action selector", async () => {
    const { app, calls } = recordingApp();
    const client = new BridgeToolClient(app);
    const invoke = client as unknown as Record<string, () => Promise<unknown>>;
    const methods = collectPrototypeMethodNames(client).filter(
      (name) => !TRANSPORT_METHODS.includes(name),
    );

    await Promise.all(methods.map((name) => invoke[name]()));

    const laneCallsMissingAction = calls
      .filter(
        (call) =>
          (LANE_TOOLS as readonly string[]).includes(call.name) &&
          typeof call.args.action !== "string",
      )
      .map((call) => call.name);
    expect(laneCallsMissingAction).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// (2) Source census: every tool-name literal in a `this.call` position.
// ---------------------------------------------------------------------------

describe("every tool-name literal in the client sources is registered", () => {
  it.each(CLIENT_SOURCES)(
    "%s passes only registered tool names",
    (relative) => {
      const source = fsModule.readFileSync(
        pathModule.join(srcDir, ...relative.split("/")),
        "utf-8",
      );
      const literals = [
        ...source.matchAll(/this\.call(?:<[^>]*>)?\(\s*"([^"]+)"/g),
      ].map((match) => match[1]);
      const unregistered = [
        ...new Set(literals.filter((name) => !REGISTERED_TOOLS.includes(name))),
      ].sort();

      expect(unregistered).toEqual([]);
    },
  );
});

// ---------------------------------------------------------------------------
// (3) The fixture's six lane names are the generated mirror's, not a copy.
// ---------------------------------------------------------------------------

describe("the registry's lane half tracks the generated process model", () => {
  it("names exactly the lanes processModel.ts declares", () => {
    expect([...LANE_TOOLS].sort()).toEqual([...LANES].sort());
  });

  it("registers 21 tools in total", () => {
    expect(REGISTERED_TOOLS).toHaveLength(21);
  });

  it("lists each registered tool once", () => {
    expect(new Set(REGISTERED_TOOLS).size).toBe(REGISTERED_TOOLS.length);
  });
});
