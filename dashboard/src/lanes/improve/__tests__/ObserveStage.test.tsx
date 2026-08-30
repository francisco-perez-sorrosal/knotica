import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

/**
 * `dashboard/src/lanes/improve/ObserveStage.tsx` does not exist yet -- this is
 * the RED half of a paired step (IMPLEMENTATION_PLAN.md Step 69/70,
 * INTERFACE_DESIGN.md §2.4's `observe` row). Loaded through a non-literal
 * dynamic `import()`, the same technique `laneRailState.test.ts` (Step 62)
 * and `LaneRail.test.tsx` (Step 64) established.
 *
 * `uplot` is mocked the same way `loopPaneStepper.characterization.test.tsx`
 * mocks it: the real package calls `window.matchMedia` at import time, which
 * jsdom does not implement, and the mock has to be declared before this
 * file's own imports resolve (Vitest hoists `vi.mock` above them).
 *
 * `observe` absorbs `LoopPane`'s cadence controls, runner-liveness chip,
 * `loop-progress`, the scalar chart, `metrics_read`, and the billed
 * two-phase `loop run_eval` (§2.4's stage table). Load-bearing assumptions
 * the paired implementer may satisfy differently -- the paired
 * implementation wins on conflict, full reasoning in
 * `LEARNINGS_test-engineer_step70.md`:
 *
 *   1. The component is invoked as `<ObserveStage client={...} topic={...}
 *      vault={...} status={...} metrics={...} />`. `status`/`metrics` are
 *      passed down from the lane's own read (mirroring `LoopPane`'s current
 *      prop shape) rather than fetched independently by this stage, since
 *      the sibling `gate` stage (Step 71) reads the same `status.loop`
 *      object.
 *   2. Cadence is self-fetched via `client.loopCadence(topic, {}, vault)` on
 *      mount, exactly as `LoopPane.tsx` does today (a `useEffect`-driven
 *      read independent of the `status`/`metrics` props).
 *   3. The billed `run_eval` two-phase shape from `TwoPhaseAction.tsx` is
 *      reused unchanged: a preview click calls `client.loopRunEval` with an
 *      empty `confirm`, and only a second, explicit confirm click redeems
 *      the minted `confirm_nonce` -- the exact sequence
 *      `toolClient.twoPhase.test.ts` pins at the wire level, asserted here
 *      at the component boundary instead.
 *   4. A disclosure toggle (if any) carries `aria-expanded`, reusing
 *      `LaneRail.tsx`'s (Step 63) accessibility-floor convention as the
 *      mechanical proxy for "one nesting level" (§2.4 rule 2).
 *   5. The runner-liveness chip keeps `LoopPane.tsx`'s existing verbatim
 *      wording (`"runner: watching · pid <n>"` / `"runner: off"`) rather than
 *      inventing new copy for the same fact.
 */

vi.mock("uplot", () => ({ default: class {} }));

interface LoopRunnerLivenessFixture {
  alive: boolean;
  pid: number | null;
  beat_at: string | null;
  interval_seconds: number | null;
}

interface ObserveStatusFixture {
  loop: {
    stage: string | null;
    runner: LoopRunnerLivenessFixture;
    baseline_scalar: number | null;
    progress: {
      phase: string;
      current: number;
      total: number;
      detail: string;
    } | null;
  };
}

interface MetricsRecordFixture {
  generation: number;
  scalar: number;
  timestamp: string;
}

interface MetricsWindowFixture {
  topic: string;
  records: MetricsRecordFixture[];
  has_more: boolean;
}

interface LoopCadenceConfigFixture {
  topic: string;
  eval_min_interval_hours: number;
  eval_window: string;
  eval_num_threads: number;
  arena_scorer: string;
}

interface LoopRunEvalResultFixture {
  action: "run_eval";
  topic: string;
  worker: string;
  judge: string;
  num_threads: number;
  estimated_cost?: string;
  confirm_nonce?: string;
  ttl?: number;
  billed?: boolean;
  acted?: boolean;
  scalar?: number | null;
  message?: string;
}

interface ObserveClientFixture {
  loopCadence: (
    topic: string,
    overrides?: Record<string, unknown>,
    vault?: string,
  ) => Promise<LoopCadenceConfigFixture>;
  loopRunEval: (
    topic: string,
    confirm?: string,
    numThreads?: number,
    vault?: string,
  ) => Promise<LoopRunEvalResultFixture>;
}

