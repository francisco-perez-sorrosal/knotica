import { cleanup, fireEvent, render, within } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ToolClient } from "../../../toolClient";
import type {
  GapfillDiscoverResult,
  GapRecord,
  GapsReadResult,
  LaneRailStageStatus,
  SuggestionRecord,
  SuggestionsReadResult,
  WikiStatus,
} from "../../../types";

/**
 * `dashboard/src/lanes/fill/QueueStage.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step absorbing `SourcesPane.tsx`'s
 * `gap`/`discover`/`approve` logic (`INTERFACE_DESIGN.md §2.5`) into three
 * rail stages, unchanged in behaviour -- the plan's own words: "a move, not
 * a rewrite," mirroring M3's `TendLane` absorption. Loaded through a
 * non-literal dynamic `import()` -- the same device `TendLane.test.tsx`
 * (Step 66) and `ImproveLane.test.tsx` used for their own not-yet-existing
 * modules: a literal `import { QueueStage } from "../QueueStage"` would fail
 * `tsc --noEmit` for the whole project the moment this file lands; a dynamic
 * import whose argument is not a string literal is left unresolved by
 * TypeScript, so the rest of the tree keeps type-checking while this file
 * fails at *runtime* with the missing-module error the paired implementation
 * step is gated on.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (full reasoning in `LEARNINGS_test-engineer_step97.md`; the paired
 * implementation wins on conflict):
 *
 *   1. `<QueueStage client={...} topic={...} vault={...} status={...}
 *      onStatusRefresh={...} />` -- the exact five props `SourcesPane.tsx`
 *      already takes today, since this step moves its logic rather than
 *      redesigning its interface.
 *   2. Renders exactly three elements matching `.lane-stage` -- ids `gap`,
 *      `discover`, `approve`, in that order. No assumption is made about a
 *      wrapping `<ol>`: `FillLane.tsx` (a later step) assembles the full
 *      five-stage rail from this file's three plus `IngestGateStage`'s two,
 *      the same way `ImproveLane.tsx` assembles six single-stage bodies
 *      under one shared `<ol>`.
 *   3. Each `.lane-stage`'s `data-state` is read from
 *      `status.topics[topic].lanes.fill` (the server-derived rail states
 *      landed in M2 Step 48) by id, defaulting to `"pending"` when that id
 *      is absent from the array or when `status`/`lanes` is missing --
 *      mirroring `ImproveLane.tsx`'s "one data spine, states arrive as
 *      already-derived facts" convention (`declared?.state ?? "pending"`).
 *      Unlike `ImproveLane`, this suite does **not** gate the underlying
 *      content's visibility on that state (no progressive disclosure):
 *      `SourcesPane` renders its gap/discover/approve content
 *      unconditionally today, and Step 96's own "behaviour-preserving move,
 *      not a rewrite" instruction rules out adding a new visibility gate as
 *      part of this move. `data-state` is asserted as a wrapper attribute
 *      only.
 *
 * REGISTER OBJECTION -- the dispatch brief for this step asked for tests of
 * "the dismiss affordance requiring a reason" and "resolved/dismissed
 * buckets." `IMPLEMENTATION_PLAN.md`'s own M4 `## Scope` section rules this
 * out explicitly for this milestone's dashboard surface: the human
 * dismiss/reopen transition (`review_gap`) "is out of this milestone's
 * scope: it is a flat MCP tool already registered ... not a dashboard
 * affordance ... adding a UI control for it here would be scope growth
 * beyond what the design specifies -- noted, not built." This suite follows
 * the plan's own written scope, not the paraphrase: no dismiss affordance,
 * no resolved/dismissed-bucket assertions. Every `GapRecord` fixture below
 * carries `status: "open"`, matching `SourcesPane`'s own
 * `gapsRead(topic, "open", ...)` call -- there is nothing here to
 * characterize for the other two statuses because nothing today writes them
 * (`INTERFACE_DESIGN.md §2.5`'s own N6 finding).
 *
 * Not tested here (later steps' job): `ingest`/`gate` (Step 98's
 * `IngestGateStage`), the assembled five-stage `FillLane` rail (Step 100),
 * and `review_gap`/dismiss (out of scope, per above).
 */

interface QueueStageProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status?: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}

type QueueStageComponent = (props: QueueStageProps) => JSX.Element;

