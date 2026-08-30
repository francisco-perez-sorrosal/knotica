import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { LANE_STAGES } from "../../../processModel";
import type { ToolClient } from "../../../toolClient";
import type {
  GapRecord,
  GapsReadResult,
  LaneRailStageStatus,
  SessionStatus,
  SuggestionRecord,
  SuggestionsReadResult,
  WikiStatus,
} from "../../../types";

/**
 * `dashboard/src/lanes/fill/FillLane.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step assembling `QueueStage.tsx`
 * (`gap`/`discover`/`approve`, Step 96) and `IngestGateStage.tsx`
 * (`ingest`/`gate`, Step 98) behind one shared rail, mirroring
 * `ImproveLane.tsx`'s "six stage bodies under one `<ol>`" shape
 * (`INTERFACE_DESIGN.md §1.2`/`§2.5`). Loaded through a non-literal dynamic
 * `import()` -- the same device `QueueStage.test.tsx`/`IngestGateStage.test.tsx`
 * used for their own not-yet-existing modules: a literal
 * `import { FillLane } from "../FillLane"` would fail `tsc --noEmit` for the
 * whole project the instant this file lands.
 *
 * This suite tests **assembly only** -- the facts that only exist once both
 * stage groups are mounted together. It deliberately does not re-litigate
 * either stage group's own internal behaviour (gap listing, the two-phase
 * discover flow, approve/reject, singleton ingest expansion, the nine
 * `HandoffStage` states) -- those are `QueueStage.test.tsx` (Step 97) and
 * `IngestGateStage.test.tsx` (Step 99)'s job, already green.
 *
 * Load-bearing assumptions (full reasoning in
 * `LEARNINGS_test-engineer_step101.md`; the paired implementation wins on
 * conflict):
 *
 *   1. `<FillLane client={...} topic={...} vault={...} status={...}
 *      onStatusRefresh={...} />` -- the same five props `QueueStage`/
 *      `IngestGateStage` already take (confirmed against both files' own
 *      signatures), and the exact shape the Step 91 census's own
 *      `<FillLane client={null} topic="t" vault="v" status={null} />` smoke
 *      fixture pins. No `obsidianCtx` (unlike `LearnLane`/`AnswerLane`) --
 *      neither absorbed component reads it.
 *   2. `FillLane` wraps both components' unwrapped `.lane-stage` rows in
 *      exactly one `<ol class="lane-rail" aria-label="fill stages">`, the
 *      same shell `TendLane.tsx`/`ImproveLane.tsx` render by hand -- it does
 *      not introduce a second nav layer or re-render either child's content.
 *   3. `client`/`topic`/`vault`/`status`/`onStatusRefresh` are passed through
 *      to both children **unmodified and identical** ("one data spine, not
 *      two independently-configured reads") -- `FillLane` itself makes no
 *      tool call.
 *
 * REGISTER OBJECTION -- `IMPLEMENTATION_PLAN.md`'s Step 100/101 prose asks
 * for "the expanded item selected in `QueueStage`'s queue ... threaded down
 * to `IngestGateStage` as the active item -- one selection state, not two
 * independently-tracked ones," and Step 101's own "Done when" names a
 * "single-selection invariant ... asserted end-to-end across both stage
 * groups." Read against the two components as actually landed, this is not
 * a wiring gap `FillLane` can close: `QueueStage`'s `approve` queue lists
 * `pending` suggestions (§`suggestionsRead(topic, "pending", ...)`) and
 * `IngestGateStage`'s `ingest` list is the disjoint `approved` set
 * (§`suggestionsRead(topic, "approved", ...)`) -- a suggestion is never a
 * member of both lists at once, so there is no single item that could ever
 * be "selected" in one queue and "opened" in the other. Neither component
 * exposes an `expandedId`/`onExpand` prop for a parent to lift anyway (both
 * self-manage their own expansion state internally, confirmed by reading
 * both files), and this step's `Files:` field is `FillLane.tsx` only, so
 * introducing such a prop is out of scope here regardless. The one real
 * "singleton rail" cardinality invariant in this lane (`INTERFACE_DESIGN.md
 * §1.4`) belongs to `IngestGateStage` alone (already characterized,
 * Step 99); `FillLane`'s assembly introduces no second one to desynchronize
 * against. This suite therefore does not assert cross-component selection
 * threading -- it asserts the composition facts that are actually testable
 * (one data spine, correct rail order/state, assembly does not break either
 * child's own interactivity) and records this gap for the implementer and
 * planner rather than encoding an assumption already falsified by the two
 * paired steps that landed first.
 *
 * Not tested here: the five-stage rail's internal position numbering.
 * `QueueStage`/`IngestGateStage` each number their own rows locally
 * (`gap`/`discover`/`approve` as 1/2/3; `ingest`/`gate` as 1/2) --
 * `FillLane` does not renumber them to a continuous 1-5, and cannot without
 * touching either child (out of this step's `Files:` scope). Pending-stage
 * glyph assertions below check *that* a numeral renders, never *which*
 * numeral, so this suite does not encode an assumption about a renumbering
 * this step is not positioned to make.
 */