type ObserveStageComponent = (props: {
  client: ObserveClientFixture | null;
  topic: string;
  vault: string;
  status: ObserveStatusFixture | null;
  metrics: MetricsWindowFixture | null;
}) => JSX.Element;

interface ObserveStageModule {
  ObserveStage: ObserveStageComponent;
}

const OBSERVE_STAGE_MODULE_PATH = "../ObserveStage";

let ObserveStage: ObserveStageComponent;

beforeAll(async () => {
  ({ ObserveStage } = (await import(
    OBSERVE_STAGE_MODULE_PATH
  )) as ObserveStageModule);
});

afterEach(cleanup);

function statusFixture(
  overrides: Partial<ObserveStatusFixture["loop"]> = {},
): ObserveStatusFixture {
  return {
    loop: {
      stage: "idle",
      runner: {
        alive: true,
        pid: 4242,
        beat_at: "2026-08-27T00:00:00Z",
        interval_seconds: 21600,
      },
      baseline_scalar: 0.62,
      progress: null,
      ...overrides,
    },
  };
}

function metricsFixture(
  overrides: Partial<MetricsWindowFixture> = {},
): MetricsWindowFixture {
  return {
    topic: "agentic-systems",
    records: [
      { generation: 12, scalar: 0.66, timestamp: "2026-08-27T00:00:00Z" },
    ],
    has_more: false,
    ...overrides,
  };
}

function cadenceFixture(
  overrides: Partial<LoopCadenceConfigFixture> = {},
): LoopCadenceConfigFixture {
  return {
    topic: "agentic-systems",
    eval_min_interval_hours: 6,
    eval_window: "7d",
    eval_num_threads: 4,
    arena_scorer: "heuristic",
    ...overrides,
  };
}

/** Untyped return (inferred) -- an explicit `ObserveClientFixture` return
 * annotation makes TS reject `vi.fn()`'s generic mock type against the
 * interface's concrete function signatures. Callers pass the result
 * straight to `<ObserveStage client={...} />` and to `expect(...)`
 * assertions on the individual mock functions -- both work fine against the
 * inferred shape. */
function makeClient(overrides: Partial<ObserveClientFixture> = {}) {
  return {
    loopCadence: vi.fn().mockResolvedValue(cadenceFixture()),
    loopRunEval: vi.fn().mockResolvedValue({
      action: "run_eval",
      topic: "agentic-systems",
      worker: "w",
      judge: "j",
      num_threads: 4,
      estimated_cost: "$0.40",
      confirm_nonce: "eval-nonce",
      ttl: 300,
    }),
    ...overrides,
  };
}

describe("observe renders its facts from the mocked status, metrics, and cadence", () => {
  it("shows the latest generation and scalar from the metrics window", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    expect(await screen.findByText(/12/)).toBeTruthy();
    expect(await screen.findByText(/0\.66/)).toBeTruthy();
  });

  /**
   * Asserts the rendered cadence readouts by their exact text, not a bare
   * `/6/`. The bare-digit form was vacuous: it resolved against the latest
   * scalar (`0.66`) on the first `waitFor` tick, before `setCadence` had
   * re-rendered anything, and so would have passed with the cadence display
   * deleted outright. All three cadence facts are checked, so a regression
   * that drops the window or the default-thread count fails here too.
   */
  it("shows the cadence once client.loopCadence resolves", async () => {
    const client = makeClient({
      loopCadence: vi
        .fn()
        .mockResolvedValue(cadenceFixture({ eval_min_interval_hours: 6 })),
    });
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    await vi.waitFor(() => expect(client.loopCadence).toHaveBeenCalled());
    expect(await screen.findByText("every 6h")).toBeTruthy();
    expect(await screen.findByText("7d")).toBeTruthy();
    expect(await screen.findByText("4")).toBeTruthy();
  });

  it("renders `runner: watching · pid <n>` when status.loop.runner.alive is true", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture({
          runner: {
            alive: true,
            pid: 4242,
            beat_at: null,
            interval_seconds: null,
          },
        })}
        metrics={metricsFixture()}
      />,
    );

    expect(await screen.findByText(/runner: watching/i)).toBeTruthy();
    expect(await screen.findByText(/4242/)).toBeTruthy();
  });

  it('renders "runner: off" when status.loop.runner.alive is false', async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture({
          runner: {
            alive: false,
            pid: null,
            beat_at: null,
            interval_seconds: null,
          },
        })}
        metrics={metricsFixture()}
      />,
    );

    expect(await screen.findByText(/runner: off/i)).toBeTruthy();
  });
});