interface QueueStageModule {
  QueueStage: QueueStageComponent;
}

const QUEUE_STAGE_MODULE_PATH = "../QueueStage";

let QueueStage: QueueStageComponent;

beforeAll(async () => {
  ({ QueueStage } = (await import(QUEUE_STAGE_MODULE_PATH)) as QueueStageModule);
});

afterEach(cleanup);

const TOPIC = "rag-patterns";
const VAULT = "kb";

const GAP = 0;
const DISCOVER = 1;
const APPROVE = 2;

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

function isDisabled(element: HTMLElement): boolean {
  return (element as HTMLButtonElement).disabled === true;
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

function baseGapsResult(overrides: Partial<GapsReadResult> = {}): GapsReadResult {
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
    reputability: { tier: "preprint_known_lab", score: 0.7, signals: ["known lab"] },
    schema_version: 1,
    ...overrides,
  };
}

function baseSuggestion(overrides: Partial<SuggestionRecord> = {}): SuggestionRecord {
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
    status: "pending",
    proposed_at: "2026-08-25T00:00:00Z",
    decided_at: null,
    decided_reason: null,
    ingested_at: null,
    detected_generation: 3,
    gap_origin: "measured",
    gate_outcome: null,
    ...overrides,
  };
}

function baseSuggestionsResult(
  overrides: Partial<SuggestionsReadResult> = {},
): SuggestionsReadResult {
  return {
    topic: TOPIC,
    status_filter: "pending",
    suggestions: [baseSuggestion()],
    status_counts: { pending: 1, approved: 0, rejected: 0, deferred: 0, ingested: 0 },
    next_cursor: "",
    has_more: false,
    total_count: 1,
    skipped_malformed: 0,
    ...overrides,
  };
}

function discoverPreview(
  overrides: Partial<GapfillDiscoverResult> = {},
): GapfillDiscoverResult {
  return {
    action: "gapfill_discover",
    topic: TOPIC,
    provider_configured: true,
    open_gaps: 4,
    would_drain: 4,
    max_gaps: null,
    estimated_cost: "~$0.02",
    confirm_nonce: "nonce-abc",
    ttl: 300,
    ...overrides,
  };
}

function discoverOutcome(
  overrides: Partial<GapfillDiscoverResult> = {},
): GapfillDiscoverResult {
  return {
    action: "gapfill_discover",
    topic: TOPIC,
    provider_configured: true,
    gaps_considered: 4,
    gaps_drained: 4,
    suggestions_staged: 3,
    ...overrides,
  };
}

/** Minimal `WikiStatus`, only the fields `QueueStage` could plausibly read
 * (`topics[].lanes.fill`, `topics[].suggestions.refused_awaiting_rework`) --
 * matches `ImproveLane.test.tsx`'s own `baseStatus` cast convention. */
function baseStatus(overrides: {
  lanesFill?: LaneRailStageStatus[];
  refusedAwaitingRework?: number;
} = {}): WikiStatus {
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
          pending: 1,
          approved_awaiting_ingest: 0,
          deferred: 0,
          rejected: 0,
          ingested: 0,
          newest_proposed_at: null,
          refused_awaiting_rework: overrides.refusedAwaitingRework ?? 0,
        },
        lanes: overrides.lanesFill ? { fill: overrides.lanesFill } : undefined,
      },
    ],
    totals: { topics: 1, pages: 10, curated: 8, lint_violations: 0 },
  } as unknown as WikiStatus;
}

/** Boundary fake of `ToolClient` (`dashboard/CLAUDE.md`: "the single seam for
 * MCP calls") -- only the four methods `SourcesPane`'s gap/discover/approve
 * logic reaches. */
function fakeClient(
  overrides: {
    gaps?: GapsReadResult;
    suggestions?: SuggestionsReadResult;
    discoverPreviewResult?: GapfillDiscoverResult;
    discoverConfirmResult?: GapfillDiscoverResult;
  } = {},
) {
  const gapsRead = vi.fn(async (..._args: unknown[]) => overrides.gaps ?? baseGapsResult());
  const suggestionsRead = vi.fn(
    async (..._args: unknown[]) => overrides.suggestions ?? baseSuggestionsResult(),
  );
  const gapfillDiscover = vi.fn(async (...args: unknown[]) => {
    const confirm = args[2];
    if (confirm) return overrides.discoverConfirmResult ?? discoverOutcome();
    return overrides.discoverPreviewResult ?? discoverPreview();
  });
  const suggestionsReview = vi.fn(async (..._args: unknown[]) => ({
    mode: "apply" as const,
  }));
  const client = {
    gapsRead,
    suggestionsRead,
    gapfillDiscover,
    suggestionsReview,
  } as unknown as ToolClient;
  return { client, gapsRead, suggestionsRead, gapfillDiscover, suggestionsReview };
}

