import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/preact";
import type { JSX } from "preact";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { ToolClient } from "../../toolClient";
import type { HostCapabilities, Mount } from "../hostCapabilities";

/**
 * `dashboard/src/lanes/HandoffStage.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step for the shared handoff stage
 * (`INTERFACE_DESIGN.md §3`, `dec-091`). Loaded through a non-literal dynamic
 * `import()` for the same reason every other not-yet-existing-module suite in
 * this tree does (`hostCapabilities.test.ts`, `QueueStage.test.tsx`): a
 * literal `import { HandoffStage } from "../HandoffStage"` would fail
 * `tsc --noEmit` for the whole project the moment this file lands; a dynamic
 * import whose argument is not a string literal is left unresolved by
 * TypeScript, so the rest of the tree keeps type-checking while this file
 * fails at *runtime* with the missing-module error the paired implementation
 * step is gated on.
 *
 * `SessionStatus` is mirrored locally rather than imported from `../../types`
 * -- Step 87 adds that type to `types.ts` alongside the component itself, so
 * it does not exist on disk yet either. The mirror below is this suite's own
 * copy of `INTERFACE_DESIGN.md §3.3`'s wire contract, not an import of the
 * real one; once the module lands, `HandoffStage.tsx`'s own `import type`
 * from `types.ts` is what actually proves the real shape matches.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (full reasoning in `LEARNINGS_test-engineer_step88.md`; the paired
 * implementation wins on conflict):
 *
 *   1. `<HandoffStage client vault topic suggestionId command ask active
 *      renderYouControl />` -- the six data/config props Step 87 names
 *      verbatim, plus a `renderYouControl: (status: SessionStatus) =>
 *      JSX.Element | null` render prop for the `next.actor === "you"`
 *      control ("Submit / Rework / Open a session ... never invented inside
 *      this component").
 *   2. Neither `hostCapabilities` nor `mount` is a prop of `HandoffStage`
 *      itself -- Step 87's prop list has no such entries. Both are read off
 *      the `client` it already receives (`client.hostCapabilities`,
 *      `client.mount`), the two fields `dec-091`'s plumbing clause (5) and
 *      Step 85 add to `ToolClient` -- consistent with "no `mount ===
 *      'bridge'` string check anywhere outside `deriveDispatchTier`": the
 *      lane never re-derives the mount itself, it only forwards what the
 *      client already carries.
 *   3. `ToolClient` gains `sendMessage(text: string): Promise<void>` and
 *      `updateModelContext(text: string): Promise<void>` (`dec-091` clause
 *      5). Tier-gating happens at the *call site* via `deriveDispatchTier`,
 *      not inside these two methods -- they are dumb wire calls, guarded by
 *      which one `HandoffStage` chooses to invoke for the resolved tier.
 *   4. The trailing dispatch line is `/knotica:<command> <suggestionId>
 *      <topic>` -- `commands/fill.md`'s own `argument-hint: "<suggestion-id>
 *      [topic]"`, not the bare `/knotica:ingest` `dec-091`'s illustrative
 *      example uses (that example predates a concrete Fill command
 *      existing).
 *   5. Every `next.actor === "you"` state (`not_started`, `client_wrote`,
 *      `refused`, `blocked`, `swept`) invokes `renderYouControl` uniformly --
 *      `HandoffStage` does not special-case any one of them with its own
 *      hardcoded button copy. `refused`'s dilution reason
 *      (`gate_outcome.reason`) and every state's `next.do` narration are
 *      rendered by `HandoffStage` itself, alongside whatever
 *      `renderYouControl` returns -- both are payload-derived text, not
 *      invented control copy.
 *   6. The conditional-poll pattern (`useEffect` guarded by `active`,
 *      immediate tick + `window.setInterval(tick, 3000)`, cleanup on
 *      unmount/dep-change) is the one `CompilePanel.tsx:56` established --
 *      cited in `IMPLEMENTATION_PLAN.md` Step 87, but that file was deleted
 *      in the M3 pane dissolution (`git show 97349b4^:dashboard/src/
 *      CompilePanel.tsx`). Recorded as a testability note, not a load-bearing
 *      assumption about `HandoffStage`'s own behavior: the poll-discipline
 *      assertions below test the *contract* (one call per 3 s tick while
 *      `active`, none while not), not which file the pattern was copied
 *      from.
 */

