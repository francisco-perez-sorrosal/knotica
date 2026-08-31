import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { PROCESS_META } from "../../processMeta";
import type { ToolClient } from "../../../toolClient";
import type {
  NoteAnchor,
  NoteDecisionEnvelope,
  NoteDrift,
  NoteDriftAlternative,
  NoteDriftItem,
  NoteRecord,
  NotesDriftResult,
  NotesListResult,
} from "../../../types";

/**
 * `dashboard/src/lanes/tend/DriftStage.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step for `INTERFACE_DESIGN.md`
 * §2.6's fifth Tend stage (`drift`), absorbing `NotesPane.tsx`'s browse view
 * and `NotesDriftView.tsx` behind one collapsed checklist row. Loaded through
 * a non-literal dynamic `import()` specifier -- the same device
 * `lanes/tend/__tests__/TendLane.test.tsx` (Step 66) used for its own
 * not-yet-existing module, so the rest of the tree keeps type-checking while
 * this file fails at *runtime* with the missing-module error the paired
 * implementation step (67) is gated on.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (the paired implementation wins on conflict; record the actual call in
 * `LEARNINGS_implementer_step67.md` when made):
 *
 *   1. `<DriftStage client={...} topic={...} vault={...} />` -- three props,
 *      no `obsidianCtx` (neither `NotesPane.tsx` nor `NotesDriftView.tsx`
 *      imports `obsidianLinks` today, so nothing to carry forward) and,
 *      unlike `TendLane`'s other three checks, an explicit `topic` --
 *      `notes`'s own MCP dispatcher validates topic as a real vault
 *      directory and rejects an empty one with `TOPIC_NOT_FOUND`
 *      (`tools_dispatch_notes_common.py::_validate_topic`), so this stage
 *      cannot be vault-wide the way doctor/lint/okf are. `TendLane` is
 *      expected to thread the same ambient `topic` `App.tsx` already
 *      maintains for every other topic-scoped pane (`NotesPane` included,
 *      today), not to invent a second topic picker.
 *   2. **Collapse budget is the whole stage, not just the drift queue.**
 *      §2.6's mockup renders `drift` as a single collapsed summary line
 *      ("not checked — one git read per anchor [Check]"), unlike
 *      doctor/lint/okf which show real content immediately -- so both reads
 *      this stage needs (`notesList` for the merged browse listing,
 *      `notesDrift` for the per-anchor diff facts) are deferred together
 *      until the explicit `[Check]`/expand action, not just the expensive
 *      one. Zero calls to either on mount is the fitness-test contract this
 *      suite pins (mirroring Step 47's RISK-04 pattern).
 *   3. **The merge is real, not a relabeling.** "`NotesPane`'s browse view
 *      and `NotesDriftView` merge into the `drift` stage" (§2.6) is read
 *      literally: expand renders one list built from `notesList` (every
 *      note in the topic, browse-shaped), where anchors whose status is
 *      queue-eligible (`QUEUE_ELIGIBLE_STATUSES` --
 *      fuzzy/orphaned/anchor-invalid, mirrored from `NotesPane.tsx:45-49`)
 *      are decorated with the richer diff facts (`pinned_quote`/
 *      `live_quote`/`overlap`/`alternatives`) sourced from the matching
 *      `notesDrift` item, joined by `note_id` + `anchor_index`. A note with
 *      no queue-eligible anchor renders as a plain browse card (archive/
 *      promote/detach), never fetching a diff for it. This is the "filter
 *      within the single surface, not a second surface" `§2.6` names.
 *   4. **D3, re-examined**: closer reading of `NotePromoteDialog.tsx` (as
 *      the plan's own Step 67 text instructs) shows it implements the
 *      *`promote`* mutation only (`client.notesPromote`) -- it has no
 *      `reanchor`/`detach` logic at all. The actual `reanchor`/`detach`
 *      preview/confirm today is already a *single* shared implementation
 *      (`ActionConfirm` in `NotesDriftView.tsx`), reused as-is by both
 *      `NotesPane.tsx`'s card actions and `NotesDriftView.tsx`'s queue
 *      items. There is no live duplicate to collapse for those two verbs
 *      as the plan literally describes it -- flagged in
 *      `LEARNINGS_test-engineer_step68.md` rather than silently assumed
 *      away. This suite therefore pins the *regression* the plan's `Done
 *      when` actually cares about: exactly one preview/confirm banner shape
 *      is reachable for `reanchor`/`detach`, from any entry point, so a
 *      future change cannot reintroduce a second, divergent one (e.g. by
 *      wiring a `NotePromoteDialog`-shaped modal to `reanchor` instead).
 *      `NotePromoteDialog` itself is reused verbatim for `promote`, which
 *      was never in dispute.
 *   5. The `[Check]` control is queried by accessible name (`/check/i`),
 *      not a fixed `data-testid` -- the paired implementer may render it as
 *      a plain button or a `StageShell`-style disclosure toggle.
 *
 * Not tested here (out of this step's scope): wiring `DriftStage` as
 * `TendLane`'s fifth `<StageShell>` row and its checklist `data-state`
 * derivation (an implementation-time integration `TendLane.tsx` performs;
 * `DriftStage` is unit-tested standalone here, the same way `LaneRail.tsx`
 * was in Step 64 before `TendLane` consumed it in Step 65).
 */