interface FillLaneProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status?: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}

type FillLaneComponent = (props: FillLaneProps) => JSX.Element;

interface FillLaneModule {
  FillLane: FillLaneComponent;
}

const FILL_LANE_MODULE_PATH = "../FillLane";

let FillLane: FillLaneComponent;

beforeAll(async () => {
  ({ FillLane } = (await import(FILL_LANE_MODULE_PATH)) as FillLaneModule);
});

afterEach(cleanup);

const TOPIC = "rag-patterns";
const VAULT = "kb";

const [GAP, DISCOVER, APPROVE, INGEST, GATE] = [0, 1, 2, 3, 4];

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

function baseGap(overrides: Partial<GapRecord> = {}): GapRecord {
  return {
    gap_id: "g_7c21",
    topic: TOPIC,
    qa_id: "qa_1",
    fault_class: "genuine_gap",
    status: "open",
    detected_at: "2026-08-20T00:00:00Z",
    question: "no coverage of HyDE",
    reference_pages: [],
    reference_pages_exist: false,
    origin: "measured",
    reported_reason: null,
    detected_generation: 3,
    ...overrides,
  };
}

function baseGapsResult(
  overrides: Partial<GapsReadResult> = {},
): GapsReadResult {
  return {
    topic: TOPIC,
    status_filter: "open",
    gaps: [baseGap()],
    status_counts: { open: 1, resolved: 0, dismissed: 0 },
    origin_counts: { measured: 1, reported: 0, retracted: 0 },
    next_cursor: "",
    has_more: false,
    total_count: 1,
    skipped_malformed: 0,
    ...overrides,
  };
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
    proposed_at: "2026-08-25T00:00:00Z",
    decided_at: "2026-08-25T01:00:00Z",
    decided_reason: null,
    ingested_at: null,
    detected_generation: 3,
    gap_origin: "measured",
    gate_outcome: null,
    ...overrides,
  };
}

function suggestionsResult(
  statusFilter: "pending" | "approved",
  suggestions: SuggestionRecord[],
): SuggestionsReadResult {
  return {
    topic: TOPIC,
    status_filter: statusFilter,
    suggestions,
    status_counts: {
      pending: statusFilter === "pending" ? suggestions.length : 0,
      approved: statusFilter === "approved" ? suggestions.length : 0,
      rejected: 0,
      deferred: 0,
      ingested: 0,
    },
    next_cursor: "",
    has_more: false,
    total_count: suggestions.length,
    skipped_malformed: 0,
  };
}

const APPROVED_SUGGESTION = baseSuggestion({ suggestion_id: "s_1a2b3c4d" });

function notStartedSession(suggestionId: string): SessionStatus {
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
  };
}

