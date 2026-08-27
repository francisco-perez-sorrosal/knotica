import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoopPane } from "../LoopPane";
import type { ArenaStage, LoopPendingCandidate, LoopStage, WikiStatus } from "../types";

/**
 * `uPlot` calls `window.matchMedia` at *import* time (module top level), which
 * jsdom does not implement -- unrelated to any chart actually rendering here
 * (every fixture below has an empty scalar history, so the chart effect's own
 * `chartRecords.length === 0` guard skips it). `uPlot` is a third-party
 * charting library the stepper under test does not otherwise touch, so
 * stubbing it at the module boundary is a boundary mock, not a mock of the
 * unit under test. Vitest hoists `vi.mock` calls above the imports above,
 * regardless of where they appear textually, so this runs before `LoopPane`
 * resolves the real `uplot` package.
 */
vi.mock("uplot", () => ({ default: class {} }));

/**
 * M3 characterization net for `LoopPane`'s four `healSteps` (Observe / Gate /
 * Heal / Merged) `ready`/`current` derivations, pinned exactly as they exist
 * today, BEFORE the M3 dissolution rewrites this stepper against the new
 * lane-rail contract. These are not new requirements -- they are a
 * regression guard: every assertion here must still pass unmodified today,
 * and every one of them is expected to start failing once Steps 65-76 land
 * (that failure is the signal the dissolution changed *something* the rail
 * contract intends to change).
 *
 * One case deliberately pins a combination the new rail's R5 invariant
 * (exactly one active-or-blocked stage) will forbid: Observe reporting
 * `current` while Heal simultaneously reports `ready`. Today's inline
 * per-step derivation allows this because each step computes its own
 * `ready`/`current` independently, with no sequence-wide invariant enforcing
 * "only one active stage" -- pinning it here gives the rail's later
 * regression test something concrete to fail against if that invariant is
 * ever silently dropped.
 */

afterEach(cleanup);

function minimalStatus(overrides: {
  stage?: LoopStage;
  arenaStage?: ArenaStage | null;
  baselineFrozen?: boolean;
  pendingCandidates?: LoopPendingCandidate[];
}): WikiStatus {
  return {
    schema_version: 1,
    vault: "main",
    vault_name: "main",
    vault_path: "/tmp/vault",
    default_vault: "main",
    available_vaults: [],
    compile_ready_threshold: 20,
    topics: [],
    totals: { topics: 0, pages: 0, curated: 0, lint_violations: 0 },
    last_lint: null,
    unpushed: null,
    gate: { state: "unknown", baseline: null, last_scalar: null },
    llm: { available: false, mode: null },
    loop: {
      runner: { alive: false, pid: null, beat_at: null, interval_seconds: null },
      stage: overrides.stage ?? "idle",
      arena_stage: overrides.arenaStage ?? "idle",
      baseline_frozen: overrides.baselineFrozen ?? false,
      pending_candidates: overrides.pendingCandidates ?? [],
    },
  };
}

function renderStepper(status: WikiStatus) {
  render(<LoopPane status={status} metrics={null} topic="agentic-systems" />);
}

/** The `<li class="heal-step ...">` ancestor of a step's title text. */
function healStep(title: "Observe" | "Gate" | "Heal" | "Merged"): HTMLElement {
  const heading = screen.getByText(title, { selector: "strong" });
  const step = heading.closest("li.heal-step");
  if (!step) throw new Error(`no li.heal-step ancestor for "${title}"`);
  return step as HTMLElement;
}

describe("the Observe step", () => {
  it("is ready once the baseline is frozen", () => {
    renderStepper(minimalStatus({ baselineFrozen: true, stage: "idle" }));

    expect(healStep("Observe").classList.contains("ready")).toBe(true);
  });

  it("is not ready before any baseline has been frozen", () => {
    renderStepper(minimalStatus({ baselineFrozen: false, stage: "idle" }));

    expect(healStep("Observe").classList.contains("ready")).toBe(false);
  });

  it("is current while an eval is running, even with a frozen baseline", () => {
    renderStepper(minimalStatus({ baselineFrozen: true, stage: "evaluating" }));

    expect(healStep("Observe").classList.contains("current")).toBe(true);
  });

  it("is current before any baseline is frozen, regardless of loop stage", () => {
    renderStepper(minimalStatus({ baselineFrozen: false, stage: "idle" }));

    expect(healStep("Observe").classList.contains("current")).toBe(true);
  });

  it("is not current once frozen and idle", () => {
    renderStepper(minimalStatus({ baselineFrozen: true, stage: "idle" }));

    expect(healStep("Observe").classList.contains("current")).toBe(false);
  });
});