interface DriftStageProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
}

type DriftStageComponent = (props: DriftStageProps) => JSX.Element;

interface DriftStageModule {
  DriftStage: DriftStageComponent;
}

const DRIFT_STAGE_MODULE_PATH = "../DriftStage";

let DriftStage: DriftStageComponent;

beforeAll(async () => {
  ({ DriftStage } = (await import(
    DRIFT_STAGE_MODULE_PATH
  )) as DriftStageModule);
});

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

function baseAnchor(overrides: Partial<NoteAnchor> = {}): NoteAnchor {
  return {
    index: 0,
    page: "agentic-systems/mipro.md",
    heading: "Overview",
    fidelity: "span",
    status: "fuzzy",
    resolved_fidelity: "span",
    quote: "the pinned passage",
    pinned_at: "abc123",
    ...overrides,
  };
}

function baseNote(overrides: Partial<NoteRecord> = {}): NoteRecord {
  return {
    note_id: "note-1",
    path: "notes/agentic-systems/note-1.md",
    intent: "reflection",
    created: "2026-01-01T00:00:00Z",
    updated: "2026-01-01T00:00:00Z",
    note_status: "active",
    status: "fuzzy",
    tags: [],
    note: "MIPRO tunes prompts via bootstrapped few-shot search.",
    anchors: [baseAnchor()],
    skipped_anchor_count: 0,
    ...overrides,
  };
}

function baseDrift(overrides: Partial<NoteDrift> = {}): NoteDrift {
  return {
    anchor_index: 0,
    cause: "rewritten",
    pinned_quote: "the pinned passage",
    live_quote: "the reworded passage",
    overlap: 0.62,
    alternatives: [],
    rewritten_at: "2026-02-01T00:00:00Z",
    rewritten_by: "",
    ...overrides,
  };
}

function baseDriftItem(overrides: Partial<NoteDriftItem> = {}): NoteDriftItem {
  return { note: baseNote(), drift: baseDrift(), ...overrides };
}

function baseDriftResult(
  items: NoteDriftItem[] = [baseDriftItem()],
  overrides: Partial<NotesDriftResult> = {},
): NotesDriftResult {
  return {
    topic: TOPIC,
    items,
    next_cursor: "",
    has_more: false,
    total_count: items.length,
    invalid_count: 0,
    ...overrides,
  };
}

function baseListResult(
  notes: NoteRecord[] = [baseNote()],
  overrides: Partial<NotesListResult> = {},
): NotesListResult {
  return {
    topic: TOPIC,
    intent_filter: "all",
    status_filter: "all",
    notes,
    intent_counts: { reflection: 0, dispute: 0, gap: 0, question: 0 },
    status_counts: {
      exact: 0,
      unanchored: 0,
      shifted: 0,
      fuzzy: 0,
      orphaned: 0,
    },
    next_cursor: "",
    has_more: false,
    total_count: notes.length,
    skipped_malformed: 0,
    ...overrides,
  };
}

