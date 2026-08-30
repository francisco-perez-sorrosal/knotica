import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ObsidianContext } from "../../../obsidianLinks";
import type { ToolClient } from "../../../toolClient";
import type { QueryAnswer, WikiStatus } from "../../../types";

/**
 * `dashboard/src/lanes/answer/AnswerLane.tsx` does not exist yet -- this is
 * the RED half of a paired implementation/test step for
 * `INTERFACE_DESIGN.md §2.3` (Answer's `ask -> cite -> react` rail). Loaded
 * through a non-literal dynamic `import()` specifier, the same device
 * `lanes/__tests__/LaneRail.test.tsx`, `lanes/improve/__tests__/ImproveLane.test.tsx`,
 * and `lanes/tend/__tests__/TendLane.test.tsx` used for their own
 * not-yet-existing modules: a literal `import { AnswerLane } from
 * "../AnswerLane"` would fail `tsc --noEmit` for the whole project the
 * moment this file lands; a dynamic import whose argument is not a string
 * literal is left unresolved by TypeScript, so the rest of the tree keeps
 * type-checking while this file fails at *runtime* with the missing-module
 * error the paired implementation step is gated on.
 *
 * `client.noteCapture`/`client.gapReport` do not exist on `ToolClient` yet
 * either (Step 94's own declared crossing into `toolClient.ts`/`types.ts`).
 * `fakeClient` below supplies both as plain `vi.fn()`s on an object cast
 * `as unknown as ToolClient`, mirroring `AskPane.noLoopLink.test.tsx`'s own
 * `fakeClient` pattern -- this keeps the file type-checking today (the cast
 * hides the interface's current shape from the compiler) while still
 * exercising real calls once the paired implementation wires them in.
 *
 * Load-bearing assumptions about the not-yet-landed component (the paired
 * implementation wins on conflict; full reasoning in
 * `LEARNINGS_test-engineer_step95.md`):
 *
 *   1. `<AnswerLane client={...} topic={...} vault={...} obsidianCtx={...}
 *      status={...} />` -- the exact same five props `AskPane` already
 *      takes (`§2.3`'s "absorb `AskPane.tsx`'s existing ask/cite rendering
 *      ... unchanged" reads as prop-surface-preserving, not just
 *      markup-preserving).
 *   2. The rail's three stages render in `AskPane.tsx`/`ImproveLane.tsx`
 *      convention: `<ol aria-label="answer stages">` of three
 *      `<li class="lane-stage" data-state="...">` nodes, the watermark one
 *      carrying `aria-current="step"` (`INTERFACE_DESIGN.md §1.5`'s
 *      accessibility floor, already honored by `LaneRail.tsx`,
 *      `ImproveLane.tsx` and `TendLane.tsx` for every other lane built so
 *      far -- Answer is not carved out as an exception anywhere in the
 *      design doc).
 *   3. The three-phase transition `§2.7`'s Answer row implies is real, not
 *      just a two-phase "idle -> answered" jump: submitting a question
 *      immediately completes `ask` (the question is locked in) and moves
 *      `cite` to `active` while the LLM call is in flight ("`cite` spinner
 *      ... progress prose, not a bar"); only once the promise resolves does
 *      `cite` complete and `react` become the watermark. This is what
 *      `deriveSequenceStages`'s single-watermark model predicts if watermark
 *      advances `0 -> 1 -> 2` across submit-then-resolve, and it is the
 *      reading that makes `ask`'s form genuinely interactive (not merely
 *      labelled `pending`) before anything is typed -- the literal `§2.7`
 *      cell text ("Rail at `ask · pending`") is read as informal English
 *      ("an answer is pending"), not the `StageState` enum's `"pending"`
 *      value, which the interactivity rule (`§1.5`) would make read-only.
 *   4. `react`'s four actions are plain, no-second-input clicks (no dialog,
 *      no free-text box) -- the mockup renders all four as one button row
 *      with nothing else, consistent with the "no native dialogs"
 *      (orchestrator ruling) discipline this milestone applies elsewhere.
 *      `client.noteCapture`'s `note`/`quote`/`pages`/`intent`/`tags` and
 *      `client.gapReport`'s `reason` are therefore asserted only where the
 *      design pins a concrete value (`gap_report`'s `question` is plainly
 *      "the question that had no coverage" -- the one just asked); fields
 *      the design does not pin are asserted for shape (present, correctly
 *      typed) rather than for exact content.
 *   5. Reaching `react`'s watermark renders the four action buttons with
 *      the mockup's literal labels: "Good example", "Bad example",
 *      "Note it", "Report gap".
 *
 * Not tested here (out of this step's scope, already covered elsewhere or
 * orthogonal to it): `status`'s compiled-version narration (clause 1) is
 * `AskPane`'s pre-existing, already-regression-tested behaviour
 * (`AskPane.noLoopLink.test.tsx`) -- every fixture below passes `status={null}`
 * since this suite is about the rail and the `react` wiring, not that
 * narration; the exact markdown-rendering/citation-linking internals
 * (`AnswerCard`) are `AskPane`'s own unchanged implementation detail.
 */

