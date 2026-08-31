import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
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
import type { Mock } from "vitest";

import type { ToolClient } from "../../../toolClient";
import type {
  GateState,
  LoopOnceResult,
  LoopPendingCandidate,
  WikiStatus,
} from "../../../types";

/**
 * `dashboard/src/lanes/improve/GateStage.tsx` does not exist yet -- this is
 * the RED half of a paired implementation/test step for
 * Improve's `gate` row. Loaded through a non-literal
 * dynamic `import()` specifier, the same device `lanes/__tests__/LaneRail.test.tsx`
 * used for its own not-yet-existing module: a literal `import { GateStage }
 * from "../GateStage"` would fail `tsc --noEmit` for the whole project the
 * moment this file lands, and a dynamic import whose argument is not a
 * string literal is left unresolved by TypeScript, so the rest of the tree
 * keeps type-checking while this file fails at *runtime* with the
 * missing-module error the paired implementation step is gated on.
 *
 * Four load-bearing assumptions the paired implementer may satisfy
 * differently (the
 * paired implementation wins on conflict):
 *
 *   1. `<GateStage client={...} topic={...} vault={...} status={...}
 *      onStatusRefresh={...} />` -- mirrors `LoopPane`'s own prop shape
 *      minus the `onOpen*` cross-lane props, which the lane split
 *      deletes rather than relocates.
 *   2. The free preview leg of the billed `loop run_once` cycle is reached
 *      through a control carrying `data-testid="gate-run-once-preview"`;
 *      the billing leg's confirm control (rendered only once a preview has
 *      landed) carries `data-testid="gate-run-once-confirm"`. These testids
 *      are this suite's own click targets, deliberately independent of
 *      `TwoPhaseAction.tsx`'s pinned internals (busy states, its own button
 *      copy) -- that primitive is already characterized in
 *      `twoPhaseAction.test.ts` and `toolClient.runOnce.test.ts`; nothing
 *      here re-asserts it, only that GateStage's own two controls drive it
 *      correctly.
 *   3. `client.loopRunOnce` is called positionally as
 *      `(topic, confirm, vault)`, exactly as `LoopPane.tsx`'s
 *      `gateCandidate` already does today -- both legs go through one call
 *      expression, so the confirm leg is provably the preview leg plus the
 *      nonce.
 *   4. `status.loop.baseline_unreachable`'s `message` field is rendered
 *      somewhere in the stage body when present.
 */

interface GateStageProps {
  client?: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}

type GateStageComponent = (props: GateStageProps) => JSX.Element;

interface GateStageModule {
  GateStage: GateStageComponent;
}

const GATE_STAGE_MODULE_PATH = "../GateStage";

let GateStage: GateStageComponent;

beforeAll(async () => {
  ({ GateStage } = (await import(GATE_STAGE_MODULE_PATH)) as GateStageModule);
});

/**
 * `PromptDiff` is a real, already-tested component -- stubbing it here is a
 * boundary mock (it reaches `client.promptDiff`, which is out of scope for
 * this suite), not a mock of the unit under test. If `GateStage` reimplements
 * its own diff rendering instead of importing and invoking the real
 * component, this stub never mounts and the assertions that look for it fail.
 */
vi.mock("../../../PromptDiff", () => ({
  PromptDiff: (props: { branch?: string | null }) => (
    <div data-testid="prompt-diff-mock" data-branch={props.branch ?? ""} />
  ),
}));

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";

function fakeClient(overrides: Partial<ToolClient> = {}): ToolClient {
  return {
    loopRunOnce: vi.fn(),
    loopRebaseline: vi.fn(),
    ...overrides,
  } as unknown as ToolClient;
}

function pendingCandidate(
  overrides: Partial<LoopPendingCandidate> = {},
): LoopPendingCandidate {
  return { branch: "loop/c/1", sha: "abc1234", pending: true, ...overrides };
}