function baseEnvelope(
  overrides: Partial<NoteDecisionEnvelope> = {},
): NoteDecisionEnvelope {
  return {
    mode: "dry-run",
    topic: TOPIC,
    note_id: "note-1",
    action: "reanchor",
    decision_id: "dec-1",
    summary: "Re-anchor to the resolved passage.",
    context: {},
    options: [
      {
        action: "reanchor",
        preview: "Re-anchor note-1 to the resolved passage.",
        reversible: true,
      },
    ],
    provenance: {},
    reason_required: false,
    ...overrides,
  };
}

/** Boundary fake of `ToolClient` (`dashboard/CLAUDE.md`: "the single seam for MCP calls") --
 * only the methods the merged drift stage reaches, defaulted to an empty-but-valid fixture. */
function fakeClient(
  overrides: {
    list?: NotesListResult;
    drift?: NotesDriftResult;
    reanchor?: NoteDecisionEnvelope;
    detach?: NoteDecisionEnvelope;
    archive?: NoteDecisionEnvelope;
  } = {},
) {
  const notesList = vi.fn(
    async (..._args: unknown[]) => overrides.list ?? baseListResult(),
  );
  const notesDrift = vi.fn(
    async (..._args: unknown[]) => overrides.drift ?? baseDriftResult(),
  );
  const notesReanchor = vi.fn(
    async (..._args: unknown[]) =>
      overrides.reanchor ?? baseEnvelope({ action: "reanchor" }),
  );
  const notesDetach = vi.fn(
    async (..._args: unknown[]) =>
      overrides.detach ??
      baseEnvelope({ action: "detach", summary: "Detach the anchor." }),
  );
  const notesArchive = vi.fn(
    async (..._args: unknown[]) =>
      overrides.archive ??
      baseEnvelope({ action: "archive", summary: "Archive note-1." }),
  );
  const notesPromote = vi.fn(async (..._args: unknown[]) => ({
    mode: "dry-run" as const,
    topic: TOPIC,
    note_id: "note-1",
    action: "promote" as const,
    decision_id: "dec-promote",
    summary: "Promote note-1 to the training set.",
    context: {
      question: "What does MIPRO tune?",
      pages_used: ["agentic-systems/mipro.md"],
    },
    options: [
      {
        action: "promote",
        preview: "Add a curated example.",
        reversible: false,
      },
    ],
    provenance: {},
    reason_required: false,
  }));
  const client = {
    notesList,
    notesDrift,
    notesReanchor,
    notesDetach,
    notesArchive,
    notesPromote,
  } as unknown as ToolClient;
  return {
    client,
    notesList,
    notesDrift,
    notesReanchor,
    notesDetach,
    notesArchive,
    notesPromote,
  };
}

function renderDriftStage(
  client: ToolClient,
  topic = TOPIC,
  vault = VAULT,
): HTMLElement {
  return render(<DriftStage client={client} topic={topic} vault={vault} />)
    .container as HTMLElement;
}

async function expandStage(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: /check/i }));
  await vi.waitFor(() => expect(screen.queryByText(/not checked/i)).toBeNull());
}

describe("the drift stage is collapsed by default (§2.6's honest 'not checked' state)", () => {
  it("renders the collapsed summary and never calls notesList or notesDrift on mount", () => {
    const { client, notesList, notesDrift } = fakeClient();
    const container = renderDriftStage(client);

    expect(container.textContent).toMatch(/not checked/i);
    expect(container.textContent).toMatch(/one git read per anchor/i);
    expect(notesList).not.toHaveBeenCalled();
    expect(notesDrift).not.toHaveBeenCalled();
  });

  it("still makes no anchor-resolution call after microtasks settle -- not a poll, not a hidden eager fetch", async () => {
    const { client, notesDrift } = fakeClient();
    renderDriftStage(client);

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(notesDrift).not.toHaveBeenCalled();
  });

  it("fetches the merged listing exactly once, for the given topic and vault, on [Check]/expand", async () => {
    const { client, notesList, notesDrift } = fakeClient();
    renderDriftStage(client, TOPIC, "kb-vault");

    await expandStage();

    expect(notesList).toHaveBeenCalledTimes(1);
    expect(notesDrift).toHaveBeenCalledTimes(1);
    expect(notesList.mock.calls[0]).toContain(TOPIC);
    expect(notesList.mock.calls[0]).toContain("kb-vault");
    expect(notesDrift.mock.calls[0]).toContain(TOPIC);
    expect(notesDrift.mock.calls[0]).toContain("kb-vault");
  });

  it("never calls notesDrift more than once from a single [Check] click -- ruling out an interval poll", async () => {
    const { client, notesDrift } = fakeClient();
    renderDriftStage(client);

    await expandStage();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(notesDrift).toHaveBeenCalledTimes(1);
  });
});

