import { cleanup, fireEvent, render, within } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ToolClient } from "../../../toolClient";

/**
 * `dashboard/src/lanes/home/HomeLane.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step (`IMPLEMENTATION_PLAN.md`
 * Steps 113/114, `INTERFACE_DESIGN.md §2.1`/`§4.2`, `dec-092`). This suite
 * spawns *before* Step 113's implementer, per the RED-handshake fix: the
 * standalone run below must fail at collection with a missing-module error,
 * which the paired implementation step is gated on.
 *
 * Loaded through a non-literal dynamic `import()` -- the same device
 * `m5HomeCensus.test.tsx`/`IngestGateStage.test.tsx` used for their own
 * not-yet-existing modules: a literal `import { HomeLane } from
 * "../HomeLane"` would fail `tsc --noEmit` for the whole project the moment
 * this file lands.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (full reasoning in `LEARNINGS_test-engineer_step114.md`; the paired
 * implementation wins on conflict):
 *
 *   1. `<HomeLane client={...} vault={...} onOpenLane={...} />` -- the same
 *      three props `m5HomeCensus.test.tsx`'s own Step-112 smoke render
 *      already guessed (`client`, `vault`, `onOpenLane`); no `topic` prop,
 *      since Home is cross-topic.
 *   2. On mount, fetches `client.wikiStatus("", vault, "attention")` --
 *      `ToolClient.wikiStatus`'s new optional third `view` parameter this
 *      same step's implementer adds.
 *   3. Re-fetches on every tick of a `startVisibilityPausedPoll(callback,
 *      10_000)` poll (`dashboard/src/lanes/visibilityPausedPoll.ts`, Step
 *      107) -- its *own* poll, independent of `App.tsx`'s 2 s
 *      `view="summary"` poll used by every other lane's rail. The module is
 *      mocked below so this suite drives the callback directly rather than
 *      depending on real interval timing (already covered by
 *      `visibilityPausedPoll.test.ts`, Step 108).
 *   4. Each row carries a `[Open]` or `[Watch]` button (`"Watch"` only for
 *      `urgency: "running"` rows) that calls `onOpenLane` with the row's own
 *      lane and nothing else -- Home is the one legitimate `onOpen*`-shaped
 *      occupant (`INTERFACE_DESIGN.md §2.0` clause 3).
 *   5. The drift row renders unconditionally as one line with a `[Check]`
 *      affordance; per Step 113's own declared scope, `[Check]` has no click
 *      handler yet -- clicking it must not reach the client beyond the
 *      initial `wikiStatus` fetch and must not call `onOpenLane`.
 *   6. Payload shape is pinned from the live server seam
 *      (`core/status.py::_attention_status`/`_attention_row`), not from
 *      `INTERFACE_DESIGN.md §2.1`'s illustrative mockup -- see
 *      `attentionRows.test.ts`'s own docblock for the full reasoning.
 *
 * Not tested here (other steps' job): the `[Check]` affordance's eventual
 * click behavior (out of Step 113's declared scope), routing/pane wiring
 * (`Step 115/116`), the `deriveAttentionRows` grouping logic itself (unit
 * -tested directly in `attentionRows.test.ts`).
 */

interface AttentionSuggestions {
  pending: number;
  refused_awaiting_rework: number;
}

interface AttentionTopicRow {
  topic: string;
  suggestions: AttentionSuggestions;
  compile_ready: boolean;
  runner: { alive: boolean };
}

interface AttentionStatus {
  schema_version: number;
  vault_name: string;
  topics: AttentionTopicRow[];
  totals: {
    topics: number;
    pending: number;
    refused_awaiting_rework: number;
    compile_ready: number;
    runners_alive: number;
  };
  last_lint: { date: string | null; age_days: number | null; stale: boolean };
  drift: { default_collapsed: boolean; count: number | null };
}

type HomeLaneProps = {
  client: ToolClient | null;
  vault: string;
  onOpenLane: (lane: string) => void;
};

type HomeLaneComponent = (props: HomeLaneProps) => JSX.Element;

const HOME_LANE_MODULE_PATH = "../HomeLane";
const VAULT = "v";

const QUIET_TOPIC: AttentionTopicRow = {
  topic: "quiet-topic",
  suggestions: { pending: 0, refused_awaiting_rework: 0 },
  compile_ready: false,
  runner: { alive: false },
};

const BLOCKED_TOPIC: AttentionTopicRow = {
  topic: "rag-patterns",
  suggestions: { pending: 0, refused_awaiting_rework: 1 },
  compile_ready: false,
  runner: { alive: false },
};