function baseStatus(
  overrides: {
    gateState?: GateState;
    pendingCandidates?: LoopPendingCandidate[];
    baselineUnreachable?: WikiStatus["loop"]["baseline_unreachable"];
  } = {},
): WikiStatus {
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
    gate: {
      state: overrides.gateState ?? "unknown",
      baseline: 0.62,
      last_scalar: null,
    },
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
      pending_candidates: overrides.pendingCandidates ?? [pendingCandidate()],
      baseline_unreachable: overrides.baselineUnreachable ?? null,
    },
  };
}

describe("the pending-candidate diff is the real PromptDiff, not a reimplementation", () => {
  it("invokes PromptDiff for a pending candidate with the candidate's branch", () => {
    const status = baseStatus({
      pendingCandidates: [pendingCandidate({ branch: "loop/c/9f3a" })],
    });

    render(
      <GateStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={status}
      />,
    );

    const diffMock = screen.getByTestId("prompt-diff-mock");
    expect(diffMock.getAttribute("data-branch")).toBe("loop/c/9f3a");
  });
});

describe("the gate stage exposes exactly one primary billed control", () => {
  it("renders a single run-once preview trigger before any interaction", () => {
    render(
      <GateStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    expect(screen.getAllByTestId("gate-run-once-preview")).toHaveLength(1);
  });
});

describe("billing `loop run_once` is a two-phase preview then confirm", () => {
  let loopRunOnce: Mock<ToolClient["loopRunOnce"]>;

  beforeEach(() => {
    loopRunOnce = vi.fn();
  });

  function callArgs(call: unknown[]): {
    topic: unknown;
    confirm: unknown;
    vault: unknown;
  } {
    const [topic, confirm, vault] = call;
    return { topic, confirm, vault };
  }

  it("bills only on a second call, and that call is the preview call plus the nonce", async () => {
    loopRunOnce
      .mockResolvedValueOnce({
        action: "run_once",
        topic: TOPIC,
        confirm_nonce: "gate-nonce",
        estimated_cost: "$0.12",
      } satisfies LoopOnceResult)
      .mockResolvedValueOnce({
        action: "run_once",
        topic: TOPIC,
        billed: true,
        acted: true,
        message: "Gate cycle finished",
      } satisfies LoopOnceResult);

    render(
      <GateStage
        client={fakeClient({ loopRunOnce })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    fireEvent.click(screen.getByTestId("gate-run-once-preview"));
    await screen.findByTestId("gate-run-once-confirm");

    fireEvent.click(screen.getByTestId("gate-run-once-confirm"));
    await vi.waitFor(() => expect(loopRunOnce).toHaveBeenCalledTimes(2));

    const preview = callArgs(loopRunOnce.mock.calls[0]);
    const billed = callArgs(loopRunOnce.mock.calls[1]);
    expect(billed).toEqual({ ...preview, confirm: "gate-nonce" });
  });

  it("never bills on a single click -- the preview leg alone cannot reach the billing leg", async () => {
    loopRunOnce.mockResolvedValueOnce({
      action: "run_once",
      topic: TOPIC,
      confirm_nonce: "gate-nonce",
      estimated_cost: "$0.12",
    } satisfies LoopOnceResult);

    render(
      <GateStage
        client={fakeClient({ loopRunOnce })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    fireEvent.click(screen.getByTestId("gate-run-once-preview"));
    await screen.findByTestId("gate-run-once-confirm");

    expect(loopRunOnce).toHaveBeenCalledTimes(1);
    expect(callArgs(loopRunOnce.mock.calls[0]).confirm).toBe("");
  });
});

describe("an unreachable baseline is narrated, not silently gated on", () => {
  it("renders the loop's own explanation for why every candidate must fail", () => {
    const status = baseStatus({
      baselineUnreachable: {
        baseline: 0.71,
        last_scalar: 0.62,
        message:
          "The frozen bar is above anything this corpus has ever scored.",
        fix: "Rebaseline to the best or latest observation to unblock the gate.",
      },
    });

    render(
      <GateStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={status}
      />,
    );

    expect(
      screen.getByText(
        "The frozen bar is above anything this corpus has ever scored.",
        { exact: false },
      ),
    ).toBeTruthy();
  });
});