describe("the merged surface renders NotesDriftView's own facts for a queue-eligible anchor", () => {
  it("renders the pinned and live quotes for a reworded (fuzzy) anchor", async () => {
    const { client } = fakeClient({
      list: baseListResult([baseNote()]),
      drift: baseDriftResult([baseDriftItem()]),
    });
    const container = renderDriftStage(client);

    await expandStage();

    expect(within(container).getByText("the pinned passage")).toBeTruthy();
    expect(within(container).getByText("the reworded passage")).toBeTruthy();
  });

  it("renders the superseded-page message instead of a survival percentage when the page was replaced wholesale", async () => {
    const { client } = fakeClient({
      list: baseListResult([baseNote()]),
      drift: baseDriftResult([
        baseDriftItem({
          drift: baseDrift({
            cause: "superseded",
            live_quote: "",
            overlap: null,
            alternatives: [],
          }),
        }),
      ]),
    });
    const container = renderDriftStage(client);

    await expandStage();

    expect(container.textContent).toMatch(/replaced, not reworded/i);
  });

  it("renders alternatives as a radio group when nothing is confidently placed", async () => {
    const alt: NoteDriftAlternative = {
      page: "agentic-systems/other.md",
      heading: "New home",
      overlap: 0.8,
    };
    const { client } = fakeClient({
      list: baseListResult([baseNote()]),
      drift: baseDriftResult([
        baseDriftItem({
          drift: baseDrift({ live_quote: "", alternatives: [alt] }),
        }),
      ]),
    });
    const container = renderDriftStage(client);

    await expandStage();

    const group = within(container).getByRole("radiogroup", {
      name: /closest surviving passage/i,
    });
    expect(within(group).getByText(/other/i)).toBeTruthy();
    expect(within(group).getByText(/80% overlap/)).toBeTruthy();
  });

  it("renders the unresolvable message for an anchor-invalid item and offers Detach but not Re-anchor", async () => {
    const invalidAnchor = baseAnchor({ status: "anchor-invalid" });
    const { client } = fakeClient({
      list: baseListResult([
        baseNote({ anchors: [invalidAnchor], status: null }),
      ]),
      drift: baseDriftResult(
        [
          baseDriftItem({
            note: baseNote({ anchors: [invalidAnchor], status: null }),
            drift: baseDrift({
              cause: undefined,
              live_quote: "",
              overlap: null,
              alternatives: [],
            }),
          }),
        ],
        { invalid_count: 1 },
      ),
    });
    const container = renderDriftStage(client);

    await expandStage();

    expect(container.textContent).toMatch(/never located a page/i);
    expect(
      within(container).queryByRole("button", { name: /^re-anchor/i }),
    ).toBeNull();
    expect(
      within(container).getByRole("button", { name: /^detach$/i }),
    ).toBeTruthy();
  });
});

describe("the browse absorption is real -- a note with no drifted anchor still renders (§2.6's merge)", () => {
  it("renders a plain browse card, with archive/promote actions, for a note absent from the drift queue", async () => {
    const healthyNote = baseNote({
      note_id: "note-2",
      status: "exact",
      anchors: [baseAnchor({ status: "exact" })],
      note: "A healthy, unaffected note.",
    });
    const { client, notesDrift } = fakeClient({
      list: baseListResult([healthyNote]),
      drift: baseDriftResult([]), // empty queue -- nothing needs review
    });
    const container = renderDriftStage(client);

    await expandStage();

    expect(
      within(container).getByText("A healthy, unaffected note."),
    ).toBeTruthy();
    expect(
      within(container).getByRole("button", { name: /promote/i }),
    ).toBeTruthy();
    expect(
      within(container).getByRole("button", { name: /archive/i }),
    ).toBeTruthy();
    // The queue was empty -- no diff facts were fetched for anything to render.
    expect(notesDrift).toHaveBeenCalledTimes(1);
  });
});

