import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ObsidianContext } from "../../../obsidianLinks";
import type { ToolClient } from "../../../toolClient";
import type { QueryAnswer, WikiStatus } from "../../../types";

/**
 * `dashboard/src/lanes/improve/ProveStage.tsx` does not exist yet -- this is
 * the RED half of a paired step for Improve's `prove` row --
 * "Improve's
 * `prove` stage embeds a probe instead of linking to Answer". Loaded through
 * a non-literal dynamic `import()` specifier, the same device
 * `lanes/__tests__/LaneRail.test.tsx` and `lanes/improve/__tests__/GateStage.test.tsx`
 * used for their own not-yet-existing modules: a literal
 * `import { ProveStage } from "../ProveStage"` would fail `tsc --noEmit` for
 * the whole project the moment this file lands, and a dynamic import whose
 * argument is not a string literal is left unresolved by TypeScript, so the
 * rest of the tree keeps type-checking while this file fails at *runtime*
 * with the missing-module error the paired implementation step is gated on.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (the paired implementation wins on conflict):
 *
 *   1. `<ProveStage client={...} topic={...} vault={...} status={...}
 *      obsidianCtx={...} />` -- the probe needs the same inputs `AskPane`
 *      needs to call `client.query` and render citation-linked answer cards.
 *   2. The probe's own question input carries
 *      `data-testid="prove-probe-question"`, its submit control carries
 *      `data-testid="prove-probe-ask"`, and the "pin this answer as Before"
 *      control carries `data-testid="prove-probe-pin"` -- this suite's own
 *      click targets, deliberately independent of whichever copy the
 *      implementer chooses (unlike `AskPane`'s established "Ask"/"Pin as
 *      Before" copy, `prove`'s probe is new UI with no prior text to anchor
 *      to).
 *   3. `client.query` is called positionally as `(topic, question, vault)`,
 *      exactly as `AskPane.tsx`'s own `ask()` already does today -- "the
 *      same `query` tool `AskPane` calls" names the tool, not a new
 *      wrapper.
 *   4. `prove` renders the compiled artifact's `prompt_diff mode="compiled"`
 *      through the real `PromptDiff` component (boundary mock, mirroring
 *      `GateStage.test.tsx`'s treatment of the same component).
 */

interface ProveStageProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  obsidianCtx: ObsidianContext;
}

type ProveStageComponent = (props: ProveStageProps) => JSX.Element;

interface ProveStageModule {
  ProveStage: ProveStageComponent;
}

const PROVE_STAGE_MODULE_PATH = "../ProveStage";

let ProveStage: ProveStageComponent;

beforeAll(async () => {
  ({ ProveStage } = (await import(
    PROVE_STAGE_MODULE_PATH
  )) as ProveStageModule);
});

/**
 * `PromptDiff` is a real, already-tested component -- stubbing it here is a
 * boundary mock (it reaches `client.promptDiff`, out of scope for this
 * suite), not a mock of the unit under test. If `ProveStage` reimplements its
 * own diff rendering instead of importing and invoking the real component,
 * this stub never mounts and the assertion that looks for it fails.
 */
vi.mock("../../../PromptDiff", () => ({
  PromptDiff: (props: { mode?: string; branch?: string | null }) => (
    <div
      data-testid="prompt-diff-mock"
      data-mode={props.mode ?? ""}
      data-branch={props.branch ?? ""}
    />
  ),
}));

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

/**
 * Takes **both** clicks of the probe's spend gate. `query` bills, so the
 * first click only arms the control -- the same grammar every other billed
 * control on this surface uses.
 */
function clickProbe(): void {
  fireEvent.click(screen.getByTestId("prove-probe-ask"));
  fireEvent.click(screen.getByTestId("prove-probe-ask"));
}

function answer(text: string, question = "q"): QueryAnswer {
  return {
    topic: TOPIC,
    question,
    answer: text,
    citations: [],
    pages_used: [],
  };
}

function baseStatus(overrides: { compiledPresent?: boolean } = {}): WikiStatus {
  return {
    schema_version: 1,
    vault: VAULT,
    vault_name: VAULT,
    vault_path: "/tmp/vault",
    default_vault: VAULT,
    available_vaults: [],
    compile_ready_threshold: 20,
    topics: [
      {
        topic: TOPIC,
        pages: 10,
        curated: 5,
        to_compile_ready: 0,
        compile_ready: true,
        compiled: overrides.compiledPresent
          ? {
              present: true,
              version: "v3",
              scalar: 0.7,
              compiled_at: "2026-08-01T00:00:00Z",
            }
          : null,
        lint_violations: 0,
        last_eval: null,
      },
    ],
    totals: { topics: 1, pages: 10, curated: 5, lint_violations: 0 },
    last_lint: null,
    unpushed: null,
    gate: { state: "pass", baseline: 0.62, last_scalar: 0.66 },
    llm: { available: true, mode: "api_key" },
    loop: {
      runner: {
        alive: false,
        pid: null,
        beat_at: null,
        interval_seconds: null,
      },
      stage: "idle",
    },
  };
}

function fakeClient(overrides: Partial<ToolClient> = {}): ToolClient {
  return {
    query: vi.fn(),
    promptDiff: vi.fn(),
    ...overrides,
  } as unknown as ToolClient;
}

