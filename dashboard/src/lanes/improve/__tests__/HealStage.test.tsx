import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ToolClient } from "../../../toolClient";
import type {
  ArenaHistory,
  ArenaStatus,
  GateState,
  WikiStatus,
} from "../../../types";

/**
 * `dashboard/src/lanes/improve/HealStage.tsx` does not exist yet -- this is
 * the RED half of a paired implementation/test step for `INTERFACE_DESIGN.md`
 * §2.4's `heal` row. Loaded through a non-literal
 * dynamic `import()` specifier for the same reason `GateStage.test.tsx`
 * (this file's sibling) uses one: a literal import of a module that
 * does not exist yet would fail `tsc --noEmit` for the whole project.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (recorded in full in `LEARNINGS_test-engineer_step72.md`; the paired
 * implementation wins on conflict):
 *
 *   1. `<HealStage client={...} topic={...} vault={...} status={...}
 *      onStatusRefresh={...} />` -- mirrors `ArenaPane`'s own prop shape
 *      (the absorbed surface) minus the `onOpen*` cross-lane props, deleted
 *      per `INTERFACE_DESIGN.md` §2.0 clause 3.
 *   2. `heal` "opens" -- fetches arena data and offers the compile control
 *      -- exactly when `status.gate.state === "fail"` ("the gate has
 *      refused"), and stays closed for every other `GateState`. This is
 *      the plan's own framing ("opening only when the gate refuses"), read
 *      against the one field the wire contract already exposes for it.
 *   3. The one spend-immediately control this stage owns
 *      (`compile action=run`, absorbed from `CompilePanel.tsx`) carries
 *      `data-testid="heal-compile-run"`. Unlike the gate's `run_once`, a
 *      compile run is a single billed call with no free preview leg
 *      (`CompilePanel.tsx`'s own `startCompile` calls `client.compileRun`
 *      directly) -- the explicit-confirmation floor here is "a click must
 *      happen", not a second wire call.
 */

interface HealStageProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}

type HealStageComponent = (props: HealStageProps) => JSX.Element;

interface HealStageModule {
  HealStage: HealStageComponent;
}

const HEAL_STAGE_MODULE_PATH = "../HealStage";

let HealStage: HealStageComponent;

beforeAll(async () => {
  ({ HealStage } = (await import(HEAL_STAGE_MODULE_PATH)) as HealStageModule);
});

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

function fakeArenaStatus(overrides: Partial<ArenaStatus> = {}): ArenaStatus {
  return {
    schema_version: 1,
    topic: TOPIC,
    race_id: "race-1",
    stage: "racing",
    baseline_scalar: 0.62,
    variants: [
      { id: "v1", label: "variant-1", scalar: 0.64, status: "scored" },
    ],
    winner_id: null,
    winner_scalar: null,
    candidate_branch: "loop/c/1",
    message: null,
    ...overrides,
  };
}

function fakeArenaHistory(): ArenaHistory {
  return { topic: TOPIC, races: [], limit: 12 };
}

function fakeClient(overrides: Partial<ToolClient> = {}): ToolClient {
  return {
    arenaStatus: vi.fn().mockResolvedValue(fakeArenaStatus()),
    arenaHistory: vi.fn().mockResolvedValue(fakeArenaHistory()),
    compileRun: vi.fn(),
    ...overrides,
  } as unknown as ToolClient;
}

function baseStatus(gateState: GateState): WikiStatus {
  return {
    schema_version: 1,
    vault: VAULT,
    vault_name: VAULT,
    vault_path: "/tmp/vault",
    default_vault: VAULT,
    available_vaults: [],
    compile_ready_threshold: 20,
    topics: [],
    totals: { topics: 0, pages: 0, curated: 0, lint_violations: 0 },
    last_lint: null,
    unpushed: null,
    gate: { state: gateState, baseline: 0.62, last_scalar: null },
    llm: { available: false, mode: null },
    loop: {
      runner: {
        alive: false,
        pid: null,
        beat_at: null,
        interval_seconds: null,
      },
      stage: "idle",
      baseline_frozen: true,
      pending_candidates: [],
    },
  };
}