const WAITING_TOPIC: AttentionTopicRow = {
  topic: "gap-fill",
  suggestions: { pending: 4, refused_awaiting_rework: 0 },
  compile_ready: false,
  runner: { alive: false },
};

const RUNNING_TOPIC: AttentionTopicRow = {
  topic: "agentic-systems",
  suggestions: { pending: 0, refused_awaiting_rework: 0 },
  compile_ready: false,
  runner: { alive: true },
};

function attentionPayload(topics: AttentionTopicRow[]): AttentionStatus {
  return {
    schema_version: 1,
    vault_name: "kb",
    topics,
    totals: {
      topics: topics.length,
      pending: topics.reduce((n, t) => n + t.suggestions.pending, 0),
      refused_awaiting_rework: topics.reduce(
        (n, t) => n + t.suggestions.refused_awaiting_rework,
        0,
      ),
      compile_ready: topics.filter((t) => t.compile_ready).length,
      runners_alive: topics.filter((t) => t.runner.alive).length,
    },
    last_lint: { date: "2026-08-20", age_days: 6, stale: false },
    drift: { default_collapsed: true, count: null },
  };
}

// ---------------------------------------------------------------------------
// `visibilityPausedPoll` is mocked, not exercised for real: its own pause/
// resume contract is Step 108's suite. `startVisibilityPausedPoll` is
// referenced inside the mock factory's *nested* function only, so it reads
// `pollCallback`/`pollTeardown` lazily -- at the time `HomeLane` actually
// calls it during a render inside an `it()` block, by which point these
// bindings are already initialized (mirrors `ImproveLane.test.tsx`'s own
// `captured`-object closure convention).
// ---------------------------------------------------------------------------
let pollCallback: (() => void) | null = null;
let pollIntervalMs: number | null = null;
const pollTeardown = vi.fn();

vi.mock("../../visibilityPausedPoll", () => ({
  startVisibilityPausedPoll: (callback: () => void, intervalMs: number) => {
    pollCallback = callback;
    pollIntervalMs = intervalMs;
    return pollTeardown;
  },
}));

function fakeClient(payload: AttentionStatus) {
  const wikiStatus = vi.fn(async (..._args: unknown[]) => payload);
  const raw = { wikiStatus };
  return { client: raw as unknown as ToolClient, wikiStatus };
}

async function renderHomeLane(
  payload: AttentionStatus,
  onOpenLane: (lane: string) => void = vi.fn(),
) {
  const { HomeLane } = (await import(HOME_LANE_MODULE_PATH)) as {
    HomeLane: HomeLaneComponent;
  };
  const { client, wikiStatus } = fakeClient(payload);
  // `render(...).container` is typed `Element` by the library but is always
  // a real `<div>` at runtime -- the same cast `IngestGateStage.test.tsx`
  // uses for the identical reason (it is passed into `within()` directly).
  const container = render(
    <HomeLane client={client} vault={VAULT} onOpenLane={onOpenLane} />,
  ).container as HTMLElement;
  await vi.waitFor(() => expect(wikiStatus).toHaveBeenCalled());
  return { container, wikiStatus, onOpenLane, client };
}

beforeEach(() => {
  pollCallback = null;
  pollIntervalMs = null;
  pollTeardown.mockClear();
});

afterEach(cleanup);

describe("fetches the cross-topic attention view on mount", () => {
  it("calls wikiStatus with an empty topic and the attention view", async () => {
    const { wikiStatus } = await renderHomeLane(attentionPayload([]));
    expect(wikiStatus).toHaveBeenCalledWith("", VAULT, "attention");
  });
});

describe("blocked class", () => {
  it("renders a why-it-needs-you narration and routes [Open] to fill only", async () => {
    const onOpenLane = vi.fn();
    const { container } = await renderHomeLane(
      attentionPayload([BLOCKED_TOPIC]),
      onOpenLane,
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/rag-patterns/),
    );
    fireEvent.click(within(container).getByRole("button", { name: /open/i }));
    expect(onOpenLane).toHaveBeenCalledTimes(1);
    expect(onOpenLane).toHaveBeenCalledWith("fill");
  });
});

describe("waiting class", () => {
  it("renders a pending-suggestions row and routes [Open] to fill", async () => {
    const onOpenLane = vi.fn();
    const { container } = await renderHomeLane(
      attentionPayload([WAITING_TOPIC]),
      onOpenLane,
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/gap-fill/),
    );
    fireEvent.click(within(container).getByRole("button", { name: /open/i }));
    expect(onOpenLane).toHaveBeenCalledTimes(1);
    expect(onOpenLane).toHaveBeenCalledWith("fill");
  });
});

