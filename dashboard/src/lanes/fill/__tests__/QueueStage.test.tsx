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
    // Probes the candidate title rather than the failed question: a triage
    // row is collapsed by default and carries the source's identity, while
    // the question it answers sits behind the row's disclosure. The behaviour
    // asserted -- the suggestion queue survives a gaps failure -- is unchanged.
    expect(
      await within(stageNodes(container)[APPROVE]).findByText(
        /precise zero-shot dense retrieval/i,
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

  it("keeps every triage action on one quiet-button family -- no filled primary among peers", async () => {
    const { client } = fakeClient();
    const container = renderQueueStage(client);
    const approve = await within(stageNodes(container)[APPROVE]).findByRole("button", {
      name: /^✓ approve$/i,
    });
    const reject = within(stageNodes(container)[APPROVE]).getByRole("button", {
      name: /reject/i,
    });
    const defer = within(stageNodes(container)[APPROVE]).getByRole("button", {
      name: /defer/i,
    });

    for (const button of [approve, reject, defer]) {
      expect(button.className).toContain("quiet-action");
      expect(button.className).not.toContain("primary");
    }
    // Distinguished by tone, never by weight.
    expect([approve, reject, defer].map((b) => b.dataset.tone)).toEqual([
      "good",
      "bad",
      "neutral",
    ]);
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

// ---------------------------------------------------------------------------
// The approve stage — triage ordering, the toolbar, and the row disclosure
// ---------------------------------------------------------------------------

/** Four records whose *server* order is deliberately none of the sorted ones. */
function triageFixtures(): SuggestionRecord[] {
  return [
    baseSuggestion({
      suggestion_id: "s_charlie",
      rank: 1,
      candidate: baseCandidate({ title: "Charlie", reputability: null }),
    }),
    baseSuggestion({
      suggestion_id: "s_bravo",
      rank: 2,
      candidate: baseCandidate({
        title: "Bravo",
        reputability: { tier: "established_org", score: 0.7, signals: [] },
      }),
    }),
    baseSuggestion({
      suggestion_id: "s_alpha",
      rank: 5,
      candidate: baseCandidate({
        title: "Alpha",
        reputability: { tier: "peer_reviewed", score: 0.9, signals: [] },
      }),
    }),
    baseSuggestion({
      suggestion_id: "s_delta",
      rank: 3,
      candidate: baseCandidate({
        title: "Delta",
        reputability: { tier: "peer_reviewed", score: 0.9, signals: [] },
      }),
    }),
  ];
}

function rowTitles(container: Element): string[] {
  return Array.from(
    stageNodes(container)[APPROVE].querySelectorAll<HTMLElement>(".triage-title"),
  ).map((node) => node.textContent ?? "");
}

describe("the approve stage's triage ordering", () => {
  it("defaults to priority: reputability descending, ties broken by rank, unrated last", async () => {
    const { client } = fakeClient({
      suggestions: baseSuggestionsResult({ suggestions: triageFixtures() }),
    });
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(client.suggestionsRead).toHaveBeenCalled());

    // Delta before Alpha: both score 0.90, Delta ranks #3 against Alpha's #5.
    // Charlie last: no reputability block at all is absence, not a low score.
    await vi.waitFor(() =>
      expect(rowTitles(container)).toEqual(["Delta", "Alpha", "Bravo", "Charlie"]),
    );
  });

  it("returns the server's own order when the sort is switched to newest", async () => {
    const { client } = fakeClient({
      suggestions: baseSuggestionsResult({ suggestions: triageFixtures() }),
    });
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(rowTitles(container)).toHaveLength(4));

    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: "newest" }),
    );

    await vi.waitFor(() =>
      expect(rowTitles(container)).toEqual(["Charlie", "Bravo", "Alpha", "Delta"]),
    );
  });

  it("says so when the priority order only spans the records already loaded", async () => {
    const { client } = fakeClient({
      suggestions: baseSuggestionsResult({
        suggestions: triageFixtures(),
        has_more: true,
        next_cursor: "cur-2",
      }),
    });
    const container = renderQueueStage(client);

    expect(
      await within(stageNodes(container)[APPROVE]).findByText(/sorted across the 4 loaded/i),
    ).toBeTruthy();
  });
});