describe("the probe calls query directly -- the same tool AskPane calls", () => {
  it("submits the probe question through client.query, not a new tool", async () => {
    const query = vi
      .fn()
      .mockResolvedValue(answer("a1", "Does compile improve grounding?"));

    render(
      <ProveStage
        client={fakeClient({ query })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
        obsidianCtx={{}}
      />,
    );

    fireEvent.input(screen.getByTestId("prove-probe-question"), {
      target: { value: "Does compile improve grounding?" },
    });
    clickProbe();

    await vi.waitFor(() => expect(query).toHaveBeenCalledTimes(1));
    expect(query).toHaveBeenCalledWith(
      TOPIC,
      "Does compile improve grounding?",
      VAULT,
    );
  });
});

describe("the probe bills, so one click never sends it", () => {
  it("arms on the first click and only calls query on the confirm", () => {
    const query = vi.fn().mockResolvedValue(answer("a1"));

    render(
      <ProveStage
        client={fakeClient({ query })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
        obsidianCtx={{}}
      />,
    );

    fireEvent.input(screen.getByTestId("prove-probe-question"), {
      target: { value: "q" },
    });
    fireEvent.click(screen.getByTestId("prove-probe-ask"));
    expect(query).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("prove-probe-ask"));
    expect(query).toHaveBeenCalledTimes(1);
  });

  it("un-arms on Cancel without spending anything", () => {
    const query = vi.fn().mockResolvedValue(answer("a1"));

    render(
      <ProveStage
        client={fakeClient({ query })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
        obsidianCtx={{}}
      />,
    );

    fireEvent.input(screen.getByTestId("prove-probe-question"), {
      target: { value: "q" },
    });
    fireEvent.click(screen.getByTestId("prove-probe-ask"));
    fireEvent.click(screen.getByTestId("prove-probe-cancel"));

    expect(query).not.toHaveBeenCalled();
  });
});

describe("the probe renders its own before/after answer cards in-lane", () => {
  it("shows both the pinned Before answer and the re-probed After answer", async () => {
    const query = vi
      .fn()
      .mockResolvedValueOnce(
        answer("Before this improvement, retrieval misses the demo."),
      )
      .mockResolvedValueOnce(
        answer("After this improvement, retrieval cites the demo."),
      );

    render(
      <ProveStage
        client={fakeClient({ query })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
        obsidianCtx={{}}
      />,
    );

    fireEvent.input(screen.getByTestId("prove-probe-question"), {
      target: { value: "q" },
    });
    clickProbe();
    await screen.findByText(
      "Before this improvement, retrieval misses the demo.",
    );

    fireEvent.click(screen.getByTestId("prove-probe-pin"));
    clickProbe();
    await screen.findByText(
      "After this improvement, retrieval cites the demo.",
    );

    expect(
      screen.getByText("Before this improvement, retrieval misses the demo."),
    ).toBeTruthy();
    expect(
      screen.getByText("After this improvement, retrieval cites the demo."),
    ).toBeTruthy();
  });
});

describe("the probe holds no cross-lane navigation", () => {
  it("renders no control whose name opens another pane", async () => {
    render(
      <ProveStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
        obsidianCtx={{}}
      />,
    );

    expect(
      screen.queryAllByRole("button", { name: /^(open|watch)\b/i }),
    ).toHaveLength(0);
  });
});

describe("the compiled diff is the real PromptDiff, not reimplemented", () => {
  it("invokes PromptDiff in compiled mode for the topic's compiled artifact", async () => {
    render(
      <ProveStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus({ compiledPresent: true })}
        obsidianCtx={{}}
      />,
    );

    const diffMock = await screen.findByTestId("prompt-diff-mock");
    expect(diffMock.getAttribute("data-mode")).toBe("compiled");
  });
});

/**
 * The mechanical check the lane split demands:
 * `grep -rn "onOpen" dashboard/src/lanes/improve/` must return no matches.
 * Automated here as a directory walk rather than left as a manual grep note
 * (the plan's own "Done when"). Vacuously true today -- the directory this
 * suite's own paired implementation step creates doesn't exist yet -- and a
 * real regression guard once `PromoteStage.tsx`/`ProveStage.tsx` and their
 * siblings land.
 *
 * `@types/node` is not a project dependency (the dashboard is browser-only
 * TypeScript), so `fs`/`path`/`url` are loaded the same way `ProveStage`
 * itself is above: a dynamic `import()` whose specifier is a variable, not a
 * string literal, which TypeScript leaves unresolved (and therefore
 * untyped) rather than erroring on a missing `@types/node`. Node still
 * resolves the bare specifier to the real built-in at runtime.
 */
interface FsModule {
  readdirSync(path: string): string[];
  statSync(path: string): { isDirectory(): boolean };
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
let improveLaneDir: string;

beforeAll(async () => {
  fsModule = (await import(FS_MODULE_NAME)) as unknown as FsModule;
  pathModule = (await import(PATH_MODULE_NAME)) as unknown as PathModule;
  const urlModule = (await import(URL_MODULE_NAME)) as unknown as UrlModule;
  improveLaneDir = pathModule.join(
    pathModule.dirname(urlModule.fileURLToPath(import.meta.url)),
    "..",
  );
});

function collectSourceFiles(dir: string): string[] {
  let entries: string[];
  try {
    entries = fsModule.readdirSync(dir);
  } catch {
    return [];
  }
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

function linesMatchingOnOpen(files: string[]): string[] {
  const matches: string[] = [];
  for (const file of files) {
    const content = fsModule.readFileSync(file, "utf-8");
    content.split("\n").forEach((line: string, index: number) => {
      if (line.includes("onOpen")) {
        matches.push(`${file}:${index + 1}: ${line.trim()}`);
      }
    });
  }
  return matches;
}

describe("no cross-lane navigation prop survives anywhere under lanes/improve", () => {
  it('finds zero onOpen* occurrences -- the automated equivalent of grep -rn "onOpen" dashboard/src/lanes/improve/', () => {
    expect(linesMatchingOnOpen(collectSourceFiles(improveLaneDir))).toEqual([]);
  });
});