/** Minimal `WikiStatus`, only the fields either child could plausibly read
 * (`topics[].lanes.fill`, `topics[].suggestions.refused_awaiting_rework`) --
 * matches `QueueStage.test.tsx`/`IngestGateStage.test.tsx`'s own cast
 * convention. */
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

/** Boundary fake of `ToolClient` combining both absorbed components' call
 * surfaces (`dashboard/CLAUDE.md`: "the single seam for MCP calls").
 * `suggestionsRead` dispatches on the `filter` argument, the same way the
 * two real components call it with two different literal filters -- this is
 * what lets one client fixture serve both stage groups without either
 * seeing the other's data. */
function fakeClient(
  overrides: {
    gaps?: GapsReadResult;
    approvedSuggestions?: SuggestionRecord[];
    sessionResponse?: SessionStatus;
  } = {},
) {
  const gapsRead = vi.fn(
    async (..._args: unknown[]) => overrides.gaps ?? baseGapsResult(),
  );
  const suggestionsRead = vi.fn(
    async (_topic: string, filter: string, ..._rest: unknown[]) => {
      if (filter === "approved") {
        return suggestionsResult(
          "approved",
          overrides.approvedSuggestions ?? [],
        );
      }
      return suggestionsResult("pending", []);
    },
  );
  const gapfillDiscover = vi.fn(async (..._args: unknown[]) => ({
    action: "gapfill_discover" as const,
    topic: TOPIC,
    provider_configured: true,
    open_gaps: 1,
    would_drain: 1,
    max_gaps: null,
    estimated_cost: "~$0.02",
    confirm_nonce: "nonce-abc",
    ttl: 300,
  }));
  const suggestionsReview = vi.fn(async (..._args: unknown[]) => ({
    mode: "apply" as const,
  }));
  const sessionStatus = vi.fn(
    async (_topic: string, suggestionId: string, ..._rest: unknown[]) =>
      overrides.sessionResponse ?? notStartedSession(suggestionId),
  );
  const sendMessage = vi.fn(async (..._args: unknown[]) => undefined);
  const updateModelContext = vi.fn(async (..._args: unknown[]) => undefined);
  const raw = {
    gapsRead,
    suggestionsRead,
    gapfillDiscover,
    suggestionsReview,
    sessionStatus,
    sendMessage,
    updateModelContext,
    hostCapabilities: {},
    mount: "bridge" as const,
  };
  return { client: raw as unknown as ToolClient, ...raw };
}

function renderFillLane(
  client: ToolClient | null,
  overrides: Partial<FillLaneProps> = {},
): HTMLElement {
  // `render(...).container` is typed `Element` by the library but is always
  // a real `<div>` at runtime -- the same cast `IngestGateStage.test.tsx`
  // uses for the identical reason (passing it into `within()` directly).
  return render(
    <FillLane
      client={client}
      topic={TOPIC}
      vault={VAULT}
      status={overrides.status ?? null}
      onStatusRefresh={overrides.onStatusRefresh}
    />,
  ).container as HTMLElement;
}

// ---------------------------------------------------------------------------
// Structure: five rail stages, assembled from the two absorbed components
// ---------------------------------------------------------------------------

describe("the five-stage rail, assembled from QueueStage + IngestGateStage", () => {
  it("renders exactly the five stages LANE_STAGES.fill declares, in that order, under one aria-labelled rail", async () => {
    const { client, gapsRead, suggestionsRead } = fakeClient();
    const container = renderFillLane(client);
    await screen.findByRole("list", { name: "fill stages" });
    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());
    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());

    const nodes = stageNodes(container);
    const expectedTitles = LANE_STAGES.fill.map((stage) => stage.title);
    expect(nodes).toHaveLength(LANE_STAGES.fill.length);
    expect(
      nodes.map((node) => node.querySelector("strong")?.textContent),
    ).toEqual(expectedTitles);
  });

  it("wraps all five stages in a single lane-rail list, not a second navigation layer", async () => {
    const { client } = fakeClient();
    const container = renderFillLane(client);
    await screen.findByRole("list", { name: "fill stages" });

    expect(container.querySelectorAll("ol.lane-rail")).toHaveLength(1);
  });

  it("renders without crashing when client is null (the codebase's cross-cutting null-client invariant)", async () => {
    const container = renderFillLane(null);
    await screen.findByRole("list", { name: "fill stages" });

    expect(stageNodes(container)).toHaveLength(LANE_STAGES.fill.length);
  });
});

