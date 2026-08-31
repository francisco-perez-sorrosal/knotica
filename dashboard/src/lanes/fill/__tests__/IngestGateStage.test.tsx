import {
  act,
  cleanup,
  fireEvent,
  render,
  within,
} from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ToolClient } from "../../../toolClient";
import type {
  GateOutcomeVerdict,
  LaneRailStageStatus,
  SessionStatus,
  SuggestionRecord,
  SuggestionsReadResult,
  WikiStatus,
} from "../../../types";

/**
 * `dashboard/src/lanes/fill/IngestGateStage.tsx` does not exist yet -- this is
 * the RED half of a paired implementation/test step (`IMPLEMENTATION_PLAN.md`
 * Steps 98/99, `INTERFACE_DESIGN.md §2.5`/`§3.3`, `dec-087`, `dec-091`). This
 * suite spawns *before* Step 98's implementer, per the RED-handshake fix: the
 * standalone run below must fail at collection with a missing-module error,
 * which the paired implementation step is gated on.
 *
 * Loaded through a non-literal dynamic `import()` -- the same device
 * `QueueStage.test.tsx` (Step 97) and `HandoffStage.test.tsx` (Step 88) used
 * for their own not-yet-existing modules: a literal
 * `import { IngestGateStage } from "../IngestGateStage"` would fail
 * `tsc --noEmit` for the whole project the moment this file lands.
 *
 * `HandoffStage` itself is **not** RED here -- Steps 87/88 already landed it
 * (`dashboard/src/lanes/HandoffStage.tsx`), so it is imported directly (not
 * dynamically) and this suite drives the real component through
 * `IngestGateStage`'s embedding, exactly as `dec-091`'s "one shared shell"
 * intent requires: a passing assertion here proves the embedding is real,
 * not a reimplementation of the nine-state contract.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (full reasoning in `LEARNINGS_test-engineer_step99.md`; the paired
 * implementation wins on conflict):
 *
 *   1. `<IngestGateStage client={...} topic={...} vault={...} status={...}
 *      onStatusRefresh={...} />` -- the same five props `QueueStage.tsx`
 *      already takes (Step 96), since both are Fill's rail-stage components
 *      assembled by the same `FillLane.tsx` (Step 100).
 *   2. The component self-fetches `client.suggestionsRead(topic, "approved",
 *      cursor, limit, vault)` on mount -- `approved` suggestions are exactly
 *      the ones in flight through `ingest`/`gate` (`pending` ones are
 *      `QueueStage`'s `approve` stage's job; this file makes no second call
 *      for `pending`).
 *   3. Renders exactly two `.lane-stage` elements, ids `ingest` then `gate`,
 *      in that order -- `INTERFACE_DESIGN.md §2.5`'s numbered rail (④⑤).
 *      `data-state` is read from `status.topics[].lanes.fill` by id,
 *      defaulting to `"pending"`, mirroring `QueueStage`'s own convention.
 *   4. Each approved suggestion renders as a collapsed row inside the
 *      `ingest` stage with an expand affordance named after the candidate's
 *      title; exactly one suggestion's rail can be expanded at a time
 *      (`ingest`'s new "singleton" state Step 98 introduces) -- expanding a
 *      second item collapses the first, unmounting its `HandoffStage`
 *      instance entirely (not merely pausing its poll), so at most one
 *      `.handoff-stage` node ever exists in the DOM.
 *   5. The expanded suggestion's rail is
 *      `<HandoffStage client vault topic suggestionId command="fill" ask={...}
 *      active={true} renderYouControl={...} />` -- `command="fill"` because
 *      `commands/fill.md` (not `commands/ingest.md`) is the dispatch target
 *      named by `INTERFACE_DESIGN.md §2.5`'s "`ingest` embeds
 *      `<HandoffStage command="fill" .../>`" instruction.
 *   6. `renderYouControl` supplies one labelled button per `next.actor
 *      === "you"` state and calls no client method itself (`not_started` ->
 *      "Open a session", `client_wrote` -> "Submit", `refused` -> "Rework
 *      it", `swept` -> "Reopen") -- consistent with "the stage itself
 *      executes no vault writes" (client-as-brain: only Claude, via the
 *      dispatched command, writes to the vault). `blocked` renders through
 *      `HandoffStage`'s own three-part what/why/fix `next.do` text with no
 *      separate button, since there is nothing an in-lane click could do
 *      about a missing baseline.
 *   7. The `gate` stage is a **read-only, zero-call** projection of the
 *      expanded suggestion's own `gate_outcome` field (already present from
 *      the `suggestionsRead` call in assumption 2 -- `dec-087` clause 1's
 *      "gate renders gate_outcome already present... zero new calls"): null
 *      -> "not yet gated" placeholder; `merged` -> a merged/closed indication
 *      naming that the originating gap is now resolved (`dec-087`'s gate-
 *      closes-the-gap decision, superseding the suggestion-keyed workaround
 *      `INTERFACE_DESIGN.md §2.5`'s N6 finding flagged); `refused` -> the
 *      dilution reason plus a `[Rework it]` affordance. No new suggestion is
 *      selected to view `gate` independently of `ingest` -- both stages
 *      always describe the same singleton-expanded item (Step 98: "exactly
 *      one suggestion's rail is open at a time").
 *   8. `[Rework it]` declares no cross-lane navigation prop (`onOpen*`-shaped)
 *      anywhere in `IngestGateStage.tsx` -- extends Step 80's cross-lane-prop
 *      census technique to this file, which Step 80's own census excluded
 *      (it scoped out everything under `dashboard/src/lanes/`).
 *
 * Not tested here (other steps' job): `QueueStage`'s `gap`/`discover`/
 * `approve` stages (Step 97), the assembled five-stage `FillLane` rail and
 * cross-stage selection wiring (Step 101), `HandoffStage`'s own nine-state/
 * dispatch-tier contract (already covered by `HandoffStage.test.tsx`).
 */