interface AnswerLaneProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  obsidianCtx: ObsidianContext;
  status: WikiStatus | null;
}

type AnswerLaneComponent = (props: AnswerLaneProps) => JSX.Element;

interface AnswerLaneModule {
  AnswerLane: AnswerLaneComponent;
}

const ANSWER_LANE_MODULE_PATH = "../AnswerLane";

let AnswerLane: AnswerLaneComponent;

beforeAll(async () => {
  ({ AnswerLane } = (await import(
    ANSWER_LANE_MODULE_PATH
  )) as AnswerLaneModule);
});

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";
const QUESTION = "How does MIPROv2 pick demonstrations?";

const ASK = 0;
const CITE = 1;
const REACT = 2;

function answer(overrides: Partial<QueryAnswer> = {}): QueryAnswer {
  return {
    topic: TOPIC,
    question: QUESTION,
    answer: "MIPROv2 bootstraps few-shot demonstrations from the trainset.",
    citations: ["mipro-overview.md"],
    pages_used: ["mipro-overview.md"],
    ...overrides,
  };
}

/** A query promise this suite can resolve on its own schedule, to observe the
 * in-flight (`cite` active/loading) state before the answer lands.
 *
 * **Implementer's fix (Step 94, declared deviation, zero assertion change)**:
 * the original draft returned the bare `resolve` local, which is only ever
 * assigned once `query()` actually runs -- but the destructured `{ resolve }`
 * a caller receives is a value snapshot taken at `deferredQuery()`'s own
 * return, i.e. always the pre-assignment `undefined`. Wrapping it in a
 * trampoline closure defers the lookup of the real resolver until the
 * cleanup call actually fires, by which point `query()` has run and assigned
 * it. Verified against plain Node before touching this file. */
function deferredQuery(): {
  query: ReturnType<typeof vi.fn>;
  resolve: (value: QueryAnswer) => void;
} {
  let resolve!: (value: QueryAnswer) => void;
  const query = vi.fn(
    () =>
      new Promise<QueryAnswer>((res) => {
        resolve = res;
      }),
  );
  return { query, resolve: (value) => resolve(value) };
}

/** Boundary fake of `ToolClient` (`dashboard/CLAUDE.md`: "the single seam for
 * MCP calls") -- only the methods Answer's rail reaches. */
function fakeClient(overrides: Record<string, unknown> = {}) {
  const query = overrides.query ?? vi.fn(async () => answer());
  const curateExample = overrides.curateExample ?? vi.fn(async () => ({}));
  const noteCapture = overrides.noteCapture ?? vi.fn(async () => ({}));
  const gapReport = overrides.gapReport ?? vi.fn(async () => ({}));
  const client = {
    query,
    curateExample,
    noteCapture,
    gapReport,
    ...overrides,
  } as unknown as ToolClient;
  return { client, query, curateExample, noteCapture, gapReport };
}

function renderAnswerLane(client: ToolClient): Element {
  return render(
    <AnswerLane
      client={client}
      topic={TOPIC}
      vault={VAULT}
      obsidianCtx={{}}
      status={null}
    />,
  ).container;
}

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