// ---------------------------------------------------------------------------
// Local mirror of INTERFACE_DESIGN.md §3.3's wire contract.
// ---------------------------------------------------------------------------

type SessionState =
  | "not_started"
  | "waiting_on_client"
  | "client_wrote"
  | "rework_in_flight"
  | "submitted"
  | "merged"
  | "refused"
  | "blocked"
  | "swept";

type SessionNextActor = "you" | "claude" | "system" | "none";

interface SessionGateOutcome {
  verdict: "merged" | "refused";
  scalar: number;
  baseline_scalar: number;
  reason?: string;
}

interface SessionStatus {
  suggestion_id: string;
  stage: string;
  stage_index: number;
  state: SessionState;
  source_present: boolean;
  pages_present: string[];
  index_synced: boolean;
  gate_eligible: boolean;
  gate_eligible_reason: string;
  restored_from: string | null;
  gate_outcome: SessionGateOutcome | null;
  next: { actor: SessionNextActor; do: string };
}

interface HandoffStageProps {
  client: ToolClient;
  topic: string;
  suggestionId: string;
  vault: string;
  command: string;
  ask: string;
  active: boolean;
  renderYouControl: (status: SessionStatus) => JSX.Element | null;
}

type HandoffStageComponent = (props: HandoffStageProps) => JSX.Element;

interface HandoffStageModule {
  HandoffStage: HandoffStageComponent;
}

const HANDOFF_STAGE_MODULE_PATH = "../HandoffStage";

let HandoffStage: HandoffStageComponent;