interface IngestGateStageProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status?: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}

type IngestGateStageComponent = (props: IngestGateStageProps) => JSX.Element;

interface IngestGateStageModule {
  IngestGateStage: IngestGateStageComponent;
}

const INGEST_GATE_STAGE_MODULE_PATH = "../IngestGateStage";

let IngestGateStage: IngestGateStageComponent;

beforeAll(async () => {
  ({ IngestGateStage } = (await import(
    INGEST_GATE_STAGE_MODULE_PATH
  )) as IngestGateStageModule);
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

const INGEST = 0;
const GATE = 1;

function stageNodes(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

function baseCandidate(
  overrides: Partial<SuggestionRecord["candidate"]> = {},
): SuggestionRecord["candidate"] {
  return {
    url: "https://arxiv.org/abs/2212.10496",
    title: "Precise Zero-Shot Dense Retrieval without Relevance Labels",
    snippet: "",
    source_provider: "you.com",
    authors: ["Gao et al."],
    venue: "arXiv",
    published_date: "2022-12-20",
    doi: null,
    citation_count: 42,
    is_open_access: true,
    fwci: null,
    provider_score: 0.91,
    reputability: {
      tier: "preprint_known_lab",
      score: 0.7,
      signals: ["known lab"],
    },
    schema_version: 1,
    ...overrides,
  };
}

function baseSuggestion(
  overrides: Partial<SuggestionRecord> = {},
): SuggestionRecord {
  return {
    schema_version: 1,
    suggestion_id: "s_1a2b3c4d",
    topic: TOPIC,
    gap_id: "g_7c21",
    qa_id: "qa_1",
    fault_class: "genuine_gap",
    question: "no coverage of HyDE",
    reference_pages: [],
    rank: 1,
    query_text: "HyDE dense retrieval",
    candidate: baseCandidate(),
    status: "approved",
    proposed_at: "2026-08-20T00:00:00Z",
    decided_at: "2026-08-21T00:00:00Z",
    decided_reason: null,
    ingested_at: null,
    detected_generation: 3,
    gap_origin: "measured",
    gate_outcome: null,
    ...overrides,
  };
}

const SUGGESTION_A = baseSuggestion({ suggestion_id: "s_1a2b3c4d" });
const SUGGESTION_B = baseSuggestion({
  suggestion_id: "s_9f8e7d6c",
  candidate: baseCandidate({
    title: "Dense Passage Retrieval for Open-Domain QA",
  }),
});

function suggestionsResult(
  suggestions: SuggestionRecord[],
  overrides: Partial<SuggestionsReadResult> = {},
): SuggestionsReadResult {
  return {
    topic: TOPIC,
    status_filter: "approved",
    suggestions,
    status_counts: {
      pending: 0,
      approved: suggestions.length,
      rejected: 0,
      deferred: 0,
      ingested: 0,
    },
    next_cursor: "",
    has_more: false,
    total_count: suggestions.length,
    skipped_malformed: 0,
    ...overrides,
  };
}

function baseStatus(lanesFill?: LaneRailStageStatus[]): WikiStatus {
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
        curated: 8,
        to_compile_ready: 0,
        lint_violations: 0,
        last_eval: null,
        suggestions: {
          pending: 0,
          approved_awaiting_ingest: 1,
          deferred: 0,
          rejected: 0,
          ingested: 0,
          newest_proposed_at: null,
          refused_awaiting_rework: 0,
        },
        lanes: lanesFill ? { fill: lanesFill } : undefined,
      },
    ],
    totals: { topics: 1, pages: 10, curated: 8, lint_violations: 0 },
  } as unknown as WikiStatus;
}