describe("running class", () => {
  it("renders an in-flight row with a Watch action routed to improve", async () => {
    const onOpenLane = vi.fn();
    const { container } = await renderHomeLane(
      attentionPayload([RUNNING_TOPIC]),
      onOpenLane,
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/agentic-systems/),
    );
    fireEvent.click(
      within(container).getByRole("button", { name: /watch/i }),
    );
    expect(onOpenLane).toHaveBeenCalledTimes(1);
    expect(onOpenLane).toHaveBeenCalledWith("improve");
  });
});

describe("empty state -- the success state", () => {
  it("renders 'Nothing needs you' when every topic is quiet", async () => {
    const { container } = await renderHomeLane(
      attentionPayload([QUIET_TOPIC]),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/nothing needs you/i),
    );
    expect(container.textContent).not.toMatch(/quiet-topic/);
  });

  it("never renders a row for a quiet topic even alongside busy ones", async () => {
    const { container } = await renderHomeLane(
      attentionPayload([QUIET_TOPIC, RUNNING_TOPIC]),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/agentic-systems/),
    );
    expect(container.textContent).not.toMatch(/quiet-topic/);
  });
});

describe("Home is an inbox, not a rail", () => {
  it("renders no LaneRail markup", async () => {
    const { container } = await renderHomeLane(
      attentionPayload([BLOCKED_TOPIC, RUNNING_TOPIC]),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/rag-patterns/),
    );
    expect(container.querySelector(".lane-rail")).toBeNull();
    expect(container.querySelector(".lane-stage")).toBeNull();
  });
});

describe("drift row -- default-collapsed, pays its cost only on expand", () => {
  it("renders unconditionally with a [Check] affordance", async () => {
    const { container } = await renderHomeLane(
      attentionPayload([BLOCKED_TOPIC]),
    );
    await vi.waitFor(() =>
      expect(container.textContent).toMatch(/drift/i),
    );
    expect(
      within(container).getByRole("button", { name: /check/i }),
    ).toBeTruthy();
  });

  it("makes no client call and no onOpenLane call beyond the initial fetch, even when every button is clicked", async () => {
    const payload = attentionPayload([BLOCKED_TOPIC]);
    const onOpenLane = vi.fn();
    const { client: raw, wikiStatus } = fakeClient(payload);
    const allowed = new Set(["wikiStatus"]);
    const offenders: string[] = [];
    const guarded = new Proxy(raw as unknown as Record<string, unknown>, {
      get(target, prop, receiver) {
        if (typeof prop === "string" && !allowed.has(prop)) {
          offenders.push(prop);
        }
        return Reflect.get(target, prop, receiver);
      },
    }) as unknown as ToolClient;

    const { HomeLane } = (await import(HOME_LANE_MODULE_PATH)) as {
      HomeLane: HomeLaneComponent;
    };
    const container = render(
      <HomeLane client={guarded} vault={VAULT} onOpenLane={onOpenLane} />,
    ).container as HTMLElement;
    await vi.waitFor(() => expect(wikiStatus).toHaveBeenCalledTimes(1));

    const checkButton = within(container).getByRole("button", {
      name: /check/i,
    });
    fireEvent.click(checkButton);

    expect(offenders).toEqual([]);
    expect(wikiStatus).toHaveBeenCalledTimes(1);
    expect(onOpenLane).not.toHaveBeenCalled();
  });
});

describe("the poll is wired through startVisibilityPausedPoll, not a bare interval", () => {
  it("starts the poll at the 10s cadence declared by dec-092", async () => {
    await renderHomeLane(attentionPayload([]));
    expect(pollIntervalMs).toBe(10_000);
    expect(pollCallback).not.toBeNull();
  });

  it("re-fetches only when the poll's own callback fires, never on its own", async () => {
    const { wikiStatus } = await renderHomeLane(attentionPayload([]));
    expect(wikiStatus).toHaveBeenCalledTimes(1);

    // Invoking the mocked poll's captured callback is the *only* thing that
    // should trigger a second fetch -- proving the cadence is delegated to
    // `startVisibilityPausedPoll`, not a `setInterval` HomeLane runs itself.
    pollCallback?.();
    await vi.waitFor(() => expect(wikiStatus).toHaveBeenCalledTimes(2));
  });

  it("tears down the poll on unmount", async () => {
    const { HomeLane } = (await import(HOME_LANE_MODULE_PATH)) as {
      HomeLane: HomeLaneComponent;
    };
    const { client, wikiStatus } = fakeClient(attentionPayload([]));
    const { unmount } = render(
      <HomeLane client={client} vault={VAULT} onOpenLane={vi.fn()} />,
    );
    await vi.waitFor(() => expect(wikiStatus).toHaveBeenCalled());
    unmount();
    expect(pollTeardown).toHaveBeenCalledTimes(1);
  });
});