function renderQueueStage(
  client: ToolClient,
  overrides: Partial<QueueStageProps> = {},
): Element {
  return render(
    <QueueStage
      client={client}
      topic={TOPIC}
      vault={VAULT}
      status={overrides.status ?? null}
      onStatusRefresh={overrides.onStatusRefresh}
    />,
  ).container;
}

// ---------------------------------------------------------------------------
// Structure: three rail stages, in order, states from the server-derived rail
// ---------------------------------------------------------------------------

describe("the three rail stages", () => {
  it("renders exactly the gap/discover/approve stages, in that order", async () => {
    const { client, gapsRead } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());

    const nodes = stageNodes(container);
    expect(nodes).toHaveLength(3);
    expect(nodes[GAP].textContent ?? "").toMatch(/gap/i);
    expect(nodes[DISCOVER].textContent ?? "").toMatch(/discover/i);
    expect(nodes[APPROVE].textContent ?? "").toMatch(/approve/i);
  });

  it("defaults every stage to 'pending' when no status/lanes block is supplied", async () => {
    const { client, gapsRead } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());

    expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("propagates each declared stage's state from status.topics[].lanes.fill onto its own row", async () => {
    const declared: LaneRailStageStatus[] = [
      { id: "gap", state: "complete", reason: null },
      { id: "discover", state: "complete", reason: null },
      { id: "approve", state: "active", reason: null },
    ];
    const { client, gapsRead } = fakeClient();
    const container = renderQueueStage(client, {
      status: baseStatus({ lanesFill: declared }),
    });
    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());

    expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
      "complete",
      "complete",
      "active",
    ]);
  });

  it("defaults a stage missing from the declared array to 'pending', not a crash", async () => {
    const declared: LaneRailStageStatus[] = [
      { id: "gap", state: "complete", reason: null },
      // "discover" and "approve" deliberately absent.
    ];
    const { client, gapsRead } = fakeClient();
    const container = renderQueueStage(client, {
      status: baseStatus({ lanesFill: declared }),
    });
    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());

    expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
      "complete",
      "pending",
      "pending",
    ]);
  });

  it("still renders the interactive gap/discover/approve content when a stage is only 'pending' -- no new visibility gate", async () => {
    const { client, gapsRead } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());

    // "pending" describes the rail wrapper only; SourcesPane's own content
    // (the discover control, the approve/reject/defer buttons) is
    // unconditional today and this move must not silently hide it.
    expect(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    ).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The gap stage
// ---------------------------------------------------------------------------

describe("the gap stage", () => {
  it("loads open gaps for the topic on mount", async () => {
    const { client, gapsRead } = fakeClient();
    renderQueueStage(client);

    await vi.waitFor(() => expect(gapsRead).toHaveBeenCalled());
    expect(gapsRead.mock.calls[0]).toContain(TOPIC);
    expect(gapsRead.mock.calls[0]).toContain("open");
  });

  it("renders each open gap's question", async () => {
    const gap = baseGap({ question: "no coverage of HyDE" });
    const { client } = fakeClient({ gaps: baseGapsResult({ gaps: [gap] }) });
    const container = renderQueueStage(client);

    await vi.waitFor(() =>
      expect(within(stageNodes(container)[GAP]).getByText(/no coverage of hyde/i)).toBeTruthy(),
    );
  });

  it("surfaces a reported gap's reason, when present", async () => {
    const gap = baseGap({
      origin: "reported",
      reported_reason: "the page never mentions retrieval augmentation",
    });
    const { client } = fakeClient({ gaps: baseGapsResult({ gaps: [gap] }) });
    const container = renderQueueStage(client);

    await vi.waitFor(() =>
      expect(
        within(stageNodes(container)[GAP]).getByText(/never mentions retrieval augmentation/i),
      ).toBeTruthy(),
    );
  });

  it("keeps the suggestion queue rendering when the gaps read fails -- the two queues fail independently", async () => {
    const { client, gapsRead, suggestionsRead } = fakeClient();
    gapsRead.mockRejectedValueOnce(new Error("gaps backend unavailable"));
    const container = renderQueueStage(client);

    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());
    expect(
      await within(stageNodes(container)[APPROVE]).findByText(
        /hyde dense retrieval|no coverage of hyde/i,
      ),
    ).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The discover stage — the two-phase billed flow
// ---------------------------------------------------------------------------

describe("the discover stage's two-phase flow", () => {
  it("the preview leg never passes a confirm nonce, and does not bill", async () => {
    const { client, gapfillDiscover } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(client.gapsRead).toBeDefined());

    fireEvent.click(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    );

    await vi.waitFor(() => expect(gapfillDiscover).toHaveBeenCalledTimes(1));
    const [, , confirmArg] = gapfillDiscover.mock.calls[0];
    expect(confirmArg).toBe("");
  });

  it("shows the quoted estimate and an explicit NOT-yet-billed notice after the preview leg", async () => {
    const { client } = fakeClient({
      discoverPreviewResult: discoverPreview({ would_drain: 4, open_gaps: 4 }),
    });
    const container = renderQueueStage(client);

    fireEvent.click(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    );

    await vi.waitFor(() =>
      expect(within(stageNodes(container)[DISCOVER]).getByText(/has\s*not\s*billed yet/i)).toBeTruthy(),
    );
  });

  it("the confirm leg passes the nonce minted by the preview leg", async () => {
    const { client, gapfillDiscover } = fakeClient({
      discoverPreviewResult: discoverPreview({ confirm_nonce: "nonce-xyz" }),
    });
    const container = renderQueueStage(client);

    fireEvent.click(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    );
    await vi.waitFor(() => expect(gapfillDiscover).toHaveBeenCalledTimes(1));

    fireEvent.click(
      await within(stageNodes(container)[DISCOVER]).findByRole("button", {
        name: /confirm.*run and bill/i,
      }),
    );

    await vi.waitFor(() => expect(gapfillDiscover).toHaveBeenCalledTimes(2));
    const [, , confirmArg] = gapfillDiscover.mock.calls[1];
    expect(confirmArg).toBe("nonce-xyz");
  });

  it("never calls the confirm leg from a single click -- billing needs the explicit second click", async () => {
    const { client, gapfillDiscover } = fakeClient();
    const container = renderQueueStage(client);

    fireEvent.click(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    );

    await vi.waitFor(() => expect(gapfillDiscover).toHaveBeenCalledTimes(1));
    // Give any hidden auto-confirm a chance to fire before asserting it never did.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(gapfillDiscover).toHaveBeenCalledTimes(1);
  });

  it("reloads gaps, suggestions, and the app-level status after a successful confirm", async () => {
    const onStatusRefresh = vi.fn();
    const { client, gapsRead, suggestionsRead } = fakeClient({
      discoverPreviewResult: discoverPreview(),
    });
    const container = renderQueueStage(client, { onStatusRefresh });

    fireEvent.click(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    );
    await vi.waitFor(() => expect(client.gapfillDiscover).toHaveBeenCalledTimes(1));
    gapsRead.mockClear();
    suggestionsRead.mockClear();

    fireEvent.click(
      await within(stageNodes(container)[DISCOVER]).findByRole("button", {
        name: /confirm.*run and bill/i,
      }),
    );

    await vi.waitFor(() => expect(onStatusRefresh).toHaveBeenCalled());
    expect(gapsRead).toHaveBeenCalled();
    expect(suggestionsRead).toHaveBeenCalled();
  });

  it("disables the confirm leg when no search provider is configured", async () => {
    const { client } = fakeClient({
      discoverPreviewResult: discoverPreview({ provider_configured: false }),
    });
    const container = renderQueueStage(client);

    fireEvent.click(
      within(stageNodes(container)[DISCOVER]).getByRole("button", {
        name: /discover sources/i,
      }),
    );

    await vi.waitFor(() =>
      expect(within(stageNodes(container)[DISCOVER]).getByText(/no search provider is configured/i)).toBeTruthy(),
    );
    expect(
      isDisabled(
        within(stageNodes(container)[DISCOVER]).getByRole("button", {
          name: /confirm.*run and bill/i,
        }),
      ),
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// The approve stage — the suggestion queue
// ---------------------------------------------------------------------------

describe("the approve stage's suggestion queue", () => {
  it("loads pending suggestions for the topic on mount", async () => {
    const { client, suggestionsRead } = fakeClient();
    renderQueueStage(client);

    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalled());
    expect(suggestionsRead.mock.calls[0]).toContain(TOPIC);
    expect(suggestionsRead.mock.calls[0]).toContain("pending");
  });

  it("approving a suggestion calls suggestionsReview with the 'approve' action, applied", async () => {
    const { client, suggestionsReview } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(client.suggestionsRead).toHaveBeenCalled());

    fireEvent.click(
      await within(stageNodes(container)[APPROVE]).findByRole("button", { name: /approve/i }),
    );

    await vi.waitFor(() => expect(suggestionsReview).toHaveBeenCalled());
    const call = suggestionsReview.mock.calls[0];
    expect(call).toContain(TOPIC);
    expect(call).toContain("s_1a2b3c4d");
    expect(call).toContain("approve");
    expect(call).toContain("apply");
  });

  it("deferring a suggestion calls suggestionsReview with the 'defer' action", async () => {
    const { client, suggestionsReview } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(client.suggestionsRead).toHaveBeenCalled());

    fireEvent.click(
      await within(stageNodes(container)[APPROVE]).findByRole("button", { name: /defer/i }),
    );

    await vi.waitFor(() => expect(suggestionsReview).toHaveBeenCalled());
    expect(suggestionsReview.mock.calls[0]).toContain("defer");
  });

  it("rejecting requires opening the reason form and typing a non-empty reason before it can be confirmed", async () => {
    const { client, suggestionsReview } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(client.suggestionsRead).toHaveBeenCalled());

    fireEvent.click(
      await within(stageNodes(container)[APPROVE]).findByRole("button", { name: /reject/i }),
    );
    const confirmButton = within(stageNodes(container)[APPROVE]).getByRole("button", {
      name: /confirm reject/i,
    });
    expect(isDisabled(confirmButton)).toBe(true);

    fireEvent.input(within(stageNodes(container)[APPROVE]).getByPlaceholderText(/why doesn't this source fit/i), {
      target: { value: "off-topic for this vault" },
    });
    expect(isDisabled(confirmButton)).toBe(false);

    fireEvent.click(confirmButton);

    await vi.waitFor(() => expect(suggestionsReview).toHaveBeenCalled());
    const call = suggestionsReview.mock.calls[0];
    expect(call).toContain("reject");
    expect(call).toContain("off-topic for this vault");
  });

  it("a decided suggestion shows its recorded decision instead of the approve/reject/defer controls", async () => {
    const decided = baseSuggestion({
      status: "rejected",
      decided_reason: "duplicate of an existing citation",
    });
    const { client } = fakeClient({
      suggestions: baseSuggestionsResult({ suggestions: [decided], status_filter: "all" }),
    });
    const container = renderQueueStage(client);

    await vi.waitFor(() =>
      expect(within(stageNodes(container)[APPROVE]).getByText(/rejected/i)).toBeTruthy(),
    );
    expect(
      within(stageNodes(container)[APPROVE]).queryByRole("button", { name: /^✓ approve$/i }),
    ).toBeNull();
    expect(
      within(stageNodes(container)[APPROVE]).getByText(/duplicate of an existing citation/i),
    ).toBeTruthy();
  });

  it("renders the topic-wide refused-awaiting-rework count from status, not a page-local recount", async () => {
    const { client } = fakeClient();
    const container = renderQueueStage(client, {
      status: baseStatus({ refusedAwaitingRework: 2 }),
    });
    await vi.waitFor(() => expect(client.suggestionsRead).toHaveBeenCalled());

    expect(within(stageNodes(container)[APPROVE]).getByText(/refused 2/i)).toBeTruthy();
  });

  it("never uses a native confirm dialog for reject -- the reason form is in-DOM", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { client } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(client.suggestionsRead).toHaveBeenCalled());

    fireEvent.click(
      await within(stageNodes(container)[APPROVE]).findByRole("button", { name: /reject/i }),
    );
    fireEvent.input(within(stageNodes(container)[APPROVE]).getByPlaceholderText(/why doesn't this source fit/i), {
      target: { value: "not relevant" },
    });
    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: /confirm reject/i }),
    );

    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