function sessionStatus(
  suggestionId: string,
  overrides: Partial<SessionStatus> = {},
): SessionStatus {
  return {
    suggestion_id: suggestionId,
    stage: "ingest",
    stage_index: 4,
    state: "not_started",
    source_present: false,
    pages_present: [],
    index_synced: false,
    gate_eligible: true,
    gate_eligible_reason: "",
    restored_from: null,
    gate_outcome: null,
    next: { actor: "you", do: "Open a session to start writing." },
    ...overrides,
  };
}

const NOT_STARTED = (id: string) => sessionStatus(id);

const WAITING_ON_CLIENT = (id: string) =>
  sessionStatus(id, {
    state: "waiting_on_client",
    next: { actor: "claude", do: "Claude should write the pages next." },
  });

const CLIENT_WROTE = (id: string) =>
  sessionStatus(id, {
    state: "client_wrote",
    source_present: true,
    pages_present: ["attention-is-all-you-need.md"],
    next: { actor: "you", do: "Review and submit — this runs the gate." },
  });

const REWORK_IN_FLIGHT = (id: string) =>
  sessionStatus(id, {
    state: "rework_in_flight",
    restored_from: "loop/x/rag-patterns/source-9f21ab3c",
    next: { actor: "claude", do: "Claude is reworking the refused candidate." },
  });

const SUBMITTED = (id: string) =>
  sessionStatus(id, {
    state: "submitted",
    source_present: true,
    pages_present: ["attention-is-all-you-need.md"],
    next: { actor: "system", do: "The gate is evaluating your candidate." },
  });

const MERGED = (id: string) =>
  sessionStatus(id, {
    state: "merged",
    source_present: true,
    pages_present: ["attention-is-all-you-need.md"],
    index_synced: true,
    gate_outcome: { verdict: "merged", scalar: 0.81, baseline_scalar: 0.71 },
    next: { actor: "none", do: "Merged and closed." },
  });