describe("reanchor and detach reach exactly one preview/confirm implementation, regardless of entry point (D3)", () => {
  it("Re-anchor renders the shared confirm banner and applies through notesReanchor", async () => {
    const { client, notesReanchor } = fakeClient();
    const container = renderDriftStage(client);
    await expandStage();

    fireEvent.click(
      within(container).getByRole("button", { name: /^re-anchor/i }),
    );

    const banner = await within(container).findByRole("status");
    expect(banner.className).toMatch(/notes-action-confirm/);
    fireEvent.click(
      within(banner).getByRole("button", { name: /confirm re-anchor/i }),
    );

    await vi.waitFor(() => expect(notesReanchor).toHaveBeenCalled());
    expect(
      notesReanchor.mock.calls[notesReanchor.mock.calls.length - 1],
    ).toContain("apply");
  });

  it("Detach renders the same confirm banner shape and applies through notesDetach", async () => {
    const { client, notesDetach } = fakeClient();
    const container = renderDriftStage(client);
    await expandStage();

    fireEvent.click(
      within(container).getByRole("button", { name: /^detach$/i }),
    );

    const banner = await within(container).findByRole("status");
    expect(banner.className).toMatch(/notes-action-confirm/);
    fireEvent.click(
      within(banner).getByRole("button", { name: /confirm detach/i }),
    );

    await vi.waitFor(() => expect(notesDetach).toHaveBeenCalled());
    expect(notesDetach.mock.calls[notesDetach.mock.calls.length - 1]).toContain(
      "apply",
    );
  });

  it("says what an applied detach did instead of only re-reading the list", async () => {
    const { client } = fakeClient();
    const container = renderDriftStage(client);
    await expandStage();

    fireEvent.click(
      within(container).getByRole("button", { name: /^detach$/i }),
    );
    const banner = await within(container).findByRole("status");
    fireEvent.click(
      within(banner).getByRole("button", { name: /confirm detach/i }),
    );

    // The stage, not the card, holds the outcome: the card this ran from is
    // re-rendered from a fresh scan, so an outcome parked there would vanish
    // at the moment it needed reading.
    await within(container).findByText(
      PROCESS_META["tend.note_detach"].outcomeFallback as string,
    );
  });

  it("never shows more than one confirm banner at a time -- guards against a second dialog implementation resurfacing", async () => {
    const { client } = fakeClient({
      list: baseListResult([
        baseNote({ note_id: "note-1" }),
        baseNote({ note_id: "note-3" }),
      ]),
      drift: baseDriftResult([
        baseDriftItem({ note: baseNote({ note_id: "note-1" }) }),
      ]),
    });
    const container = renderDriftStage(client);
    await expandStage();

    fireEvent.click(
      within(container).getByRole("button", { name: /^re-anchor/i }),
    );

    await within(container).findByRole("status");
    expect(container.querySelectorAll(".notes-action-confirm")).toHaveLength(1);
  });
});

describe("Promote reuses NotePromoteDialog verbatim (never in dispute for D3)", () => {
  it("opens the promote dialog pre-filled with the note's own text", async () => {
    const { client } = fakeClient({
      list: baseListResult([
        baseNote({
          note: "A dispute about MIPRO's cost claim.",
          intent: "dispute",
        }),
      ]),
      drift: baseDriftResult([]),
    });
    const container = renderDriftStage(client);
    await expandStage();

    fireEvent.click(
      within(container).getByRole("button", { name: /promote/i }),
    );

    const dialog = await screen.findByRole("dialog", { name: /promote note/i });
    expect(
      within(dialog).getByText(/A dispute about MIPRO's cost claim\./),
    ).toBeTruthy();
  });
});