describe("the approve stage's toolbar", () => {
  it("names the refresh control, rather than leaving a bare glyph", async () => {
    const { client, suggestionsRead } = fakeClient();
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalledTimes(1));

    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: "Refresh" }),
    );

    await vi.waitFor(() => expect(suggestionsRead).toHaveBeenCalledTimes(2));
  });

  it("prints each filter's own count on its pill, and the whole-topic total on 'all'", async () => {
    const { client } = fakeClient({
      suggestions: baseSuggestionsResult({
        status_counts: { pending: 3, approved: 1, rejected: 2, deferred: 0, ingested: 4 },
      }),
    });
    const container = renderQueueStage(client);
    const approve = stageNodes(container)[APPROVE];

    // `all` sums status_counts rather than reading `total_count`, which
    // describes only the currently-filtered slice.
    expect(await within(approve).findByRole("button", { name: "pending 3" })).toBeTruthy();
    expect(within(approve).getByRole("button", { name: "accepted 1" })).toBeTruthy();
    expect(within(approve).getByRole("button", { name: "all 10" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The approve stage — a decided row transforms in place, it never vanishes
// ---------------------------------------------------------------------------

/**
 * A fake whose suggestions read *reflects* the decisions applied to it: the
 * server drops a decided record from the `pending` filter, so the reload after
 * a verdict no longer carries it. That absence is the whole reason the ghost
 * exists -- a fake that keeps serving the decided row cannot characterize it.
 *
 * `withdraw` puts the record back, in its original slot, exactly as the server
 * returning it to `pending` would.
 */
function decidingClient(initial: SuggestionRecord[]) {
  let live = initial.map((row) => row.suggestion_id);
  const rows = () => initial.filter((row) => live.includes(row.suggestion_id));
  const suggestionsRead = vi.fn(async (..._args: unknown[]) =>
    baseSuggestionsResult({
      suggestions: rows(),
      status_counts: {
        pending: rows().length,
        approved: initial.length - rows().length,
        rejected: 0,
        deferred: 0,
        ingested: 0,
      },
    }),
  );
  const suggestionsReview = vi.fn(async (...args: unknown[]) => {
    const id = args[1] as string;
    const action = args[2] as string;
    live =
      action === "withdraw"
        ? [...live, id]
        : live.filter((entry) => entry !== id);
    return { mode: "apply" as const };
  });
  const client = {
    gapsRead: vi.fn(async (..._args: unknown[]) => baseGapsResult({ gaps: [] })),
    suggestionsRead,
    gapfillDiscover: vi.fn(),
    suggestionsReview,
  } as unknown as ToolClient;
  return { client, suggestionsRead, suggestionsReview };
}

function triageRows(container: Element): HTMLElement[] {
  return Array.from(
    stageNodes(container)[APPROVE].querySelectorAll<HTMLElement>(".triage-row"),
  );
}

function rowFor(container: Element, title: string): HTMLElement {
  const row = triageRows(container).find(
    (node) => (node.querySelector(".triage-title")?.textContent ?? "") === title,
  );
  if (!row) throw new Error(`no triage row titled ${title}`);
  return row;
}

/** The ghost's own sentence -- glyph and word live in separate spans, so the
 *  assertion reads the statement's text rather than querying for one string. */
function ghostStatement(container: Element, title: string): string {
  return rowFor(container, title).querySelector(".triage-ghost-statement")?.textContent ?? "";
}

async function approveRow(container: Element, title: string): Promise<void> {
  await vi.waitFor(() => rowFor(container, title));
  fireEvent.click(within(rowFor(container, title)).getByRole("button", { name: /^✓ approve$/i }));
}

describe("a decided row's ghost", () => {
  it("keeps the approved row on screen, stating what became of it", async () => {
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    const title = baseCandidate().title;
    await approveRow(container, title);

    // The row is still there -- a vanish is what made the click look like a no-op.
    await vi.waitFor(() =>
      expect(within(rowFor(container, title)).getByText(/queued for ingest/i)).toBeTruthy(),
    );
    expect(rowFor(container, title).dataset.decision).toBe("approved");
    // ...and it is no longer offering the decision it has already taken.
    expect(
      within(rowFor(container, title)).queryByRole("button", { name: /^✓ approve$/i }),
    ).toBeNull();
  });

  // Titles are deliberately free of the verbs: a row's disclosure is named
  // "Details for <title>", so a candidate called "Reject me" would collide
  // with the accessible name of its own reject button.
  it("states a rejection and a deferral in the same place, each toned to its own decision", async () => {
    const rows = [
      baseSuggestion({ suggestion_id: "s_r", candidate: baseCandidate({ title: "Cardinal" }) }),
      baseSuggestion({ suggestion_id: "s_d", candidate: baseCandidate({ title: "Sparrow" }) }),
    ];
    const { client } = decidingClient(rows);
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(triageRows(container)).toHaveLength(2));

    fireEvent.click(
      within(rowFor(container, "Cardinal")).getByRole("button", { name: /^✕ reject/i }),
    );
    fireEvent.input(
      within(rowFor(container, "Cardinal")).getByPlaceholderText(/why doesn't this source fit/i),
      { target: { value: "off-topic" } },
    );
    fireEvent.click(
      within(rowFor(container, "Cardinal")).getByRole("button", { name: /confirm reject/i }),
    );
    await vi.waitFor(() => expect(rowFor(container, "Cardinal").dataset.decision).toBe("rejected"));

    fireEvent.click(
      within(rowFor(container, "Sparrow")).getByRole("button", { name: /^⧗ defer$/i }),
    );
    await vi.waitFor(() => expect(rowFor(container, "Sparrow").dataset.decision).toBe("deferred"));

    // Word plus glyph, never the left-edge tone alone (WCAG 1.4.1).
    expect(ghostStatement(container, "Cardinal")).toMatch(/✕\s*rejected/);
    expect(ghostStatement(container, "Sparrow")).toMatch(/⧗\s*deferred/);
  });

  it("holds the ghost's own slot under priority order", async () => {
    const { client } = decidingClient(triageFixtures());
    const container = renderQueueStage(client);
    await vi.waitFor(() =>
      expect(rowTitles(container)).toEqual(["Delta", "Alpha", "Bravo", "Charlie"]),
    );

    await approveRow(container, "Bravo");

    await vi.waitFor(() =>
      expect(within(rowFor(container, "Bravo")).getByText(/queued for ingest/i)).toBeTruthy(),
    );
    expect(rowTitles(container)).toEqual(["Delta", "Alpha", "Bravo", "Charlie"]);
  });

  it("holds the ghost's own slot under newest order, which has no comparator to re-derive it", async () => {
    const { client } = decidingClient(triageFixtures());
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(rowTitles(container)).toHaveLength(4));
    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: "newest" }),
    );
    await vi.waitFor(() =>
      expect(rowTitles(container)).toEqual(["Charlie", "Bravo", "Alpha", "Delta"]),
    );

    await approveRow(container, "Bravo");

    await vi.waitFor(() =>
      expect(within(rowFor(container, "Bravo")).getByText(/queued for ingest/i)).toBeTruthy(),
    );
    expect(rowTitles(container)).toEqual(["Charlie", "Bravo", "Alpha", "Delta"]);
  });

  it("moves the filter pill counts in the same reload", async () => {
    const { client } = decidingClient(triageFixtures());
    const container = renderQueueStage(client);
    const approve = stageNodes(container)[APPROVE];
    expect(await within(approve).findByRole("button", { name: "pending 4" })).toBeTruthy();

    await approveRow(container, "Bravo");

    await vi.waitFor(() =>
      expect(within(approve).getByRole("button", { name: "pending 3" })).toBeTruthy(),
    );
    expect(within(approve).getByRole("button", { name: "accepted 1" })).toBeTruthy();
  });
});

describe("the ghost's withdraw undo", () => {
  it("offers Withdraw on an approved ghost only", async () => {
    const rows = [
      baseSuggestion({ suggestion_id: "s_a", candidate: baseCandidate({ title: "Cardinal" }) }),
      baseSuggestion({ suggestion_id: "s_d", candidate: baseCandidate({ title: "Sparrow" }) }),
    ];
    const { client } = decidingClient(rows);
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(triageRows(container)).toHaveLength(2));

    await approveRow(container, "Cardinal");
    await vi.waitFor(() => expect(rowFor(container, "Cardinal").dataset.decision).toBe("approved"));
    fireEvent.click(
      within(rowFor(container, "Sparrow")).getByRole("button", { name: /^⧗ defer$/i }),
    );
    await vi.waitFor(() => expect(rowFor(container, "Sparrow").dataset.decision).toBe("deferred"));

    expect(
      within(rowFor(container, "Cardinal")).getByRole("button", { name: /^withdraw$/i }),
    ).toBeTruthy();
    expect(
      within(rowFor(container, "Sparrow")).queryByRole("button", { name: /^withdraw$/i }),
    ).toBeNull();
  });

  it("round-trips: withdrawing returns the record to pending and drops its ghost", async () => {
    const { client, suggestionsReview } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    const title = baseCandidate().title;
    await approveRow(container, title);
    await vi.waitFor(() => expect(rowFor(container, title).dataset.decision).toBe("approved"));

    fireEvent.click(within(rowFor(container, title)).getByRole("button", { name: /withdraw/i }));

    await vi.waitFor(() =>
      expect(
        within(rowFor(container, title)).getByRole("button", { name: /^✓ approve$/i }),
      ).toBeTruthy(),
    );
    expect(rowFor(container, title).dataset.decision).toBeUndefined();
    expect(suggestionsReview.mock.calls[1]).toContain("withdraw");
    // Still exactly one row -- the live record replaced its own ghost.
    expect(triageRows(container)).toHaveLength(1);
  });
});

