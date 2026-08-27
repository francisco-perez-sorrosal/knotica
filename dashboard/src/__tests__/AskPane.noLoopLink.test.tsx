import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AskPane } from "../AskPane";
import type { ToolClient } from "../toolClient";
import type { QueryAnswer, WikiStatus } from "../types";

/**
 * `INTERFACE_DESIGN.md §2.0` clause 1-2: Answer may narrate a shared object
 * in its own vocabulary ("answered with compiled v3") but may not link to
 * another lane. Clause 3's mechanical check names `AskPane` as the one
 * survives-with-edits case: "remove `AskPane.tsx`'s `onOpenLoop`/
 * `onOpenArena` props, their two buttons, and the flywheel/compile action
 * banners (`AskPane.tsx:158-183`)". `AskPane.tsx` already exists (this is not
 * a missing-module RED), so RED here is behavioral: the three banners this
 * suite asserts absent are all live in the current file, and this test
 * exercises exactly the state that renders each of them today. The suite
 * turns green only once the paired implementation step (Step 73) deletes
 * that block.
 *
 * `renders no Watch Loop control...` passes `onOpenLoop` through a double
 * cast (`as unknown as Parameters<typeof AskPane>[0]`) rather than a typed
 * JSX attribute, so this file keeps compiling whether or not the paired
 * implementation removes `onOpenLoop` from `AskPane`'s prop type -- the
 * assertion is about rendered behavior, not about what TypeScript will still
 * let a caller pass.
 *
 * One test (`still narrates its own compiled-version status...`) is a
 * regression guard, not a RED assertion: the `.ask-delta` one-line narration
 * lives outside the deleted `AskPane.tsx:158-183` range and is unchanged by
 * this step, so it already passes today and must keep passing after.
 */

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

function answer(text: string, question = "q"): QueryAnswer {
  return {
    topic: TOPIC,
    question,
    answer: text,
    citations: [],
    pages_used: [],
  };
}

function topicRow(
  overrides: Partial<WikiStatus["topics"][number]> = {},
): WikiStatus["topics"][number] {
  return {
    topic: TOPIC,
    pages: 10,
    curated: 5,
    to_compile_ready: 0,
    compile_ready: false,
    compiled: null,
    lint_violations: 0,
    last_eval: null,
    ...overrides,
  };
}

function baseStatus(
  overrides: {
    topicRow?: Partial<WikiStatus["topics"][number]>;
    gateState?: WikiStatus["gate"]["state"];
  } = {},
): WikiStatus {
  return {
    schema_version: 1,
    vault: VAULT,
    vault_name: VAULT,
    vault_path: "/tmp/vault",
    default_vault: VAULT,
    available_vaults: [],
    compile_ready_threshold: 20,
    topics: [topicRow(overrides.topicRow)],
    totals: { topics: 1, pages: 10, curated: 5, lint_violations: 0 },
    last_lint: null,
    unpushed: null,
    gate: {
      state: overrides.gateState ?? "unknown",
      baseline: null,
      last_scalar: null,
    },
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
    curateExample: vi.fn(),
    ...overrides,
  } as unknown as ToolClient;
}

async function askAndGetAnswer(text: string) {
  fireEvent.input(screen.getByPlaceholderText("Ask the wiki…"), {
    target: { value: "q" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  return screen.findByText(text);
}

describe("Answer keeps only its own compiled-version narration -- no flywheel/compile action banners", () => {
  it("renders no flywheel-ready banner urging a Loop/Compile action", () => {
    const status = baseStatus({
      topicRow: { compile_ready: true, compiled: null },
    });

    render(
      <AskPane
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        obsidianCtx={{}}
        status={status}
      />,
    );

    expect(screen.queryByText("Flywheel ready")).toBeNull();
  });

  it("renders no compiled-engine-live banner urging a re-ask via Loop", async () => {
    const status = baseStatus({
      topicRow: {
        compiled: {
          present: true,
          version: "v3",
          scalar: 0.7,
          compiled_at: "2026-08-01T00:00:00Z",
        },
      },
    });
    const query = vi.fn().mockResolvedValue(answer("a1"));

    render(
      <AskPane
        client={fakeClient({ query })}
        topic={TOPIC}
        vault={VAULT}
        obsidianCtx={{}}
        status={status}
      />,
    );

    await askAndGetAnswer("a1");
    fireEvent.click(screen.getByRole("button", { name: "Pin as Before" }));

    expect(screen.queryByText("Compiled engine is live")).toBeNull();
  });

  it("renders no gate-is-red banner or an Open Arena control", () => {
    const status = baseStatus({ gateState: "fail" });

    render(
      <AskPane
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        obsidianCtx={{}}
        status={status}
      />,
    );

    expect(screen.queryByText("Gate is red")).toBeNull();
    expect(screen.queryByRole("button", { name: "Open Arena" })).toBeNull();
  });

  it("renders no Watch Loop control even when a caller still supplies onOpenLoop", async () => {
    const onOpenLoop = vi.fn();
    const query = vi.fn().mockResolvedValue(answer("a1"));
    const props = {
      client: fakeClient({ query }),
      topic: TOPIC,
      vault: VAULT,
      obsidianCtx: {},
      status: baseStatus(),
      onOpenLoop,
    } as unknown as Parameters<typeof AskPane>[0];

    render(<AskPane {...props} />);

    await askAndGetAnswer("a1");
    fireEvent.click(screen.getByRole("button", { name: "Pin as Before" }));

    expect(screen.queryByRole("button", { name: "Watch Loop" })).toBeNull();
    expect(onOpenLoop).not.toHaveBeenCalled();
  });
});

describe("Answer still narrates its own compiled-version status in one line (clause 1)", () => {
  it("keeps the after-answer narration reading only its own object, unrelated to the removed banners", async () => {
    const status = baseStatus({
      topicRow: {
        compiled: {
          present: true,
          version: "v3",
          scalar: 0.7,
          compiled_at: "2026-08-01T00:00:00Z",
        },
      },
    });
    const query = vi
      .fn()
      .mockResolvedValueOnce(answer("Before answer."))
      .mockResolvedValueOnce(answer("After answer."));

    render(
      <AskPane
        client={fakeClient({ query })}
        topic={TOPIC}
        vault={VAULT}
        obsidianCtx={{}}
        status={status}
      />,
    );

    await askAndGetAnswer("Before answer.");
    fireEvent.click(screen.getByRole("button", { name: "Pin as Before" }));

    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText("After answer.");

    expect(screen.getByRole("status").textContent?.toLowerCase()).toContain(
      "compil",
    );
  });
});