const REFUSED = (id: string) =>
  sessionStatus(id, {
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

const BLOCKED = (id: string) =>
  sessionStatus(id, {
    state: "blocked",
    gate_eligible: false,
    gate_eligible_reason: "no frozen baseline",
    next: {
      actor: "you",
      do: "No frozen baseline exists yet — freeze one in improve · instrument to unblock the gate.",
    },
  });

const SWEPT = (id: string) =>
  sessionStatus(id, {
    state: "swept",
    next: {
      actor: "you",
      do: "The session expired after 24 hours — reopen to restart.",
    },
  });

const ALL_NINE_STATES: Array<[string, (id: string) => SessionStatus]> = [
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
 * MCP calls"). `sessionStatus` is keyed by suggestion id and consumed one
 * response at a time per id, then held on its last entry -- lets a test
 * script a state transition across successive poll ticks for one item while
 * another item's queue is untouched.
 */
function fakeClient(
  overrides: {
    suggestions?: SuggestionRecord[];
    sessionResponses?: Record<string, SessionStatus[]>;
  } = {},
) {
  const responsesById = new Map<string, SessionStatus[]>(
    Object.entries(
      overrides.sessionResponses ?? {
        [SUGGESTION_A.suggestion_id]: [NOT_STARTED(SUGGESTION_A.suggestion_id)],
      },
    ).map(([id, responses]) => [id, [...responses]]),
  );
  const suggestionsRead = vi.fn(async (..._args: unknown[]) =>
    suggestionsResult(overrides.suggestions ?? [SUGGESTION_A]),
  );
  const sessionStatusFn = vi.fn(
    async (_topic: string, suggestionId: string, ..._rest: unknown[]) => {
      const queue = responsesById.get(suggestionId) ?? [
        NOT_STARTED(suggestionId),
      ];
      return queue.length > 1 ? queue.shift()! : queue[0];
    },
  );
  const sendMessage = vi.fn(async (..._args: unknown[]) => undefined);
  const updateModelContext = vi.fn(async (..._args: unknown[]) => undefined);
  const raw = {
    suggestionsRead,
    sessionStatus: sessionStatusFn,
    sendMessage,
    updateModelContext,
    hostCapabilities: {},
    mount: "bridge" as const,
  };
  return { client: raw as unknown as ToolClient, ...raw };
}

function renderIngestGateStage(
  client: ToolClient,
  overrides: Partial<IngestGateStageProps> = {},
): HTMLElement {
  // `render(...).container` is typed `Element` by the library but is always
  // a real `<div>` at runtime -- the same cast `HandoffStage.test.tsx` and
  // `QueueStage.test.tsx` avoid needing only because they never pass the
  // bare container into `within()` directly.
  return render(
    <IngestGateStage
      client={client}
      topic={TOPIC}
      vault={VAULT}
      status={overrides.status ?? null}
      onStatusRefresh={overrides.onStatusRefresh}
    />,
  ).container as HTMLElement;
}

function expandButtonFor(container: HTMLElement, title: string): HTMLElement {
  return within(stageNodes(container)[INGEST]).getByRole("button", {
    name: new RegExp(title, "i"),
  });
}

/**
 * `suggestionsRead` being *called* (a synchronous mock-call fact, true the
 * instant the effect fires) and the resulting row actually landing in the
 * DOM (a state-update-then-rerender fact, at least one microtask later) are
 * two different moments -- `vi.waitFor`'s callback resolves the instant it
 * stops throwing, so a bare "has been called" check can win the race and
 * return before Preact's own re-render commits. Query-mechanism-only fix
 * (declared adjustment, `LEARNINGS_implementer_step98.md`): every
 * `expandButtonFor` call site waits on this DOM fact instead of (or in
 * addition to) the mock-call fact, mirroring `QueueStage.test.tsx`'s own
 * post-GREEN `find*` conversion for the identical race.
 */
function ingestRowsRendered(container: HTMLElement): boolean {
  return (
    within(stageNodes(container)[INGEST]).queryAllByRole("button").length > 0
  );
}

// ---------------------------------------------------------------------------
// Structure: two rail stages, in order, states from the server-derived rail
// ---------------------------------------------------------------------------

describe("the two rail stages", () => {
  it("renders exactly the ingest/gate stages, in that order", async () => {
    const { client, suggestionsRead } = fakeClient();
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());

    const nodes = stageNodes(container);
    expect(nodes).toHaveLength(2);
    expect(nodes[INGEST].textContent ?? "").toMatch(/ingest/i);
    expect(nodes[GATE].textContent ?? "").toMatch(/gate/i);
  });

  it("defaults both stages to 'pending' when no status/lanes block is supplied", async () => {
    const { client, suggestionsRead } = fakeClient();
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());

    expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
      "pending",
      "pending",
    ]);
  });

  it("propagates each declared stage's state from status.topics[].lanes.fill onto its own row", async () => {
    const declared: LaneRailStageStatus[] = [
      { id: "ingest", state: "active", reason: null },
      { id: "gate", state: "blocked", reason: "awaiting rework" },
    ];
    const { client, suggestionsRead } = fakeClient();
    const container = renderIngestGateStage(client, {
      status: baseStatus(declared),
    });
    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());

    expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
      "active",
      "blocked",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Fetch: approved suggestions, not the pending queue QueueStage already owns
// ---------------------------------------------------------------------------

describe("loading the ingest+gate queue", () => {
  it("loads approved suggestions for the topic on mount -- not the pending queue", async () => {
    const { client, suggestionsRead } = fakeClient();
    renderIngestGateStage(client);

    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());
    expect(suggestionsRead.mock.calls[0]).toContain(TOPIC);
    expect(suggestionsRead.mock.calls[0]).toContain("approved");
  });

  it("lists every approved suggestion's title as a collapsed row before anything is expanded", async () => {
    const { client } = fakeClient({
      suggestions: [SUGGESTION_A, SUGGESTION_B],
    });
    const container = renderIngestGateStage(client);

    await vi.waitFor(() =>
      expect(
        within(stageNodes(container)[INGEST]).getByText(
          SUGGESTION_A.candidate.title,
        ),
      ).toBeTruthy(),
    );
    expect(
      within(stageNodes(container)[INGEST]).getByText(
        SUGGESTION_B.candidate.title,
      ),
    ).toBeTruthy();
    // Nothing is expanded yet -- no handoff rail exists.
    expect(container.querySelector(".handoff-stage")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Singleton expansion -- Step 99's central structural assertion
// ---------------------------------------------------------------------------

describe("singleton expansion -- exactly one suggestion's rail is open at a time", () => {
  it("expanding a suggestion mounts its HandoffStage rail beneath the ingest stage", async () => {
    const { client } = fakeClient({ suggestions: [SUGGESTION_A] });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });

    fireEvent.click(expandButtonFor(container, SUGGESTION_A.candidate.title));

    await vi.waitFor(() =>
      expect(container.querySelectorAll(".handoff-stage")).toHaveLength(1),
    );
  });

  it("expanding a second item collapses the first -- at most one rail is ever open", async () => {
    const { client, sessionStatus: sessionStatusFn } = fakeClient({
      suggestions: [SUGGESTION_A, SUGGESTION_B],
      sessionResponses: {
        [SUGGESTION_A.suggestion_id]: [NOT_STARTED(SUGGESTION_A.suggestion_id)],
        [SUGGESTION_B.suggestion_id]: [NOT_STARTED(SUGGESTION_B.suggestion_id)],
      },
    });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });

    fireEvent.click(expandButtonFor(container, SUGGESTION_A.candidate.title));
    await vi.waitFor(() =>
      expect(container.querySelectorAll(".handoff-stage")).toHaveLength(1),
    );
    const callsForAAfterFirstExpand = sessionStatusFn.mock.calls.filter(
      (call) => call[1] === SUGGESTION_A.suggestion_id,
    ).length;
    expect(callsForAAfterFirstExpand).toBeGreaterThan(0);

    fireEvent.click(expandButtonFor(container, SUGGESTION_B.candidate.title));
    await vi.waitFor(() =>
      expect(
        sessionStatusFn.mock.calls.filter(
          (call) => call[1] === SUGGESTION_B.suggestion_id,
        ).length,
      ).toBeGreaterThan(0),
    );

    // Still exactly one rail open, and it now belongs to B, not A.
    expect(container.querySelectorAll(".handoff-stage")).toHaveLength(1);
    const callsForAAfterSecondExpand = sessionStatusFn.mock.calls.filter(
      (call) => call[1] === SUGGESTION_A.suggestion_id,
    ).length;
    expect(callsForAAfterSecondExpand).toBe(callsForAAfterFirstExpand);
  });
});

// ---------------------------------------------------------------------------
// The nine states render through the *real*, already-landed HandoffStage
// ---------------------------------------------------------------------------

describe("the expanded item's rail renders through the real HandoffStage (INTERFACE_DESIGN.md §3.3)", () => {
  it.each(ALL_NINE_STATES)(
    "%s narrates its own next.do sentence, proving the embedding is real, not reimplemented",
    async (_label, statusFor) => {
      const status = statusFor(SUGGESTION_A.suggestion_id);
      const { client } = fakeClient({
        suggestions: [SUGGESTION_A],
        sessionResponses: { [SUGGESTION_A.suggestion_id]: [status] },
      });
      const container = renderIngestGateStage(client);
      await vi.waitFor(() => {
        expect(client.suggestionsRead).toHaveBeenCalled();
        expect(ingestRowsRendered(container)).toBe(true);
      });
      fireEvent.click(expandButtonFor(container, SUGGESTION_A.candidate.title));

      await vi.waitFor(() =>
        expect(within(container).getByText(status.next.do)).toBeTruthy(),
      );
    },
  );

  it("carries the literal /knotica:fill <suggestion-id> <topic> dispatch line for a claude-actor state", async () => {
    const { client } = fakeClient({
      suggestions: [SUGGESTION_A],
      sessionResponses: {
        [SUGGESTION_A.suggestion_id]: [
          WAITING_ON_CLIENT(SUGGESTION_A.suggestion_id),
        ],
      },
    });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });
    fireEvent.click(expandButtonFor(container, SUGGESTION_A.candidate.title));

    const dispatchPattern = new RegExp(
      `/knotica:fill\\s+${SUGGESTION_A.suggestion_id}\\s+${TOPIC}`,
    );
    await vi.waitFor(() =>
      expect(container.textContent ?? "").toMatch(dispatchPattern),
    );
  });
});