async function askAndAwaitAnswer(): Promise<void> {
  fireEvent.input(screen.getByPlaceholderText("Ask the wiki…"), {
    target: { value: QUESTION },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await screen.findByRole("button", { name: "Good example" });
}

describe("the rail container (INTERFACE_DESIGN.md §1.5 accessibility floor)", () => {
  it("labels the stage list with the lane name and renders exactly three stages", () => {
    const { client } = fakeClient();
    const container = renderAnswerLane(client);

    expect(screen.getByRole("list", { name: "answer stages" })).toBeTruthy();
    expect(stageNodes(container)).toHaveLength(3);
  });

  it("marks ask as the current stage before any question is asked", () => {
    const { client } = fakeClient();
    const container = renderAnswerLane(client);

    const nodes = stageNodes(container);
    expect(nodes[ASK].getAttribute("aria-current")).toBe("step");
    expect(nodes[CITE].getAttribute("aria-current")).toBeNull();
    expect(nodes[REACT].getAttribute("aria-current")).toBeNull();
  });
});

describe("before any question is asked -- the empty state is the form", () => {
  it("renders the question box and the Ask control, nothing from cite or react", () => {
    const { client } = fakeClient();
    const container = renderAnswerLane(client);

    expect(screen.getByPlaceholderText("Ask the wiki…")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Good example" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Report gap" })).toBeNull();

    const nodes = stageNodes(container);
    expect(nodes[CITE].dataset.state).toBe("pending");
    expect(nodes[REACT].dataset.state).toBe("pending");
  });
});

describe("while awaiting an answer -- cite is the loading stage, not ask", () => {
  it("completes ask and activates cite the moment a question is submitted", () => {
    const { query, resolve } = deferredQuery();
    const { client } = fakeClient({ query });
    const container = renderAnswerLane(client);

    fireEvent.input(screen.getByPlaceholderText("Ask the wiki…"), {
      target: { value: QUESTION },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    const nodes = stageNodes(container);
    expect(nodes[ASK].dataset.state).toBe("complete");
    expect(nodes[CITE].dataset.state).toBe("active");
    expect(nodes[REACT].dataset.state).toBe("pending");
    expect(container.querySelector("[role='progressbar']")).toBeNull();

    // Clean up the still-pending promise so it does not leak into the next test.
    resolve(answer());
  });
});

describe("once an answer arrives -- ask and cite complete, react becomes current", () => {
  it("renders the answer, its citations, and the four react actions", async () => {
    const { client } = fakeClient();
    const container = renderAnswerLane(client);

    await askAndAwaitAnswer();

    const nodes = stageNodes(container);
    expect(nodes[ASK].dataset.state).toBe("complete");
    expect(nodes[CITE].dataset.state).toBe("complete");
    expect(nodes[REACT].dataset.state).toBe("active");
    expect(nodes[REACT].getAttribute("aria-current")).toBe("step");

    expect(
      screen.getByText(
        "MIPROv2 bootstraps few-shot demonstrations from the trainset.",
      ),
    ).toBeTruthy();
    // `AnswerCard` (absorbed unchanged from `AskPane.tsx`) renders the same
    // page name twice when `citations` and `pages_used` overlap -- once as a
    // `<code>` citation, once in the "Pages" list. Implementer's fix (Step
    // 94, declared deviation, zero assertion weakened): assert presence via
    // `getAllByText`, not a specific count of one.
    expect(screen.getAllByText("mipro-overview.md").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Good example" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Bad example" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Note it" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Report gap" })).toBeTruthy();
  });
});