describe("the ghost's lifetime", () => {
  it("clears on a filter switch -- a different filter is a different context", async () => {
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    const title = baseCandidate().title;
    await approveRow(container, title);
    await vi.waitFor(() => expect(rowFor(container, title).dataset.decision).toBe("approved"));

    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: /^all/i }),
    );

    await vi.waitFor(() => expect(triageRows(container)).toHaveLength(0));
  });

  it("clears on the manual refresh -- asking for the truth gets the truth", async () => {
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    const title = baseCandidate().title;
    await approveRow(container, title);
    await vi.waitFor(() => expect(rowFor(container, title).dataset.decision).toBe("approved"));

    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: "Refresh" }),
    );

    await vi.waitFor(() => expect(triageRows(container)).toHaveLength(0));
  });

  it("survives a Load more append -- widening the page is not a context change", async () => {
    const first = baseSuggestion({
      suggestion_id: "s_first",
      candidate: baseCandidate({ title: "First" }),
    });
    const second = baseSuggestion({
      suggestion_id: "s_second",
      candidate: baseCandidate({ title: "Second" }),
    });
    // Keyed on the cursor, not on call order: the reload after a decision
    // re-reads page one, which by then no longer carries the decided record.
    let decided = false;
    const suggestionsRead = vi.fn(async (...args: unknown[]) =>
      args[2]
        ? baseSuggestionsResult({ suggestions: [second] })
        : baseSuggestionsResult({
            suggestions: decided ? [] : [first],
            has_more: true,
            next_cursor: "cur-2",
          }),
    );
    const client = {
      gapsRead: vi.fn(async () => baseGapsResult({ gaps: [] })),
      suggestionsRead,
      gapfillDiscover: vi.fn(),
      suggestionsReview: vi.fn(async () => {
        decided = true;
        return { mode: "apply" as const };
      }),
    } as unknown as ToolClient;

    const container = renderQueueStage(client);
    await approveRow(container, "First");
    await vi.waitFor(() => expect(rowFor(container, "First").dataset.decision).toBe("approved"));

    fireEvent.click(
      within(stageNodes(container)[APPROVE]).getByRole("button", { name: /load more/i }),
    );

    await vi.waitFor(() => expect(rowFor(container, "Second")).toBeTruthy());
    expect(rowFor(container, "First").dataset.decision).toBe("approved");
  });
});

