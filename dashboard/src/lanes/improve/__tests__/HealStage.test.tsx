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
    // The mount read goes through the read-only seam; `loopCadence` below is
    // the write the scorer switch clicks (`td-059`).
    loopCadenceRead: vi.fn().mockResolvedValue({
      topic: TOPIC,
      eval_min_interval_hours: 0,
      eval_window: "",
      eval_num_threads: 4,
      arena_scorer: "heuristic",
    }),
    loopCadence: vi
      .fn()
      .mockImplementation((_topic, writeArgs, _vault, confirm) => {
        const requested = (writeArgs as { arenaScorer?: string } | undefined)
          ?.arenaScorer;
        // The server's spend gate: `eval` with no nonce writes nothing and
        // quotes; everything else applies in one call.
        if (requested === "eval" && !confirm) {
          return Promise.resolve({
            action: "cadence",
            topic: TOPIC,
            arena_scorer: "heuristic",
            requested_arena_scorer: "eval",
            estimated_cost: "~1 full golden-set eval per raced variant",
            confirm_nonce: "scorer-nonce",
            ttl: 300,
            message: "nothing was written",
          });
        }
        return Promise.resolve({
          topic: TOPIC,
          eval_min_interval_hours: 0,
          eval_window: "",
          eval_num_threads: 4,
          // Echo semantics, like the server: a read reports heuristic until a
          // write lands; a write echoes the resolved value back.
          arena_scorer: requested ?? "heuristic",
        });
      }),
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

describe("an aborted race explains itself and names the next step", () => {
  function abortedStatus(): ArenaStatus {
    return fakeArenaStatus({
      stage: "aborted",
      scorer_id: "heuristic-keyword",
      message:
        "arena aborted: scorer 'heuristic-keyword' does not produce eval-comparable scalars",
      variants: [
        { id: "v1", label: "variant-1", scalar: null, status: "pending" },
        { id: "v2", label: "variant-2", scalar: null, status: "pending" },
      ],
    });
  }

  it("renders the server's abort reason verbatim and the config next step", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(abortedStatus()),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    const reason = await screen.findByTestId("heal-abort-reason");
    expect(reason.textContent).toContain(
      "does not produce eval-comparable scalars",
    );
    // The remediation is a control, with the hand-edit it performs still
    // named, and the prerequisites that stop it silently falling back.
    expect(screen.getByTestId("heal-arena-scorer")).toBeTruthy();
    expect(
      screen.getByText(/arena_scorer = "eval"/, { exact: false }),
    ).toBeTruthy();
    expect(
      screen.getByText(/frozen golden set/i, { exact: false }),
    ).toBeTruthy();
  });

  it("renders no abort card while the race is merely racing", async () => {
    const client = fakeClient();

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");
    expect(screen.queryByTestId("heal-abort-reason")).toBeNull();
    expect(screen.queryByTestId("heal-arena-scorer")).toBeNull();
    expect(screen.queryByText(/arena_scorer = "eval"/)).toBeNull();
  });

  it("never writes the config on the first click -- arming only relabels the control", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(abortedStatus()),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    fireEvent.click(await screen.findByTestId("heal-arena-scorer"));

    // The stage's open read does not even reach this method any more (it is
    // `loopCadenceRead`'s), and the arm click's FREE leg is sanctioned --
    // what must not happen before the second click is a call carrying the
    // nonce, which is the only thing that writes.
    const loopCadence = client.loopCadence as unknown as ReturnType<
      typeof vi.fn
    >;
    expect(loopCadence.mock.calls.filter((call) => call[3])).toHaveLength(0);
    expect(
      await screen.findByRole("button", {
        name: /future races bill per variant/i,
      }),
    ).toBeTruthy();
  });

  it("writes arena_scorer=eval only after the second, explicit confirm", async () => {
    const onStatusRefresh = vi.fn();
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(abortedStatus()),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
        onStatusRefresh={onStatusRefresh}
      />,
    );

    fireEvent.click(await screen.findByTestId("heal-arena-scorer"));
    // The arm click is a server round trip now (the free quote leg), so the
    // confirm is only clickable once that envelope has landed.
    await screen.findByRole("button", {
      name: /future races bill per variant/i,
    });
    fireEvent.click(screen.getByTestId("heal-arena-scorer"));

    const loopCadence = client.loopCadence as unknown as ReturnType<
      typeof vi.fn
    >;
    await vi.waitFor(() =>
      expect(loopCadence.mock.calls.filter((call) => call[3])).toHaveLength(1),
    );
    const writeCall = loopCadence.mock.calls.find((call) => call[3]);
    // The write leg is the arm leg plus the server's nonce -- nothing else.
    expect(writeCall).toEqual([
      TOPIC,
      { arenaScorer: "eval" },
      VAULT,
      "scorer-nonce",
    ]);
    await vi.waitFor(() => expect(onStatusRefresh).toHaveBeenCalled());
  });

  it("flips the control and states the standing config once the switch lands", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(abortedStatus()),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    fireEvent.click(await screen.findByTestId("heal-arena-scorer"));
    // The arm click is a server round trip now (the free quote leg), so the
    // confirm is only clickable once that envelope has landed.
    await screen.findByRole("button", {
      name: /future races bill per variant/i,
    });
    fireEvent.click(screen.getByTestId("heal-arena-scorer"));

    // The click's outcome is visible as CHANGED STATE, not only a note: the
    // standing-config line appears and the control now offers the revert.
    const standing = await screen.findByTestId("heal-scorer-configured");
    expect(standing.textContent).toContain("already configured");
    expect(
      screen.getByRole("button", { name: /use heuristic scorer/i }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Use eval scorer" }),
    ).toBeNull();
  });

  it("does not urge a switch that already happened elsewhere", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(abortedStatus()),
      loopCadenceRead: vi.fn().mockResolvedValue({
        topic: TOPIC,
        eval_min_interval_hours: 0,
        eval_window: "",
        eval_num_threads: 4,
        arena_scorer: "eval",
      }),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    const standing = await screen.findByTestId("heal-scorer-configured");
    expect(standing.textContent).toContain("already configured");
    expect(
      screen.queryByRole("button", { name: "Use eval scorer" }),
    ).toBeNull();
  });

  it("re-reads the arena once the scorer has been switched", async () => {
    const arenaStatus = vi.fn().mockResolvedValue(abortedStatus());
    const client = fakeClient({ arenaStatus });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    fireEvent.click(await screen.findByTestId("heal-arena-scorer"));
    // The arm click is a server round trip now (the free quote leg), so the
    // confirm is only clickable once that envelope has landed.
    await screen.findByRole("button", {
      name: /future races bill per variant/i,
    });
    fireEvent.click(screen.getByTestId("heal-arena-scorer"));

    await vi.waitFor(() => expect(arenaStatus).toHaveBeenCalledTimes(2));
  });

  it("surfaces a rejected write instead of reporting a switch that did not happen", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(abortedStatus()),
      loopCadence: vi
        .fn()
        .mockRejectedValue(new Error("[loop] arena_scorer must be one of")),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    fireEvent.click(await screen.findByTestId("heal-arena-scorer"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("arena_scorer must be one of");
  });
});

