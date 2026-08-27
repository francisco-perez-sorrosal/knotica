import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * `dashboard/src/lanes/improve/InstrumentStage.tsx` does not exist yet -- this
 * is the RED half of a paired step (IMPLEMENTATION_PLAN.md Step 69/70,
 * INTERFACE_DESIGN.md §2.4's `instrument` row). Loaded through a non-literal
 * dynamic `import()`, the same technique `laneRailState.test.ts` (Step 62) and
 * `LaneRail.test.tsx` (Step 64) established -- a literal specifier would fail
 * `tsc --noEmit` for the whole project the moment this file lands.
 *
 * `instrument` absorbs `DatasetsPane`'s inventory/bootstrap/bootstrap_train/
 * freeze plus `VaultPane`'s "Bootstrap trainset" action (§2.4's stage table).
 * Three load-bearing assumptions the paired implementer may satisfy
 * differently -- the paired implementation wins on conflict, full reasoning
 * in `LEARNINGS_test-engineer_step70.md`:
 *
 *   1. The component is invoked as `<InstrumentStage client={...} topic={...}
 *      vault={...} />` and fetches its own `datasetsInventory` on mount --
 *      mirroring `DatasetsPane`'s existing self-fetch and §2.7's "each stage
 *      resolves independently as its read lands" rule for Improve.
 *   2. **Declared test-contract adjustment (orchestrator ruling, dispatched
 *      with the paired implementation step, not this suite's original
 *      design):** the two spend-immediately billed actions absorbed here --
 *      `datasetsBootstrap` ("Bootstrap") and `datasetsBootstrapTrain`
 *      ("Bootstrap trainset") -- gate on an in-DOM two-click armed→confirm
 *      affordance, not `window.confirm()`. A sandboxed MCP-App iframe has no
 *      `allow-modals`, so a native `confirm()` can be silently suppressed and
 *      return `false`, bricking the action on Claude Desktop -- exactly the
 *      "standing design asymmetry" a Batch-P light-review flagged as "worth a
 *      two-phase upgrade when the Improve lane pane lands" (LEARNINGS.md,
 *      Step 38 disposition), now landing as an in-DOM affordance instead of
 *      the native dialog this suite originally assumed. Neither action mints
 *      a server-side `confirm_nonce` (only the two `loop` actions do), so
 *      this is the honest client-side mirror of "explicit confirmation" --
 *      the same shape `HealStage.tsx`'s `compile action=run` control already
 *      established for its own nonce-less billed action.
 *   3. A disclosure toggle (if any) carries `aria-expanded`, per the
 *      accessibility floor `LaneRail.tsx` (Step 63) already established for
 *      this codebase -- reused here as the mechanical proxy for "one nesting
 *      level, no disclosure inside a disclosure" (§2.4 rule 2).
 *
 * Golden/trainset facts and the confirmation gate are the two behaviors this
 * suite pins; `golden load/save`, `baseline_probe`, and `loop set_baseline`
 * are out of this suite's scope by design -- see the scope note in
 * `LEARNINGS_test-engineer_step70.md`.
 */

interface DatasetFileRowFixture {
  role: string;
  count: number;
  exists: boolean;
}

interface DatasetsInventoryFixture {
  topic: string;
  floor: number;
  files: DatasetFileRowFixture[];
  overlaps: {
    train_held_out: number;
    train_reviewed: number;
    train_candidates: number;
  };
  pipeline: {
    candidates_n: number;
    reviewed_n: number;
    held_out_n: number;
    seal_ok: boolean;
    freeze_ready: boolean;
  };
}

interface InstrumentClientFixture {
  datasetsInventory: (
    topic: string,
    vault?: string,
  ) => Promise<DatasetsInventoryFixture>;
  datasetsBootstrap: (
    topic: string,
    vault?: string,
  ) => Promise<{ n_candidates: number; filename: string }>;
  datasetsBootstrapTrain: (
    topic: string,
    target?: number,
    vault?: string,
  ) => Promise<{ appended: number; pages_read: number }>;
  datasetsFreeze: (
    topic: string,
    vault?: string,
  ) => Promise<{ n_frozen: number; commit_sha: string; below_floor: boolean }>;
}

type InstrumentStageComponent = (props: {
  client: InstrumentClientFixture | null;
  topic: string;
  vault: string;
}) => JSX.Element;

interface InstrumentStageModule {
  InstrumentStage: InstrumentStageComponent;
}

const INSTRUMENT_STAGE_MODULE_PATH = "../InstrumentStage";

let InstrumentStage: InstrumentStageComponent;

beforeAll(async () => {
  ({ InstrumentStage } = (await import(
    INSTRUMENT_STAGE_MODULE_PATH
  )) as InstrumentStageModule);
});

afterEach(cleanup);

function inventoryFixture(
  overrides: Partial<DatasetsInventoryFixture> = {},
): DatasetsInventoryFixture {
  return {
    topic: "agentic-systems",
    floor: 20,
    files: [{ role: "trainset", count: 62, exists: true }],
    overlaps: { train_held_out: 0, train_reviewed: 0, train_candidates: 0 },
    pipeline: {
      candidates_n: 12,
      reviewed_n: 22,
      held_out_n: 40,
      seal_ok: true,
      freeze_ready: true,
    },
    ...overrides,
  };
}