// ---------------------------------------------------------------------------
// One data spine: identical client/topic/vault reaches both stage groups
// ---------------------------------------------------------------------------

describe("one data spine feeds both stage groups", () => {
  it("threads the same topic/vault to QueueStage's pending-suggestions fetch and IngestGateStage's approved-suggestions fetch", async () => {
    const { client, suggestionsRead } = fakeClient();
    renderFillLane(client);

    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalledTimes(2));
    // The two stage groups page at different sizes on purpose: the triage
    // queue reads 50 at a time so its client-side priority sort usually
    // covers the whole queue, while the ingest list stays at 20 because it
    // expands one singleton row at a time.
    expect(suggestionsRead).toHaveBeenCalledWith(
      TOPIC,
      "pending",
      "",
      50,
      VAULT,
    );
    expect(suggestionsRead).toHaveBeenCalledWith(
      TOPIC,
      "approved",
      "",
      20,
      VAULT,
    );
  });

  it("renders content from both stage groups together, proving FillLane mounts both rather than one", async () => {
    const { client } = fakeClient({
      approvedSuggestions: [APPROVED_SUGGESTION],
    });
    const container = renderFillLane(client);

    // `findByText(/open gaps/i)` is ambiguous here -- QueueStage's discover
    // stage also renders static prose containing "open gaps" mid-sentence,
    // so the same fixture that proves both stage groups mounted together
    // trips a multiple-match error on a plain text query. Query the gap
    // stage's own heading role instead -- same fact asserted (the gap stage
    // rendered its count heading), disambiguated by element type rather than
    // by weakening what is checked.
    await screen.findByRole("heading", { name: /open gaps/i });
    await within(stageNodes(container)[INGEST]).findByRole("button", {
      name: APPROVED_SUGGESTION.candidate.title,
    });
  });
});

// ---------------------------------------------------------------------------
// Watermark: per-stage state read from status.topics[].lanes.fill, verbatim
// ---------------------------------------------------------------------------

describe("watermark: per-stage state from status.topics[].lanes.fill, verbatim", () => {
  it("defaults every stage to pending when no status/lanes block is supplied", async () => {
    const { client } = fakeClient();
    const container = renderFillLane(client, { status: null });
    await screen.findByRole("list", { name: "fill stages" });

    await vi.waitFor(() =>
      expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
        "pending",
        "pending",
        "pending",
        "pending",
        "pending",
      ]),
    );
  });

  it("propagates each declared stage's state onto its matching row, in rail order", async () => {
    const declared: LaneRailStageStatus[] = [
      { id: "gap", state: "complete", reason: null },
      { id: "discover", state: "complete", reason: null },
      { id: "approve", state: "active", reason: null },
      { id: "ingest", state: "pending", reason: null },
      { id: "gate", state: "blocked", reason: "awaiting rework" },
    ];
    const { client } = fakeClient({
      approvedSuggestions: [APPROVED_SUGGESTION],
    });
    const container = renderFillLane(client, { status: baseStatus(declared) });
    await screen.findByRole("list", { name: "fill stages" });

    await vi.waitFor(() =>
      expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
        "complete",
        "complete",
        "active",
        "pending",
        "blocked",
      ]),
    );
  });
});

// ---------------------------------------------------------------------------
// Collapsed/active rendering per §1.5: glyph + word together, never gated
// ---------------------------------------------------------------------------