// ---------------------------------------------------------------------------
// The gate stage: read-only projection of the suggestion's own gate_outcome
// ---------------------------------------------------------------------------

describe("the gate stage projects the expanded suggestion's own gate_outcome -- zero new calls", () => {
  it("shows no verdict yet when the suggestion has not been gated", async () => {
    const notGated = baseSuggestion({ gate_outcome: null });
    const { client } = fakeClient({ suggestions: [notGated] });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });
    fireEvent.click(expandButtonFor(container, notGated.candidate.title));

    await vi.waitFor(() =>
      expect(
        within(stageNodes(container)[GATE]).getByText(/not.*gated|no gate/i),
      ).toBeTruthy(),
    );
  });

  it.each<GateOutcomeVerdict>(["merged", "refused"])(
    "surfaces a %s verdict from the suggestion record without any additional client call",
    async (verdict) => {
      const gated = baseSuggestion({
        gate_outcome: {
          verdict,
          scalar: verdict === "merged" ? 0.81 : 0.62,
          baseline_scalar: 0.71,
          ref: "loop/c/rag-patterns/source-9f21ab3c",
          reason:
            verdict === "refused"
              ? "candidate dilutes 3 questions in the golden set"
              : undefined,
        },
      });
      const { client, suggestionsRead } = fakeClient({ suggestions: [gated] });
      const container = renderIngestGateStage(client);
      await vi.waitFor(() => {
        expect(suggestionsRead).toHaveBeenCalled();
        expect(ingestRowsRendered(container)).toBe(true);
      });
      fireEvent.click(expandButtonFor(container, gated.candidate.title));

      await vi.waitFor(() =>
        expect(
          within(stageNodes(container)[GATE]).getByText(
            new RegExp(verdict, "i"),
          ),
        ).toBeTruthy(),
      );
      // "zero new calls" -- suggestionsRead was called exactly once, by the
      // initial queue load; rendering the gate verdict never re-reads it.
      expect(suggestionsRead).toHaveBeenCalledTimes(1);
    },
  );

  it("a merged verdict notes the originating gap is now resolved (dec-087)", async () => {
    const merged = baseSuggestion({
      gate_outcome: {
        verdict: "merged",
        scalar: 0.81,
        baseline_scalar: 0.71,
        ref: "ref-1",
      },
    });
    const { client } = fakeClient({ suggestions: [merged] });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });
    fireEvent.click(expandButtonFor(container, merged.candidate.title));

    await vi.waitFor(() =>
      expect(
        within(stageNodes(container)[GATE]).getByText(/resolved|closed/i),
      ).toBeTruthy(),
    );
  });

  it("a refused verdict surfaces the dilution reason and a Rework it affordance", async () => {
    const refused = baseSuggestion({
      gate_outcome: {
        verdict: "refused",
        scalar: 0.62,
        baseline_scalar: 0.71,
        ref: "ref-1",
        reason: "candidate dilutes 3 questions in the golden set",
      },
    });
    const { client } = fakeClient({ suggestions: [refused] });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });
    fireEvent.click(expandButtonFor(container, refused.candidate.title));

    await vi.waitFor(() =>
      expect(
        within(stageNodes(container)[GATE]).getByText(
          /dilutes 3 questions in the golden set/i,
        ),
      ).toBeTruthy(),
    );
    // The gate's own projection settles synchronously off already-loaded
    // suggestion data; the embedded `HandoffStage`'s live session poll is a
    // second, independent async source that needs its own wait.
    await vi.waitFor(() =>
      expect(
        within(container).getByRole("button", { name: /rework it/i }),
      ).toBeTruthy(),
    );
  });
});