describe("the arena card carries the race's instrument and each variant's provenance", () => {
  it("shows the baseline and the scorer as stats", async () => {
    const client = fakeClient({
      arenaStatus: vi
        .fn()
        .mockResolvedValue(
          fakeArenaStatus({ scorer_id: "eval", n_examples: 40 }),
        ),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");
    expect(screen.getByText("0.6200")).toBeTruthy();
    expect(screen.getByText("eval · 40 q")).toBeTruthy();
  });

  it("opens a variant's overlay onto that variant's own scalar provenance, and leads with the honest absent-change copy when change_summary is null", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(
        fakeArenaStatus({
          variants: [
            {
              id: "v1",
              label: "variant-1",
              scalar: 0.6421,
              status: "scored",
              scorer_id: "eval",
              n_examples: 40,
              // No `change_summary`/`diff` — this race predates change
              // tracking (or the current in-flight race that shipped it).
            },
          ],
        }),
      ),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");
    fireEvent.click(
      screen.getByRole("button", { name: "variant-1 — what this means" }),
    );
    const note = screen.getByRole("note");
    expect(note.textContent).toContain(
      "Recorded before change tracking — what this variant tried was not kept",
    );
    expect(note.textContent).toContain(
      "Scored 0.6421 by eval over 40 golden questions",
    );
  });

  it("opens a variant's overlay leading with the change_summary when the wire carries one", async () => {
    // A distinct variant id ("v2") from the sibling test above -- the
    // `TermHint` open signal is module-level and keyed by
    // `heal-variant-${id}`, so reusing "v1" here would read the prior
    // test's still-open panel instead of this test's own click.
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(
        fakeArenaStatus({
          variants: [
            {
              id: "v2",
              label: "variant-2",
              scalar: 0.82,
              status: "scored",
              scorer_id: "eval",
              n_examples: 40,
              change_summary:
                "+3 / -0 lines vs the current prompt — first change: '## Tighter answers'",
            },
          ],
        }),
      ),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");
    fireEvent.click(
      screen.getByRole("button", { name: "variant-2 — what this means" }),
    );
    const note = screen.getByRole("note");
    expect(note.textContent).toContain(
      "Tries: +3 / -0 lines vs the current prompt — first change: '## Tighter answers'.",
    );
    expect(note.textContent).not.toContain("Recorded before change tracking");
  });

  it("renders a diff toggle only for variants the wire ships a diff for, and opens the diff text on click", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(
        fakeArenaStatus({
          variants: [
            {
              id: "v1",
              label: "variant-1",
              scalar: 0.82,
              status: "scored",
              diff: "--- query.md (current)\n+++ variant\n@@\n+## Tighter answers",
            },
            {
              id: "v2",
              label: "variant-2",
              scalar: 0.7,
              status: "lost",
              // No `diff` — no toggle should render for this row.
            },
          ],
        }),
      ),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");

    expect(
      screen.getByRole("button", { name: "Show variant-1's diff" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Show variant-2's diff" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Hide variant-2's diff" }),
    ).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Show variant-1's diff" }),
    );

    expect(
      screen.getByRole("button", { name: "Hide variant-1's diff" }),
    ).toBeTruthy();
    expect(screen.getByText(/## Tighter answers/)).toBeTruthy();
  });

  it("keeps at most one variant's diff panel open at a time", async () => {
    const client = fakeClient({
      arenaStatus: vi.fn().mockResolvedValue(
        fakeArenaStatus({
          variants: [
            {
              id: "v1",
              label: "variant-1",
              scalar: 0.82,
              status: "scored",
              diff: "+first variant diff",
            },
            {
              id: "v2",
              label: "variant-2",
              scalar: 0.7,
              status: "lost",
              diff: "+second variant diff",
            },
          ],
        }),
      ),
    });

    render(
      <HealStage
        client={client}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus("fail")}
      />,
    );

    await screen.findByTestId("heal-compile-run");

    fireEvent.click(
      screen.getByRole("button", { name: "Show variant-1's diff" }),
    );
    expect(screen.getByText(/first variant diff/)).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "Show variant-2's diff" }),
    );
    expect(screen.getByText(/second variant diff/)).toBeTruthy();
    expect(screen.queryByText(/first variant diff/)).toBeNull();
  });
});