describe("the billed `run_eval` control never bills on a single click", () => {
  it("previews only -- one click calls loopRunEval with an empty confirm and nothing else", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    const runButton = await screen.findByRole("button", {
      name: /run eval now/i,
    });
    fireEvent.click(runButton);

    await vi.waitFor(() => expect(client.loopRunEval).toHaveBeenCalledTimes(1));
    expect(client.loopRunEval).toHaveBeenCalledWith(
      "agentic-systems",
      "",
      expect.anything(),
      "main",
    );
  });

  it("bills only after an explicit second confirm, redeeming the nonce the preview minted", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    const runButton = await screen.findByRole("button", {
      name: /run eval now/i,
    });
    fireEvent.click(runButton);
    await vi.waitFor(() => expect(client.loopRunEval).toHaveBeenCalledTimes(1));

    const confirmButton = await screen.findByRole("button", {
      name: /confirm/i,
    });
    fireEvent.click(confirmButton);

    await vi.waitFor(() => expect(client.loopRunEval).toHaveBeenCalledTimes(2));
    const loopRunEvalMock = client.loopRunEval as unknown as ReturnType<
      typeof vi.fn
    >;
    const [, firstConfirm] = loopRunEvalMock.mock.calls[0];
    const [, secondConfirm] = loopRunEvalMock.mock.calls[1];
    expect(firstConfirm).toBe("");
    expect(secondConfirm).toBe("eval-nonce");
  });
});

describe("the arena scorer is switchable in place, asymmetrically guarded", () => {
  it("prints the resolved scorer as a stat", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    expect(await screen.findByText("heuristic")).toBeTruthy();
  });

  it("never writes on the first click -- arming only relabels the control", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    await vi.waitFor(() => expect(client.loopCadence).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByTestId("observe-arena-scorer"));

    // Still only the read-only mount call.
    expect(client.loopCadence).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: /future races bill per variant/i }),
    ).toBeTruthy();
  });

  it("writes arena_scorer=eval only after the second, explicit confirm", async () => {
    const client = makeClient();
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    await vi.waitFor(() => expect(client.loopCadence).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByTestId("observe-arena-scorer"));
    fireEvent.click(screen.getByTestId("observe-arena-scorer"));

    await vi.waitFor(() => expect(client.loopCadence).toHaveBeenCalledTimes(2));
    const loopCadenceMock = client.loopCadence as unknown as ReturnType<
      typeof vi.fn
    >;
    expect(loopCadenceMock.mock.calls[1]).toEqual([
      "agentic-systems",
      { arenaScorer: "eval" },
      "main",
    ]);
  });

  it("switches back to the heuristic on a single click -- going free needs no guard", async () => {
    const client = makeClient({
      loopCadence: vi
        .fn()
        .mockResolvedValue(cadenceFixture({ arena_scorer: "eval" })),
    });
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    // Wait for the mount read to *land*, not merely to fire -- the offered
    // direction is derived from the resolved value.
    fireEvent.click(
      await screen.findByRole("button", { name: /use heuristic scorer/i }),
    );

    await vi.waitFor(() => expect(client.loopCadence).toHaveBeenCalledTimes(2));
    const loopCadenceMock = client.loopCadence as unknown as ReturnType<
      typeof vi.fn
    >;
    expect(loopCadenceMock.mock.calls[1][1]).toEqual({
      arenaScorer: "heuristic",
    });
  });

  it("reports a rejected write instead of silently showing the old value", async () => {
    const loopCadence = vi
      .fn()
      .mockResolvedValueOnce(cadenceFixture())
      .mockRejectedValueOnce(new Error("[loop] arena_scorer must be one of"));
    const client = makeClient({ loopCadence });
    render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );

    await vi.waitFor(() => expect(loopCadence).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByTestId("observe-arena-scorer"));
    fireEvent.click(screen.getByTestId("observe-arena-scorer"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("arena_scorer must be one of");
  });
});

describe("one nesting level (§2.4 rule 2)", () => {
  it("never places a disclosure toggle inside another disclosure toggle", async () => {
    const client = makeClient();
    const { container } = render(
      <ObserveStage
        client={client}
        topic="agentic-systems"
        vault="main"
        status={statusFixture()}
        metrics={metricsFixture()}
      />,
    );
    await screen.findByText(/12/);

    const toggles = Array.from(
      container.querySelectorAll<HTMLElement>("[aria-expanded]"),
    );
    const nested = toggles.some((toggle) =>
      toggles.some((other) => other !== toggle && other.contains(toggle)),
    );
    expect(nested).toBe(false);
  });
});