describe("the decision's non-visual feedback", () => {
  it("announces the outcome and what the queue holds now", async () => {
    const { client } = decidingClient(triageFixtures());
    const container = renderQueueStage(client);
    await vi.waitFor(() => expect(triageRows(container)).toHaveLength(4));

    await approveRow(container, "Bravo");

    const live = stageNodes(container)[APPROVE].querySelector('[role="status"]');
    await vi.waitFor(() =>
      expect(live?.textContent ?? "").toMatch(/approved: bravo — 3 pending remaining/i),
    );
  });

  it("announces a withdrawal too -- an undo is an outcome", async () => {
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    const title = baseCandidate().title;
    await approveRow(container, title);
    await vi.waitFor(() => expect(rowFor(container, title).dataset.decision).toBe("approved"));

    fireEvent.click(within(rowFor(container, title)).getByRole("button", { name: /withdraw/i }));

    const live = stageNodes(container)[APPROVE].querySelector('[role="status"]');
    await vi.waitFor(() =>
      expect(live?.textContent ?? "").toMatch(/withdrawn, back to pending/i),
    );
  });

  it("marks the row busy and busies only the control that was clicked", async () => {
    let release = (): void => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { client } = decidingClient([baseSuggestion()]);
    const slow = client as unknown as { suggestionsReview: (...args: unknown[]) => unknown };
    const inner = slow.suggestionsReview.bind(client);
    slow.suggestionsReview = async (...args: unknown[]) => {
      await gate;
      return inner(...args);
    };

    const container = renderQueueStage(client);
    const title = baseCandidate().title;
    await approveRow(container, title);

    await vi.waitFor(() => expect(rowFor(container, title).getAttribute("aria-busy")).toBe("true"));
    const row = within(rowFor(container, title));
    // The clicked control shows its busy form -- the verb is kept and marked
    // `aria-busy`, so the row's own busy flag is no longer the only place a
    // reader can learn *which* verb is running. Its peer keeps its own word,
    // disabled but not pretending to be the one in flight.
    const approving = row.getByRole("button", { name: /✓ approve/i });
    expect(approving.getAttribute("aria-busy")).toBe("true");
    expect(isDisabled(row.getByRole("button", { name: /⧗ defer/i }))).toBe(true);

    release();
    await vi.waitFor(() =>
      expect(rowFor(container, title).getAttribute("aria-busy")).toBeNull(),
    );
  });
});