describe("collapsed/active rendering per §1.5 (glyph and state word together, content never hidden)", () => {
  it("renders a check glyph and the literal state word for a complete stage, without hiding its content", async () => {
    const declared: LaneRailStageStatus[] = [
      { id: "gap", state: "complete", reason: null },
    ];
    const { client } = fakeClient();
    const container = renderFillLane(client, { status: baseStatus(declared) });
    await screen.findByRole("list", { name: "fill stages" });
    await vi.waitFor(() =>
      expect(stageNodes(container)[GAP].dataset.state).toBe("complete"),
    );

    const gapNode = stageNodes(container)[GAP];
    expect(gapNode.querySelector(".lane-stage-index")?.textContent).toBe("✓");
    expect(gapNode.querySelector(".lane-state-label")?.textContent).toBe(
      "complete",
    );
    // Fill never gates content on stage state (unlike ImproveLane) -- the
    // stage's own intro prose must still be present.
    expect(gapNode.textContent ?? "").toMatch(/waiting for source discovery/i);
  });

  it("renders a blocked glyph and its state word for a blocked stage", async () => {
    const declared: LaneRailStageStatus[] = [
      { id: "gate", state: "blocked", reason: "awaiting rework" },
    ];
    const { client } = fakeClient();
    const container = renderFillLane(client, { status: baseStatus(declared) });
    await screen.findByRole("list", { name: "fill stages" });
    await vi.waitFor(() =>
      expect(stageNodes(container)[GATE].dataset.state).toBe("blocked"),
    );

    const gateNode = stageNodes(container)[GATE];
    expect(gateNode.querySelector(".lane-stage-index")?.textContent).toBe("!");
    expect(gateNode.querySelector(".lane-state-label")?.textContent).toBe(
      "blocked",
    );
  });

  it("renders a numeral glyph (never a checkmark or bang) for a pending stage", async () => {
    const { client } = fakeClient();
    const container = renderFillLane(client, { status: null });
    await screen.findByRole("list", { name: "fill stages" });
    await vi.waitFor(() =>
      expect(stageNodes(container)[DISCOVER].dataset.state).toBe("pending"),
    );

    const glyph =
      stageNodes(container)[DISCOVER].querySelector(
        ".lane-stage-index",
      )?.textContent;
    expect(glyph).toMatch(/^\d+$/);
  });
});

// ---------------------------------------------------------------------------
// Assembly does not break either stage group's own interactive behaviour
// ---------------------------------------------------------------------------

describe("assembly preserves each stage group's own interactivity", () => {
  it("still opens IngestGateStage's handoff session when an approved suggestion row is expanded", async () => {
    const { client } = fakeClient({
      approvedSuggestions: [APPROVED_SUGGESTION],
    });
    const container = renderFillLane(client);

    const ingestNode = stageNodes(container)[INGEST];
    const expandButton = await within(ingestNode).findByRole("button", {
      name: APPROVED_SUGGESTION.candidate.title,
    });
    fireEvent.click(expandButton);

    await within(ingestNode).findByRole("button", { name: "Open a session" });
  });

  it("does not desynchronize QueueStage's approve stage when IngestGateStage's own item expands", async () => {
    const { client } = fakeClient({
      approvedSuggestions: [APPROVED_SUGGESTION],
    });
    const container = renderFillLane(client);

    const ingestNode = stageNodes(container)[INGEST];
    const expandButton = await within(ingestNode).findByRole("button", {
      name: APPROVED_SUGGESTION.candidate.title,
    });
    fireEvent.click(expandButton);
    await within(ingestNode).findByRole("button", { name: "Open a session" });

    // QueueStage's approve stage is untouched by IngestGateStage's own
    // singleton expansion -- the two stage groups' internal states are
    // independent by domain construction (§ REGISTER OBJECTION above), so
    // the approve stage still renders its own (empty, per this fixture)
    // pending-suggestions content rather than reacting to ingest's expand.
    const approveNode = stageNodes(container)[APPROVE];
    expect(approveNode.textContent ?? "").not.toMatch(
      APPROVED_SUGGESTION.candidate.title,
    );
  });
});
