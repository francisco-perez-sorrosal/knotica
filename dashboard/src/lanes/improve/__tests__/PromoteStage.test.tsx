import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ToolClient } from "../../../toolClient";
import type {
  BranchScoreboard,
  CompilePromoteResult,
  ScoreboardEntry,
  WikiStatus,
} from "../../../types";

/**
 * `dashboard/src/lanes/improve/PromoteStage.tsx` does not exist yet -- this is
 * the RED half of a paired step (`IMPLEMENTATION_PLAN.md` Step 73/74,
 * `INTERFACE_DESIGN.md` §2.4's `promote` row, §2.0 clause 2/3). Loaded through
 * a non-literal dynamic `import()` specifier, the same device
 * `lanes/__tests__/LaneRail.test.tsx` and `lanes/improve/__tests__/GateStage.test.tsx`
 * used for their own not-yet-existing modules: a literal
 * `import { PromoteStage } from "../PromoteStage"` would fail `tsc --noEmit`
 * for the whole project the moment this file lands, and a dynamic import
 * whose argument is not a string literal is left unresolved by TypeScript, so
 * the rest of the tree keeps type-checking while this file fails at
 * *runtime* with the missing-module error the paired implementation step is
 * gated on.
 *
 * Four load-bearing assumptions the paired implementer may satisfy
 * differently (the paired implementation wins on conflict; full reasoning in
 * `LEARNINGS_test-engineer_step74.md`):
 *
 *   1. `<PromoteStage client={...} topic={...} vault={...} status={...}
 *      onStatusRefresh={...} />` -- mirrors `ScoreboardPanel`'s own prop
 *      shape, since `promote` is described as absorbing that panel's
 *      scoreboard/promote/delete surface.
 *   2. The reviewed (open) compile branch's preview control carries
 *      `data-testid="promote-preview-trigger"` -- this suite's own click
 *      target, deliberately independent of whichever button copy the
 *      implementer chooses (today's two duplicate paths disagree: "Preview
 *      promote" in `ScoreboardPanel.tsx`, "Preview merge" in
 *      `CompilePanel.tsx` -- §2.4 point 6 collapses them into one, but not
 *      which copy wins).
 *   3. `§2.4` point 6's "one control" collapse resolves to the
 *      `branches action=promote kind="compile"` call
 *      (`client.branchPromote`) and never reaches the standalone
 *      `compile action=promote` call (`client.compilePromote`) -- the two
 *      calls "resolve to the same core call" per the design doc, so keeping
 *      both wired would be the exact duplicate-path defect being designed
 *      against.
 *   4. The real `PromotePreviewBanner` (`PromotePreview.tsx`) renders the
 *      dry-run preview and its `onApply` triggers the apply leg -- already
 *      characterized in the codebase; this suite only asserts PromoteStage
 *      invokes it (boundary mock, mirroring `GateStage.test.tsx`'s treatment
 *      of `PromptDiff`), not that its internals behave correctly.
 */

interface PromoteStageProps {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}

type PromoteStageComponent = (props: PromoteStageProps) => JSX.Element;

interface PromoteStageModule {
  PromoteStage: PromoteStageComponent;
}

const PROMOTE_STAGE_MODULE_PATH = "../PromoteStage";

let PromoteStage: PromoteStageComponent;

beforeAll(async () => {
  ({ PromoteStage } = (await import(
    PROMOTE_STAGE_MODULE_PATH
  )) as PromoteStageModule);
});

/**
 * `PromptDiff` is a real, already-tested component -- stubbing it here is a
 * boundary mock (it reaches `client.promptDiff`, out of scope for this
 * suite), not a mock of the unit under test. If `PromoteStage` reimplements
 * its own diff rendering instead of importing and invoking the real
 * component, this stub never mounts and the assertion that looks for it
 * fails.
 */
vi.mock("../../../PromptDiff", () => ({
  PromptDiff: (props: { branch?: string | null }) => (
    <div data-testid="prompt-diff-mock" data-branch={props.branch ?? ""} />
  ),
}));

/**
 * Same boundary-mock discipline for `PromotePreviewBanner`: a real,
 * already-tested component (`PromotePreview.tsx`). The stub renders a single
 * apply control wired to the real `onApply` handler PromoteStage passes it,
 * so the apply leg of the dry-run/apply sequence is still driven through
 * PromoteStage's own logic -- only the banner's presentation is stubbed.
 */
vi.mock("../../../PromotePreview", () => ({
  PromotePreviewBanner: (props: {
    preview: CompilePromoteResult | null;
    onApply: () => void;
  }) =>
    props.preview ? (
      <div
        data-testid="promote-preview-mock"
        data-branch={props.preview.branch}
      >
        <button type="button" onClick={() => void props.onApply()}>
          Apply merge
        </button>
      </div>
    ) : null,
}));

afterEach(cleanup);

const TOPIC = "agentic-systems";
const VAULT = "main";
const BRANCH = "compile/agentic-systems/2026-08-20T10-00-00";

function scoreboardEntry(
  overrides: Partial<ScoreboardEntry> = {},
): ScoreboardEntry {
  return {
    kind: "compile",
    name: BRANCH,
    sha: "abc1234",
    scalar: 0.66,
    baseline: 0.62,
    delta: 0.04,
    beats_baseline: true,
    status: "open",
    promotable: true,
    slot: "open",
    deletable: false,
    ...overrides,
  };
}