// ---------------------------------------------------------------------------
// The rework path is in-lane -- no cross-lane navigation prop anywhere
// ---------------------------------------------------------------------------

describe("[Rework it] re-enters ingest in-lane -- no navigation-prop call (extends Step 80's census)", () => {
  it("IngestGateStage.tsx declares no onOpen*-shaped cross-lane navigation prop", async () => {
    // Non-literal specifiers -- `@types/node` is not a project dependency, so
    // a literal `import("fs")` fails `tsc --noEmit` even when the result is
    // cast; the same device `crossLaneLinkCensus.test.ts` (Step 80) uses.
    const FS_MODULE_NAME = "fs";
    const PATH_MODULE_NAME = "path";
    const URL_MODULE_NAME = "url";
    const fsModule = (await import(FS_MODULE_NAME)) as unknown as {
      readFileSync(path: string, encoding: string): string;
      existsSync(path: string): boolean;
    };
    const pathModule = (await import(PATH_MODULE_NAME)) as unknown as {
      dirname(path: string): string;
      join(...parts: string[]): string;
    };
    const urlModule = (await import(URL_MODULE_NAME)) as unknown as {
      fileURLToPath(url: string): string;
    };
    const testDir = pathModule.dirname(
      urlModule.fileURLToPath(import.meta.url),
    );
    const sourcePath = pathModule.join(testDir, "..", "IngestGateStage.tsx");

    expect(fsModule.existsSync(sourcePath)).toBe(true);
    const source = fsModule.readFileSync(sourcePath, "utf-8");
    expect(source).not.toMatch(/\bonOpen\w*\b/);
  });

  it("clicking Rework it never touches the client beyond a session_status re-read", async () => {
    const refused = baseSuggestion({
      gate_outcome: {
        verdict: "refused",
        scalar: 0.62,
        baseline_scalar: 0.71,
        ref: "ref-1",
        reason: "candidate dilutes 3 questions in the golden set",
      },
    });
    const { client, sendMessage, updateModelContext } = fakeClient({
      suggestions: [refused],
      sessionResponses: {
        [refused.suggestion_id]: [REFUSED(refused.suggestion_id)],
      },
    });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => {
      expect(client.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });
    fireEvent.click(expandButtonFor(container, refused.candidate.title));
    await vi.waitFor(() =>
      expect(
        within(container).getByRole("button", { name: /rework it/i }),
      ).toBeTruthy(),
    );

    fireEvent.click(
      within(container).getByRole("button", { name: /rework it/i }),
    );

    expect(sendMessage).not.toHaveBeenCalled();
    expect(updateModelContext).not.toHaveBeenCalled();
    // Still the same rail for the same item -- no lane switch, no unmount.
    expect(container.querySelectorAll(".handoff-stage")).toHaveLength(1);
  });

  it("once the client reworks, the next poll renders the dispatch control labelled for rework, in the same rail", async () => {
    vi.useFakeTimers();
    const refused = baseSuggestion({
      gate_outcome: {
        verdict: "refused",
        scalar: 0.62,
        baseline_scalar: 0.71,
        ref: "ref-1",
        reason: "candidate dilutes 3 questions in the golden set",
      },
    });
    const { client } = fakeClient({
      suggestions: [refused],
      sessionResponses: {
        [refused.suggestion_id]: [
          REFUSED(refused.suggestion_id),
          REWORK_IN_FLIGHT(refused.suggestion_id),
        ],
      },
    });
    const container = renderIngestGateStage(client);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(expandButtonFor(container, refused.candidate.title));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      within(container).getByRole("button", { name: /rework it/i }),
    ).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(container.querySelectorAll(".handoff-stage")).toHaveLength(1);
    expect(within(container).getByText(/rework/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Zero-Improve-calls -- dec-087 clause 1 + dec-091's cost-discipline clause
// ---------------------------------------------------------------------------

describe("the whole component touches nothing beyond suggestionsRead and HandoffStage's five allowed members", () => {
  it("guards every client access across mount, expand, and every you-actor control", async () => {
    const allowed = new Set([
      "suggestionsRead",
      "sessionStatus",
      "sendMessage",
      "updateModelContext",
      "hostCapabilities",
      "mount",
    ]);
    const offenders: string[] = [];
    const { client: raw } = fakeClient({
      suggestions: [SUGGESTION_A],
      sessionResponses: {
        [SUGGESTION_A.suggestion_id]: [
          NOT_STARTED(SUGGESTION_A.suggestion_id),
          CLIENT_WROTE(SUGGESTION_A.suggestion_id),
          REFUSED(SUGGESTION_A.suggestion_id),
        ],
      },
    });
    const guarded = new Proxy(raw as unknown as Record<string, unknown>, {
      get(target, prop, receiver) {
        if (typeof prop === "string" && !allowed.has(prop)) {
          offenders.push(prop);
        }
        return Reflect.get(target, prop, receiver);
      },
    }) as unknown as ToolClient;

    const container = renderIngestGateStage(guarded);
    await vi.waitFor(() => {
      expect(guarded.suggestionsRead).toHaveBeenCalled();
      expect(ingestRowsRendered(container)).toBe(true);
    });
    fireEvent.click(expandButtonFor(container, SUGGESTION_A.candidate.title));
    await vi.waitFor(() =>
      expect(container.querySelectorAll(".handoff-stage")).toHaveLength(1),
    );

    for (const button of within(container).queryAllByRole("button")) {
      fireEvent.click(button);
    }

    expect(offenders).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// The you-control's disclosure -- the four states a user actually stands on
// ---------------------------------------------------------------------------

/**
 * Before this panel existed, the four `next.actor === "you"` controls were
 * `<button type="button">` with no handler at all: visible, enabled,
 * focusable, and inert. Worse, `HandoffStage` gated its dispatch surface on
 * `next.actor === "claude"`, so on precisely the states a user stands on
 * there was nothing to reveal -- no command, no tier button, no copy.
 *
 * The click now opens an inline disclosure that (a) says why the work
 * happens in Claude rather than here, (b) offers the dispatch affordance the
 * host's tier supports, and (c) always shows the literal invocation. Two
 * deliberate clicks: open the panel, then dispatch. Nothing here dispatches
 * on the first click -- `[Rework it]`'s own no-client-call guarantee above
 * still holds.
 */

/** Promotes the default (tier-C) fake to tier A: a bridge host that can start a turn. */
function withMessageCapability(client: ToolClient): ToolClient {
  Object.assign(client as unknown as Record<string, unknown>, {
    hostCapabilities: { message: {} },
  });
  return client;
}

async function openPanelFor(
  container: HTMLElement,
  title: string,
  controlName: RegExp,
): Promise<HTMLElement> {
  fireEvent.click(expandButtonFor(container, title));
  const control = await vi.waitFor(() =>
    within(container).getByRole("button", { name: controlName }),
  );
  fireEvent.click(control);
  return control;
}

describe("the you-control opens an inline panel rather than doing nothing", () => {
  const YOU_STATES: Array<
    [string, (id: string) => SessionStatus, RegExp, RegExp]
  > = [
    [
      "not_started",
      NOT_STARTED,
      /^open a session$/i,
      /only claude can write into it/i,
    ],
    [
      "client_wrote",
      CLIENT_WROTE,
      /^submit$/i,
      /runs the preflight in your claude session/i,
    ],
    ["refused", REFUSED, /^rework it$/i, /reopens the quarantined session/i],
    ["swept", SWEPT, /^reopen$/i, /expired after 24 hours/i],
  ];

  it.each(YOU_STATES)(
    "%s: the control is a disclosure whose aria-controls resolves to the panel it opens",
    async (_label, state, controlName, narration) => {
      const suggestion = baseSuggestion();
      const { client } = fakeClient({
        suggestions: [suggestion],
        sessionResponses: {
          [suggestion.suggestion_id]: [state(suggestion.suggestion_id)],
        },
      });
      const container = renderIngestGateStage(client);
      await vi.waitFor(() => expect(ingestRowsRendered(container)).toBe(true));

      fireEvent.click(expandButtonFor(container, suggestion.candidate.title));
      const control = await vi.waitFor(() =>
        within(container).getByRole("button", { name: controlName }),
      );
      expect(control.getAttribute("aria-expanded")).toBe("false");

      fireEvent.click(control);

      expect(control.getAttribute("aria-expanded")).toBe("true");
      const panelId = control.getAttribute("aria-controls") ?? "";
      const panel = container.querySelector(`#${CSS.escape(panelId)}`);
      expect(panel).toBeTruthy();
      // The panel says why the work happens in Claude, and shows the one
      // command that serves all four entry points.
      expect(panel?.textContent ?? "").toMatch(narration);
      expect(panel?.textContent ?? "").toContain(
        `/knotica:fill ${suggestion.suggestion_id} ${TOPIC}`,
      );
    },
  );

  it("tier A: dispatch is a second, explicit click, and the sent state is stated", async () => {
    const suggestion = baseSuggestion();
    const { client, sendMessage } = fakeClient({
      suggestions: [suggestion],
      sessionResponses: {
        [suggestion.suggestion_id]: [NOT_STARTED(suggestion.suggestion_id)],
      },
    });
    const container = renderIngestGateStage(withMessageCapability(client));
    await vi.waitFor(() => expect(ingestRowsRendered(container)).toBe(true));

    await openPanelFor(
      container,
      suggestion.candidate.title,
      /^open a session$/i,
    );
    // Opening the panel is not dispatching.
    expect(sendMessage).not.toHaveBeenCalled();

    fireEvent.click(
      within(container).getByRole("button", { name: /send to claude/i }),
    );

    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(
      await within(container).findByText(/sent to your claude session/i),
    ).toBeTruthy();
  });

  it("tier C: no programmatic button, only the copy -- and nothing is claimed to have been sent", async () => {
    const suggestion = baseSuggestion();
    const { client, sendMessage, updateModelContext } = fakeClient({
      suggestions: [suggestion],
      sessionResponses: {
        [suggestion.suggestion_id]: [NOT_STARTED(suggestion.suggestion_id)],
      },
    });
    const container = renderIngestGateStage(client);
    await vi.waitFor(() => expect(ingestRowsRendered(container)).toBe(true));

    await openPanelFor(
      container,
      suggestion.candidate.title,
      /^open a session$/i,
    );

    expect(
      within(container).queryByRole("button", { name: /send to claude/i }),
    ).toBeNull();
    expect(within(container).getByText("Copy the instruction")).toBeTruthy();
    expect(
      within(container).queryByText(/sent to your claude session/i),
    ).toBeNull();
    expect(sendMessage).not.toHaveBeenCalled();
    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it("expanding a different suggestion resets both the disclosure and the sent state", async () => {
    const { client, sendMessage } = fakeClient({
      suggestions: [SUGGESTION_A, SUGGESTION_B],
      sessionResponses: {
        [SUGGESTION_A.suggestion_id]: [NOT_STARTED(SUGGESTION_A.suggestion_id)],
        [SUGGESTION_B.suggestion_id]: [NOT_STARTED(SUGGESTION_B.suggestion_id)],
      },
    });
    const container = renderIngestGateStage(withMessageCapability(client));
    await vi.waitFor(() => expect(ingestRowsRendered(container)).toBe(true));

    await openPanelFor(
      container,
      SUGGESTION_A.candidate.title,
      /^open a session$/i,
    );
    fireEvent.click(
      within(container).getByRole("button", { name: /send to claude/i }),
    );
    await vi.waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(
      await within(container).findByText(/sent to your claude session/i),
    ).toBeTruthy();

    fireEvent.click(expandButtonFor(container, SUGGESTION_B.candidate.title));

    const controlB = await vi.waitFor(() =>
      within(container).getByRole("button", { name: /^open a session$/i }),
    );
    // B's panel is closed, and A's confirmation does not bleed into it.
    expect(controlB.getAttribute("aria-expanded")).toBe("false");
    expect(
      within(container).queryByText(/sent to your claude session/i),
    ).toBeNull();
  });
});