describe("heal opens only once the gate has refused (never independently reachable)", () => {
  it("renders no compile control and fetches no arena data while the gate has not refused", async () => {
    const arenaStatus = vi.fn().mockResolvedValue(fakeArenaStatus());
    const arenaHistory = vi.fn().mockResolvedValue(fakeArenaHistory());
    const client = fakeClient({ arenaStatus, arenaHistory });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("pass")}
      />,
    );

    // Let any microtask an eager effect might have queued settle, then prove
    // none of them reached the arena or the compile boundary.
    await Promise.resolve();
    await Promise.resolve();

    expect(screen.queryByTestId("heal-compile-run")).toBeNull();
    expect(arenaStatus).not.toHaveBeenCalled();
    expect(arenaHistory).not.toHaveBeenCalled();
  });

  it("opens the absorbed arena view and offers the compile control once the gate has refused", async () => {
    const arenaStatus = vi.fn().mockResolvedValue(fakeArenaStatus());
    const arenaHistory = vi.fn().mockResolvedValue(fakeArenaHistory());
    const client = fakeClient({ arenaStatus, arenaHistory });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");

    expect(arenaStatus).toHaveBeenCalled();
    expect(arenaHistory).toHaveBeenCalled();
  });
});

describe("compile is a spend-immediately action gated on an explicit click, never a silent trigger", () => {
  /**
   * Declared adjustment (Step 71, per the orchestrator's no-native-dialogs
   * ruling in `LEARNINGS.md`): `compile action=run` has no server-side nonce
   * cycle to piggyback the confirmation on, so it gates on an in-DOM two-click
   * armed→confirm affordance rather than a single click. The original RED
   * assumption ("a click must happen" == exactly one click bills) predates
   * that ruling; this suite now asserts the stronger property the ruling
   * demands — a single click never bills, and only a second, explicit click
   * on the same control redeems it. `data-testid="heal-compile-run"` (the
   * paired test-engineer's own load-bearing assumption) is unchanged.
   */
  it("does not call compileRun merely from mounting once the gate has refused", async () => {
    const compileRun = vi.fn().mockResolvedValue({
      topic: TOPIC,
      branch: "compile/1",
      stage: "completed",
      message: "done",
      scalar_before: 0.6,
      scalar_after: 0.66,
      train_n: 40,
      golden_n: 40,
    });
    const client = fakeClient({ compileRun });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );
    await screen.findByTestId("heal-compile-run");

    expect(compileRun).not.toHaveBeenCalled();
  });

  it("never bills on a single click -- the first click only arms the control", async () => {
    const compileRun = vi.fn().mockResolvedValue({
      topic: TOPIC,
      branch: "compile/1",
      stage: "completed",
      message: "done",
      scalar_before: 0.6,
      scalar_after: 0.66,
      train_n: 40,
      golden_n: 40,
    });
    const client = fakeClient({ compileRun });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );
    const compileButton = await screen.findByTestId("heal-compile-run");

    fireEvent.click(compileButton);

    expect(compileRun).not.toHaveBeenCalled();
  });

  it("reaches compileRun only after the control is armed then explicitly confirmed", async () => {
    const compileRun = vi.fn().mockResolvedValue({
      topic: TOPIC,
      branch: "compile/1",
      stage: "completed",
      message: "done",
      scalar_before: 0.6,
      scalar_after: 0.66,
      train_n: 40,
      golden_n: 40,
    });
    const client = fakeClient({ compileRun });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );
    const compileButton = await screen.findByTestId("heal-compile-run");

    fireEvent.click(compileButton);
    fireEvent.click(screen.getByTestId("heal-compile-run"));
    await vi.waitFor(() => expect(compileRun).toHaveBeenCalledTimes(1));

    expect(compileRun.mock.calls[0][0]).toBe(TOPIC);
    expect(compileRun.mock.calls[0][1]).toBe(VAULT);
  });
});