/** Untyped return (inferred) -- an explicit `InstrumentClientFixture` return
 * annotation makes TS reject `vi.fn()`'s generic mock type against the
 * interface's concrete function signatures. Callers pass the result
 * straight to `<InstrumentStage client={...} />` and to `expect(...)`
 * assertions on the individual mock functions -- both work fine against the
 * inferred shape. */
function makeClient(overrides: Partial<InstrumentClientFixture> = {}) {
  return {
    datasetsInventory: vi.fn().mockResolvedValue(inventoryFixture()),
    datasetsBootstrap: vi
      .fn()
      .mockResolvedValue({ n_candidates: 8, filename: "candidates.jsonl" }),
    datasetsBootstrapTrain: vi
      .fn()
      .mockResolvedValue({ appended: 5, pages_read: 3 }),
    datasetsFreeze: vi.fn().mockResolvedValue({
      n_frozen: 40,
      commit_sha: "abc123",
      below_floor: false,
    }),
    ...overrides,
  };
}

describe("instrument renders its facts from the mocked datasets inventory", () => {
  it("shows the sealed held-out count and the trainset count once the read lands", async () => {
    const client = makeClient({
      datasetsInventory: vi.fn().mockResolvedValue(
        inventoryFixture({
          files: [{ role: "trainset", count: 62, exists: true }],
          pipeline: {
            candidates_n: 12,
            reviewed_n: 22,
            held_out_n: 40,
            seal_ok: true,
            freeze_ready: true,
          },
        }),
      ),
    });

    render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );

    expect(await screen.findByText(/40/)).toBeTruthy();
    expect(await screen.findByText(/62/)).toBeTruthy();
    expect(client.datasetsInventory).toHaveBeenCalledWith(
      "agentic-systems",
      "main",
    );
  });

  it("keeps `Freeze golden` reachable at the top level, not behind a disclosure", async () => {
    const client = makeClient();
    render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );

    const freezeButton = await screen.findByRole("button", { name: /freeze/i });
    expect(freezeButton.closest("[aria-expanded]")).toBeNull();
  });
});

describe("the spend-immediately billed actions require an explicit second click before spending", () => {
  it("does not call datasetsBootstrap on a single click -- the first click only arms it", async () => {
    const client = makeClient();
    render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );

    const bootstrapButton = await screen.findByRole("button", {
      name: /^bootstrap$/i,
    });
    fireEvent.click(bootstrapButton);
    await Promise.resolve();

    expect(client.datasetsBootstrap).not.toHaveBeenCalled();
  });

  it("calls datasetsBootstrap only after the second, explicit confirm click", async () => {
    const client = makeClient();
    render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );

    const bootstrapButton = await screen.findByRole("button", {
      name: /^bootstrap$/i,
    });
    fireEvent.click(bootstrapButton);

    const confirmButton = await screen.findByRole("button", {
      name: /^confirm bootstrap —/i,
    });
    fireEvent.click(confirmButton);

    await vi.waitFor(() =>
      expect(client.datasetsBootstrap).toHaveBeenCalledTimes(1),
    );
    expect(client.datasetsBootstrap).toHaveBeenCalledWith(
      "agentic-systems",
      "main",
    );
  });

  it("does not call datasetsBootstrapTrain on a single click -- the first click only arms it", async () => {
    const client = makeClient();
    render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );

    const bootstrapTrainButton = await screen.findByRole("button", {
      name: /^bootstrap trainset$/i,
    });
    fireEvent.click(bootstrapTrainButton);
    await Promise.resolve();

    expect(client.datasetsBootstrapTrain).not.toHaveBeenCalled();
  });

  it("calls datasetsBootstrapTrain only after the second, explicit confirm click", async () => {
    const client = makeClient();
    render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );

    const bootstrapTrainButton = await screen.findByRole("button", {
      name: /^bootstrap trainset$/i,
    });
    fireEvent.click(bootstrapTrainButton);

    const confirmButton = await screen.findByRole("button", {
      name: /^confirm bootstrap trainset/i,
    });
    fireEvent.click(confirmButton);

    await vi.waitFor(() =>
      expect(client.datasetsBootstrapTrain).toHaveBeenCalledTimes(1),
    );
  });
});

describe("one nesting level (§2.4 rule 2)", () => {
  it("never places a disclosure toggle inside another disclosure toggle", async () => {
    const client = makeClient();
    const { container } = render(
      <InstrumentStage client={client} topic="agentic-systems" vault="main" />,
    );
    await screen.findByText(/40/);

    const toggles = Array.from(
      container.querySelectorAll<HTMLElement>("[aria-expanded]"),
    );
    const nested = toggles.some((toggle) =>
      toggles.some((other) => other !== toggle && other.contains(toggle)),
    );
    expect(nested).toBe(false);
  });
});