describe("react's four actions terminate inside Answer (§2.0 clause 2, §2.3 clause 2)", () => {
  it("Good example calls curateExample with the unchanged verdict shape", async () => {
    const { client, curateExample } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Good example" }));

    await vi.waitFor(() => expect(curateExample).toHaveBeenCalledTimes(1));
    expect(curateExample).toHaveBeenCalledWith(
      TOPIC,
      QUESTION,
      "MIPROv2 bootstraps few-shot demonstrations from the trainset.",
      "good",
      ["mipro-overview.md"],
      VAULT,
    );
  });

  it("Bad example calls curateExample with the unchanged verdict shape", async () => {
    const { client, curateExample } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Bad example" }));

    await vi.waitFor(() => expect(curateExample).toHaveBeenCalledTimes(1));
    expect(curateExample).toHaveBeenCalledWith(
      TOPIC,
      QUESTION,
      "MIPROv2 bootstraps few-shot demonstrations from the trainset.",
      "bad",
      ["mipro-overview.md"],
      VAULT,
    );
  });

  it("Note it calls the new client.noteCapture method, scoped to this topic and vault", async () => {
    const { client, noteCapture } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Note it" }));

    await vi.waitFor(() => expect(noteCapture).toHaveBeenCalledTimes(1));
    const [calledTopic, calledNote, , , , , calledVault] = (
      noteCapture as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    expect(calledTopic).toBe(TOPIC);
    expect(typeof calledNote).toBe("string");
    expect((calledNote as string).length).toBeGreaterThan(0);
    expect(calledVault ?? VAULT).toBe(VAULT);
  });

  it("Report gap calls the new client.gapReport method with the question that lacked coverage", async () => {
    const { client, gapReport } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Report gap" }));

    await vi.waitFor(() => expect(gapReport).toHaveBeenCalledTimes(1));
    const call = (gapReport as ReturnType<typeof vi.fn>).mock.calls[0];
    const [calledTopic, calledQuestion, , calledReferencePages, calledVault] =
      call;
    expect(calledTopic).toBe(TOPIC);
    expect(calledQuestion).toBe(QUESTION);
    expect(calledReferencePages).toEqual(["mipro-overview.md"]);
    expect(calledVault ?? VAULT).toBe(VAULT);
  });

  it("updates the rail's terminal to 'answer + signal' only after a react action, never before", async () => {
    const { client } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    expect(screen.queryByText(/answer \+ signal/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Good example" }));

    await screen.findByText(/answer \+ signal/i);
  });

  it("never calls a cross-lane navigation callback even when a caller still supplies one", async () => {
    const onOpenImprove = vi.fn();
    const { client } = fakeClient({ onOpenImprove });
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Report gap" }));
    await vi.waitFor(() =>
      expect(screen.queryByRole("button", { name: "Report gap" })).toBeTruthy(),
    );

    expect(onOpenImprove).not.toHaveBeenCalled();
  });
});

describe("the lifecycle contract on React's verbs", () => {
  it("names the lane each signal actually feeds, and a different one per verb", async () => {
    // React's three verbs were the app's strongest "did that do anything?" —
    // they recorded a signal into a lane the user was never told about. The
    // outcome now names it, and the destination differs per verb, which is
    // the whole reason the sentence is worth rendering.
    const { client } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Report gap" }));
    await screen.findByText(/Go to Fill → Discover\./);

    cleanup();
    renderAnswerLane(fakeClient().client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Good example" }));
    await screen.findByText(/Go to Improve → Instrument\./);
  });

  it("keeps one live region: the verb's own sentence carries the outcome", async () => {
    const { client } = fakeClient();
    const container = renderAnswerLane(client);
    await askAndAwaitAnswer();

    fireEvent.click(screen.getByRole("button", { name: "Note it" }));

    await vi.waitFor(() => {
      const live = Array.from(
        container.querySelectorAll<HTMLElement>('[role="status"]'),
      );
      expect(live).toHaveLength(1);
      expect(live[0].textContent).toBe("Answer + signal: Captured as a note.");
    });
  });
});

describe("ephemeral by design (§2.3's explicit decision: query stays a non-writer)", () => {
  it("does not restore cite/react state across a fresh mount", async () => {
    const { client } = fakeClient();
    renderAnswerLane(client);
    await askAndAwaitAnswer();

    cleanup();

    const { client: freshClient } = fakeClient();
    const freshContainer = renderAnswerLane(freshClient);

    expect(screen.getByPlaceholderText("Ask the wiki…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Good example" })).toBeNull();
    const nodes = stageNodes(freshContainer);
    expect(nodes[ASK].getAttribute("aria-current")).toBe("step");
  });
});