describe("the triage row's disclosure", () => {
  it("hides the failed question until the row is expanded", async () => {
    const { client } = fakeClient();
    const container = renderQueueStage(client);
    const approve = stageNodes(container)[APPROVE];
    const disclose = await within(approve).findByRole("button", {
      name: /^details for /i,
    });

    expect(disclose.getAttribute("aria-expanded")).toBe("false");
    expect(within(approve).queryByText(/no coverage of hyde/i)).toBeNull();

    fireEvent.click(disclose);

    expect(await within(approve).findByText(/no coverage of hyde/i)).toBeTruthy();
    expect(disclose.getAttribute("aria-expanded")).toBe("true");
  });

  it("auto-expands the row when the reject form opens -- a reason needs the evidence", async () => {
    const { client } = fakeClient();
    const container = renderQueueStage(client);
    const approve = stageNodes(container)[APPROVE];

    fireEvent.click(await within(approve).findByRole("button", { name: /reject/i }));

    expect(await within(approve).findByText(/no coverage of hyde/i)).toBeTruthy();
    expect(within(approve).getByPlaceholderText(/why doesn't this source fit/i)).toBeTruthy();
  });
});

describe("the lifecycle contract on the triage verbs", () => {
  it("names what an approved source still owes, and it is not this stage", async () => {
    // The queue always said *that* a decision landed; it never said the
    // decision was only half of getting the source into the wiki. Approving
    // queues an instruction that only a session can run.
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    await approveRow(container, baseCandidate().title);

    const approve = stageNodes(container)[APPROVE];
    expect(await within(approve).findByText(/Go to Fill → Ingest\./)).toBeTruthy();
  });

  it("sends a rejection back to discovery -- the gap outlives the candidate", async () => {
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    const approve = stageNodes(container)[APPROVE];

    fireEvent.click(await within(approve).findByRole("button", { name: /^✕ reject…$/i }));
    fireEvent.input(within(approve).getByPlaceholderText(/why doesn't this source fit/i), {
      target: { value: "wrong topic" },
    });
    fireEvent.click(within(approve).getByRole("button", { name: /^confirm reject$/i }));

    expect(await within(approve).findByText(/Go to Fill → Discover\./)).toBeTruthy();
  });

  it("keeps one live region in the stage -- the outcome adds no second one", async () => {
    // A region inserted with its text is not reliably announced, so the
    // stage's own `sr-only` region stays mounted from first paint and stays
    // the only one.
    const { client } = decidingClient([baseSuggestion()]);
    const container = renderQueueStage(client);
    await approveRow(container, baseCandidate().title);

    await vi.waitFor(() =>
      expect(
        stageNodes(container)[APPROVE].querySelectorAll('[role="status"]'),
      ).toHaveLength(1),
    );
  });
});