function baseBoard(
  overrides: Partial<BranchScoreboard> = {},
): BranchScoreboard {
  return {
    schema_version: 1,
    topic: TOPIC,
    baseline: 0.62,
    baseline_meta: {
      scope: "topic",
      source: "frozen",
      path: `${TOPIC}/.knotica/loop-state.json`,
      frozen: true,
      last_metrics_scalar: 0.66,
    },
    default_branch: "main",
    open_compile_branch: BRANCH,
    entries: [scoreboardEntry()],
    ...overrides,
  };
}

function baseStatus(): WikiStatus {
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
    gate: { state: "pass", baseline: 0.62, last_scalar: 0.66 },
    llm: { available: false, mode: null },
    loop: {
      runner: {
        alive: false,
        pid: null,
        beat_at: null,
        interval_seconds: null,
      },
      stage: "idle",
    },
  };
}

function dryRunPreview(
  overrides: Partial<CompilePromoteResult> = {},
): CompilePromoteResult {
  return {
    mode: "dry-run",
    merged: false,
    branch: BRANCH,
    commit_sha: null,
    message: "Would merge the compile branch onto main.",
    ...overrides,
  };
}

function applyResult(
  overrides: Partial<CompilePromoteResult> = {},
): CompilePromoteResult {
  return {
    mode: "apply",
    merged: true,
    branch: BRANCH,
    commit_sha: "deadbee",
    message: "Merged the compile branch onto main.",
    ...overrides,
  };
}

function fakeClient(overrides: Partial<ToolClient> = {}): ToolClient {
  return {
    branchScoreboard: vi.fn().mockResolvedValue(baseBoard()),
    branchPromote: vi.fn(),
    compilePromote: vi.fn(),
    ...overrides,
  } as unknown as ToolClient;
}

describe("the branch under review is rendered from the live scoreboard", () => {
  it("shows the reviewed compile branch's identity once the scoreboard loads", async () => {
    render(
      <PromoteStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    expect(await screen.findByText(BRANCH, { exact: false })).toBeTruthy();
  });
});

describe("the compiled diff is the real PromptDiff, not reimplemented", () => {
  it("invokes PromptDiff for the reviewed branch", async () => {
    render(
      <PromoteStage
        client={fakeClient()}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    const diffMock = await screen.findByTestId("prompt-diff-mock");
    expect(diffMock.getAttribute("data-branch")).toBe(BRANCH);
  });
});

describe("promote collapses onto one call path -- compile action=promote is never reached", () => {
  it("previews and applies through branches action=promote, and never calls the standalone compile-promote action", async () => {
    const branchPromote = vi
      .fn()
      .mockResolvedValueOnce(dryRunPreview())
      .mockResolvedValueOnce(applyResult());
    const compilePromote = vi.fn();

    render(
      <PromoteStage
        client={fakeClient({ branchPromote, compilePromote })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    fireEvent.click(await screen.findByTestId("promote-preview-trigger"));
    fireEvent.click(await screen.findByRole("button", { name: "Apply merge" }));

    await vi.waitFor(() => expect(branchPromote).toHaveBeenCalledTimes(2));
    expect(compilePromote).not.toHaveBeenCalled();
    expect(branchPromote.mock.calls[0]).toEqual([
      "compile",
      TOPIC,
      BRANCH,
      "dry-run",
      VAULT,
    ]);
    expect(branchPromote.mock.calls[1]).toEqual([
      "compile",
      TOPIC,
      BRANCH,
      "apply",
      VAULT,
    ]);
  });
});

describe("the merge affordance previews before it mutates", () => {
  it("never applies on a single click -- the preview leg alone cannot reach the merge", async () => {
    const branchPromote = vi.fn().mockResolvedValueOnce(dryRunPreview());

    render(
      <PromoteStage
        client={fakeClient({ branchPromote })}
        topic={TOPIC}
        vault={VAULT}
        status={baseStatus()}
      />,
    );

    fireEvent.click(await screen.findByTestId("promote-preview-trigger"));
    await screen.findByTestId("promote-preview-mock");

    expect(branchPromote).toHaveBeenCalledTimes(1);
    expect(branchPromote.mock.calls[0][3]).toBe("dry-run");
  });
});

describe("the lifecycle contract on the two branch verbs", () => {
  it("sends a merged branch to the probe -- the scoreboard is a claim, not proof", async () => {
    const branchPromote = vi.fn(async () => ({
      mode: "apply" as const,
      branch: "compile/open",
      into: "main",
      merged: true,
      commit_sha: "abc1234",
      message: "Merged compile/open into main.",
    }));
    const client = fakeClient({ branchPromote });
    render(
      <PromoteStage client={client} topic={TOPIC} vault={VAULT} status={null} />,
    );

    fireEvent.click(
      await screen.findByTestId("promote-preview-trigger"),
    );
    fireEvent.click(await screen.findByRole("button", { name: /apply merge/i }));

    expect(await screen.findByText(/Go to Improve → Prove\./)).toBeTruthy();
  });
});