describe("the Gate step", () => {
  it("is ready as soon as a candidate is pending", () => {
    renderStepper(
      minimalStatus({
        baselineFrozen: true,
        pendingCandidates: [{ branch: "loop/c/1", sha: "abc1234", pending: true }],
      }),
    );

    expect(healStep("Gate").classList.contains("ready")).toBe(true);
  });

  it("is not ready with no pending candidate", () => {
    renderStepper(minimalStatus({ baselineFrozen: true, pendingCandidates: [] }));

    expect(healStep("Gate").classList.contains("ready")).toBe(false);
  });

  it("is current once frozen with nothing pending -- the watcher is waiting for a candidate", () => {
    renderStepper(minimalStatus({ baselineFrozen: true, pendingCandidates: [] }));

    expect(healStep("Gate").classList.contains("current")).toBe(true);
  });

  it("is not current while a candidate is pending", () => {
    renderStepper(
      minimalStatus({
        baselineFrozen: true,
        pendingCandidates: [{ branch: "loop/c/1", sha: "abc1234", pending: true }],
      }),
    );

    expect(healStep("Gate").classList.contains("current")).toBe(false);
  });
});

describe("the Heal step", () => {
  it("is ready while the arena is racing", () => {
    renderStepper(minimalStatus({ stage: "racing", arenaStage: "racing" }));

    expect(healStep("Heal").classList.contains("ready")).toBe(true);
  });

  it("is ready once healed via the loop stage reaching 'passed'", () => {
    renderStepper(minimalStatus({ stage: "passed", arenaStage: "idle" }));

    expect(healStep("Heal").classList.contains("ready")).toBe(true);
  });

  it("is ready once healed via the arena stage reaching 'completed'", () => {
    renderStepper(minimalStatus({ stage: "idle", arenaStage: "completed" }));

    expect(healStep("Heal").classList.contains("ready")).toBe(true);
  });

  it("is ready whenever the arena is live, even in a non-racing, non-healed stage", () => {
    renderStepper(minimalStatus({ stage: "idle", arenaStage: "reverted" }));

    expect(healStep("Heal").classList.contains("ready")).toBe(true);
  });

  it("is not ready when the arena has never engaged", () => {
    renderStepper(minimalStatus({ stage: "idle", arenaStage: "idle" }));

    expect(healStep("Heal").classList.contains("ready")).toBe(false);
  });

  it("is current while racing", () => {
    renderStepper(minimalStatus({ stage: "racing", arenaStage: "racing" }));

    expect(healStep("Heal").classList.contains("current")).toBe(true);
  });

  it("is current while the arena is promoting a winner", () => {
    renderStepper(minimalStatus({ stage: "idle", arenaStage: "promoting" }));

    expect(healStep("Heal").classList.contains("current")).toBe(true);
  });

  it("is not current once the arena has completed", () => {
    renderStepper(minimalStatus({ stage: "idle", arenaStage: "completed" }));

    expect(healStep("Heal").classList.contains("current")).toBe(false);
  });
});

describe("the Merged step", () => {
  it("is ready while the loop stage is actively merging", () => {
    renderStepper(minimalStatus({ stage: "merging", baselineFrozen: true }));

    expect(healStep("Merged").classList.contains("ready")).toBe(true);
  });

  it("is ready once passed with a frozen baseline", () => {
    renderStepper(minimalStatus({ stage: "passed", baselineFrozen: true }));

    expect(healStep("Merged").classList.contains("ready")).toBe(true);
  });

  it("is not ready when passed but the baseline was never (re-)frozen", () => {
    renderStepper(minimalStatus({ stage: "passed", baselineFrozen: false }));

    expect(healStep("Merged").classList.contains("ready")).toBe(false);
  });

  it("is current only while actively merging, not once already merged", () => {
    renderStepper(minimalStatus({ stage: "merging", baselineFrozen: true }));

    expect(healStep("Merged").classList.contains("current")).toBe(true);
  });

  it("is ready but no longer current once a merge has completed (stage settled to 'passed')", () => {
    renderStepper(minimalStatus({ stage: "passed", baselineFrozen: true }));

    expect(healStep("Merged").classList.contains("ready")).toBe(true);
    expect(healStep("Merged").classList.contains("current")).toBe(false);
  });
});

describe("a combination the new rail contract will forbid", () => {
  it("renders Observe as current at the same time Heal is ready -- today's steps have no shared 'only one active step' invariant", () => {
    renderStepper(minimalStatus({ stage: "idle", arenaStage: "completed", baselineFrozen: false }));

    expect(healStep("Observe").classList.contains("current")).toBe(true);
    expect(healStep("Heal").classList.contains("ready")).toBe(true);
  });
});