beforeAll(async () => {
  ({ HandoffStage } = (await import(
    HANDOFF_STAGE_MODULE_PATH
  )) as HandoffStageModule);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const TOPIC = "rag-patterns";
const VAULT = "kb";
const SUGGESTION_ID = "s_1a2b3c4d";
const COMMAND = "fill";
const ASK = `Claude writes the pages for "attention-is-all-you-need" into ${TOPIC}, using the open candidate session.`;
const DISPATCH_LINE_PATTERN = new RegExp(
  `/knotica:${COMMAND}\\s+${SUGGESTION_ID}\\s+${TOPIC}`,
);

function baseSessionStatus(
  overrides: Partial<SessionStatus> = {},
): SessionStatus {
  return {
    suggestion_id: SUGGESTION_ID,
    stage: "ingest",
    stage_index: 2,
    state: "not_started",
    source_present: false,
    pages_present: [],
    index_synced: false,
    gate_eligible: false,
    gate_eligible_reason: "",
    restored_from: null,
    gate_outcome: null,
    next: { actor: "you", do: "Open a session to start writing." },
    ...overrides,
  };
}

const NOT_STARTED = baseSessionStatus();

const WAITING_ON_CLIENT = baseSessionStatus({
  state: "waiting_on_client",
  next: { actor: "claude", do: "Claude should write the pages next." },
});

const CLIENT_WROTE = baseSessionStatus({
  state: "client_wrote",
  source_present: true,
  pages_present: ["attention-is-all-you-need.md"],
  next: { actor: "you", do: "Review and submit — this runs the gate." },
});

const REWORK_IN_FLIGHT = baseSessionStatus({
  state: "rework_in_flight",
  restored_from: "loop/x/rag-patterns/source-9f21ab3c",
  next: { actor: "claude", do: "Claude is reworking the refused candidate." },
});

const SUBMITTED = baseSessionStatus({
  state: "submitted",
  source_present: true,
  pages_present: ["attention-is-all-you-need.md"],
  next: { actor: "system", do: "The gate is evaluating your candidate." },
});

const MERGED = baseSessionStatus({
  state: "merged",
  source_present: true,
  pages_present: ["attention-is-all-you-need.md"],
  index_synced: true,
  gate_outcome: { verdict: "merged", scalar: 0.81, baseline_scalar: 0.71 },
  next: { actor: "none", do: "Merged and closed." },
});

const REFUSED = baseSessionStatus({
  state: "refused",
  source_present: true,
  pages_present: ["attention-is-all-you-need.md"],
  gate_outcome: {
    verdict: "refused",
    scalar: 0.62,
    baseline_scalar: 0.71,
    reason: "candidate dilutes 3 questions in the golden set",
  },
  next: { actor: "you", do: "The gate refused this candidate — rework it." },
});

const BLOCKED = baseSessionStatus({
  state: "blocked",
  gate_eligible: false,
  gate_eligible_reason: "no frozen baseline",
  next: {
    actor: "you",
    do: "No frozen baseline exists yet — freeze one in improve · instrument to unblock the gate.",
  },
});

const SWEPT = baseSessionStatus({
  state: "swept",
  next: {
    actor: "you",
    do: "The session expired after 24 hours — reopen to restart.",
  },
});

const YOU_ACTOR_STATES: Array<[string, SessionStatus]> = [
  ["not_started", NOT_STARTED],
  ["client_wrote", CLIENT_WROTE],
  ["refused", REFUSED],
  ["blocked", BLOCKED],
  ["swept", SWEPT],
];

const ALL_NINE_STATES: Array<[string, SessionStatus]> = [
  ["not_started", NOT_STARTED],
  ["waiting_on_client", WAITING_ON_CLIENT],
  ["client_wrote", CLIENT_WROTE],
  ["rework_in_flight", REWORK_IN_FLIGHT],
  ["submitted", SUBMITTED],
  ["merged", MERGED],
  ["refused", REFUSED],
  ["blocked", BLOCKED],
  ["swept", SWEPT],
];

/**
 * Boundary fake of `ToolClient` (`dashboard/CLAUDE.md`: "the single seam for
 * MCP calls") -- only the three methods plus the two capability fields
 * `HandoffStage` reaches. `responses` is consumed one at a time by
 * `sessionStatus`, then held on its last entry -- lets a test script a
 * state transition across successive poll ticks.
 */
function buildRawClient(
  overrides: {
    responses?: SessionStatus[];
    hostCapabilities?: HostCapabilities;
    mount?: Mount;
  } = {},
) {
  const responses = [...(overrides.responses ?? [NOT_STARTED])];
  const sessionStatus = vi.fn(async (..._args: unknown[]) =>
    responses.length > 1 ? responses.shift()! : responses[0],
  );
  const sendMessage = vi.fn(async (..._args: unknown[]) => undefined);
  const updateModelContext = vi.fn(async (..._args: unknown[]) => undefined);
  return {
    sessionStatus,
    sendMessage,
    updateModelContext,
    hostCapabilities: overrides.hostCapabilities ?? {},
    mount: overrides.mount ?? ("bridge" as Mount),
  };
}

function fakeClient(
  overrides: {
    responses?: SessionStatus[];
    hostCapabilities?: HostCapabilities;
    mount?: Mount;
  } = {},
) {
  const raw = buildRawClient(overrides);
  return { client: raw as unknown as ToolClient, ...raw };
}

function fakeYouControl() {
  return vi.fn((status: SessionStatus) => (
    <button
      type="button"
      data-testid="you-control"
    >{`you-control:${status.state}`}</button>
  ));
}

function renderHandoffStage(
  client: ToolClient,
  overrides: {
    active?: boolean;
    renderYouControl?: ReturnType<typeof fakeYouControl>;
  } = {},
) {
  const renderYouControl = overrides.renderYouControl ?? fakeYouControl();
  const utils = render(
    <HandoffStage
      client={client}
      topic={TOPIC}
      suggestionId={SUGGESTION_ID}
      vault={VAULT}
      command={COMMAND}
      ask={ASK}
      active={overrides.active ?? true}
      renderYouControl={renderYouControl}
    />,
  );
  return { ...utils, renderYouControl };
}

function dispatchControlPresent(): boolean {
  return (
    screen.queryByText(
      /send to claude|queue for claude|copy the instruction/i,
    ) !== null
  );
}

function youControlPresent(): HTMLElement | null {
  return screen.queryByTestId("you-control");
}

// ---------------------------------------------------------------------------
// The nine states: next.actor drives which affordance shows
// ---------------------------------------------------------------------------

describe("the nine states of INTERFACE_DESIGN.md §3.3 — next.actor drives the affordance", () => {
  describe("next.actor: claude — the dispatch control shows, never the in-lane control", () => {
    it("waiting_on_client shows the dispatch control and never calls renderYouControl", async () => {
      const { client } = fakeClient({ responses: [WAITING_ON_CLIENT] });
      const { renderYouControl } = renderHandoffStage(client);
      await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

      expect(renderYouControl).not.toHaveBeenCalled();
    });

    it("rework_in_flight shows the dispatch control labelled for rework", async () => {
      const { client } = fakeClient({ responses: [REWORK_IN_FLIGHT] });
      renderHandoffStage(client);
      await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

      expect(screen.getByText(/rework/i)).toBeTruthy();
    });
  });

  describe("next.actor: you — the embedding stage's own control shows, via the render prop", () => {
    it.each(YOU_ACTOR_STATES)(
      "%s calls renderYouControl with the live status and never shows the dispatch control",
      async (_label, status) => {
        const { client } = fakeClient({ responses: [status] });
        const { renderYouControl } = renderHandoffStage(client);
        await vi.waitFor(() => expect(renderYouControl).toHaveBeenCalled());

        // The second argument is the dispatch context: a you-state may now
        // offer the same dispatch affordance, since the user clicking the
        // in-lane control *is* the `you` actor taking their turn.
        expect(renderYouControl).toHaveBeenCalledWith(
          expect.objectContaining({ state: status.state }),
          expect.anything(),
        );
        expect(dispatchControlPresent()).toBe(false);
        expect(youControlPresent()?.textContent).toBe(
          `you-control:${status.state}`,
        );
      },
    );

    it("refused additionally surfaces the gate's dilution reason", async () => {
      const { client } = fakeClient({ responses: [REFUSED] });
      renderHandoffStage(client);
      await vi.waitFor(() =>
        expect(screen.getByTestId("you-control")).toBeTruthy(),
      );

      expect(
        screen.getByText(/candidate dilutes 3 questions in the golden set/i),
      ).toBeTruthy();
    });
  });

  describe("next.actor: system — status text only, no control at all", () => {
    it("submitted renders status text and calls neither affordance", async () => {
      const { client } = fakeClient({ responses: [SUBMITTED] });
      const { renderYouControl } = renderHandoffStage(client);
      await vi.waitFor(() =>
        expect(screen.getByText(/gate is evaluating/i)).toBeTruthy(),
      );

      expect(dispatchControlPresent()).toBe(false);
      expect(renderYouControl).not.toHaveBeenCalled();
    });
  });

  describe("next.actor: none — terminal, no control at all", () => {
    it("merged renders terminal status and calls neither affordance", async () => {
      const { client } = fakeClient({ responses: [MERGED] });
      const { renderYouControl } = renderHandoffStage(client);
      await vi.waitFor(() =>
        expect(screen.getByText(/merged and closed/i)).toBeTruthy(),
      );

      expect(dispatchControlPresent()).toBe(false);
      expect(renderYouControl).not.toHaveBeenCalled();
    });
  });
});

describe("next.do narrates every one of the nine states — the anti-dead-end guarantee", () => {
  it.each(ALL_NINE_STATES)(
    "%s renders its own next.do sentence verbatim",
    async (_label, status) => {
      const { client } = fakeClient({ responses: [status] });
      renderHandoffStage(client);

      await vi.waitFor(() =>
        expect(screen.getByText(status.next.do)).toBeTruthy(),
      );
    },
  );
});

// ---------------------------------------------------------------------------
// Dispatch: four tiers, honest labels, the copyable floor always ships
// ---------------------------------------------------------------------------

describe("dispatch: four tiers, honest labels, zero hard dependency (INTERFACE_DESIGN.md §3.4)", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn() },
      configurable: true,
    });
  });

  it("tier A — bridge + message: 'Send to Claude' starts a turn via sendMessage", async () => {
    const { client, sendMessage } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: { message: {} },
      mount: "bridge",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    expect(screen.getByText(/may ask you to confirm/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /send to claude/i }));

    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
  });

  it("tier B — bridge + updateModelContext only: 'Queue for Claude' never starts a turn", async () => {
    const { client, updateModelContext, sendMessage } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: { updateModelContext: {} },
      mount: "bridge",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    expect(screen.getByText(/does not start a turn/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /queue for claude/i }));

    await vi.waitFor(() => expect(updateModelContext).toHaveBeenCalledTimes(1));
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("tier C — bridge, neither capability: only the copy affordance, nothing programmatic", async () => {
    const { client, sendMessage, updateModelContext } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: {},
      mount: "bridge",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    expect(
      screen.queryByRole("button", { name: /send to claude/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /queue for claude/i }),
    ).toBeNull();
    expect(screen.getByText(/copy the instruction/i)).toBeTruthy();
    expect(sendMessage).not.toHaveBeenCalled();
    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it("tier D — HTTP mount: identical to C regardless of any advertised capabilities", async () => {
    const { client } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: { message: {}, updateModelContext: {} },
      mount: "http",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    expect(
      screen.queryByRole("button", { name: /send to claude/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /queue for claude/i }),
    ).toBeNull();
    expect(screen.getByText(/copy the instruction/i)).toBeTruthy();
  });

  it.each<[string, HostCapabilities, Mount]>([
    ["A", { message: {} }, "bridge"],
    ["B", { updateModelContext: {} }, "bridge"],
    ["C", {}, "bridge"],
    ["D", { message: {}, updateModelContext: {} }, "http"],
  ])(
    "tier %s always renders the literal dispatch invocation — a user whose host silently drops the request is never stranded",
    async (_tier, hostCapabilities, mount) => {
      const { client } = fakeClient({
        responses: [WAITING_ON_CLIENT],
        hostCapabilities,
        mount,
      });
      renderHandoffStage(client);
      await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

      const rendered = document.body.textContent ?? "";
      expect(rendered).toContain(ASK);
      expect(rendered).toMatch(DISPATCH_LINE_PATTERN);
    },
  );
});

// ---------------------------------------------------------------------------
// A successful dispatch confirms itself at every tier — the uncontrolled
// `next.actor === "claude"` mount included, where nothing is lifted to a
// parent to hold the flag.
// ---------------------------------------------------------------------------

describe("a dispatch that succeeded says so, wherever the panel is mounted", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(async () => undefined) },
      configurable: true,
    });
  });

  it("tier A — the send is confirmed and the button is gone, so a second click cannot double-send", async () => {
    const { client, sendMessage } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: { message: {} },
      mount: "bridge",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: /send to claude/i }));

    expect(
      await screen.findByText(/sent to your claude session/i),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /send to claude/i }),
    ).toBeNull();
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });

  it("tier D — a successful copy IS the dispatch, and says only what it can see", async () => {
    const { client } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: { message: {}, updateModelContext: {} },
      mount: "http",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    fireEvent.click(
      screen.getByRole("button", { name: /copy the instruction/i }),
    );

    // "Copied", never "Sent": nothing was dispatched — the user holds the
    // text. The copy affordance itself stays, so a re-copy is still possible.
    expect(await screen.findByText(/^copied\.$/i)).toBeTruthy();
    expect(screen.queryByText(/sent to your claude session/i)).toBeNull();
    expect(
      screen.getByRole("button", { name: /copy the instruction/i }),
    ).toBeTruthy();
  });

  it("tier C — a copy that the host rejected claims nothing", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: vi.fn(async () => {
          throw new Error("denied");
        }),
      },
      configurable: true,
    });
    const { client } = fakeClient({
      responses: [WAITING_ON_CLIENT],
      hostCapabilities: {},
      mount: "bridge",
    });
    renderHandoffStage(client);
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));

    fireEvent.click(
      screen.getByRole("button", { name: /copy the instruction/i }),
    );

    expect(await screen.findByText(/copy failed/i)).toBeTruthy();
    expect(screen.queryByText(/^copied\.$/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Conditional polling discipline (dec-091 clause 2, the process-state lens's
// cost budget) — only the active item polls, only at 3 s.
// ---------------------------------------------------------------------------

describe("conditional polling discipline — only the active item polls, only at 3 s", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("active=false never grows the call count across a 3 s tick", async () => {
    const { client, sessionStatus } = fakeClient({
      responses: [WAITING_ON_CLIENT],
    });
    renderHandoffStage(client, { active: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const callsBeforeTick = sessionStatus.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(sessionStatus.mock.calls.length).toBe(callsBeforeTick);
  });

  it("active=true polls once immediately, then exactly once per 3 s tick", async () => {
    const { client, sessionStatus } = fakeClient({
      responses: [WAITING_ON_CLIENT, WAITING_ON_CLIENT, WAITING_ON_CLIENT],
    });
    renderHandoffStage(client, { active: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(sessionStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(sessionStatus).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(sessionStatus).toHaveBeenCalledTimes(3);
  });

  it("resumes on write-back — a fresh watch payload advances the rendered state", async () => {
    const { client } = fakeClient({
      responses: [WAITING_ON_CLIENT, CLIENT_WROTE],
    });
    const { renderYouControl } = renderHandoffStage(client, { active: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(dispatchControlPresent()).toBe(true);
    expect(renderYouControl).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(dispatchControlPresent()).toBe(false);
    expect(renderYouControl).toHaveBeenCalledWith(
      expect.objectContaining({ state: "client_wrote" }),
      expect.anything(),
    );
  });
});

// ---------------------------------------------------------------------------
// Observation-only — the stage never executes a vault write itself
// ---------------------------------------------------------------------------

describe("observation-only — the stage never reaches a vault-write tool (client-as-brain)", () => {
  it("touches nothing on the client beyond session_status, sendMessage, updateModelContext, hostCapabilities and mount", async () => {
    const allowed = new Set([
      "sessionStatus",
      "sendMessage",
      "updateModelContext",
      "hostCapabilities",
      "mount",
    ]);
    const raw = buildRawClient({
      responses: [WAITING_ON_CLIENT, CLIENT_WROTE],
      hostCapabilities: { message: {} },
    });
    const guarded = new Proxy(raw, {
      get(target, prop, receiver) {
        if (typeof prop === "string" && !allowed.has(prop)) {
          throw new Error(
            `HandoffStage touched client.${prop} -- observation-only, per dec-091: it must ` +
              "never reach a vault-write tool directly, only the client-as-brain does that.",
          );
        }
        return Reflect.get(target, prop, receiver);
      },
    }) as unknown as ToolClient;

    const renderYouControl = fakeYouControl();
    render(
      <HandoffStage
        client={guarded}
        topic={TOPIC}
        suggestionId={SUGGESTION_ID}
        vault={VAULT}
        command={COMMAND}
        ask={ASK}
        active={true}
        renderYouControl={renderYouControl}
      />,
    );
    await vi.waitFor(() => expect(dispatchControlPresent()).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /send to claude/i }));
    await vi.waitFor(() => expect(raw.sendMessage).toHaveBeenCalledTimes(1));

    expect(raw.sessionStatus.mock.calls.length).toBeGreaterThan(0);
  });
});
