import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/preact";
import type { JSX } from "preact";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { ObsidianContext } from "../../../obsidianLinks";
import type { ToolClient } from "../../../toolClient";
import type {
  DoctorCheck,
  DoctorReport,
  LintViolation,
  OkfCheckResult,
  OkfRepairResult,
  VaultLintResult,
} from "../../../types";

/**
 * `dashboard/src/lanes/tend/TendLane.tsx` does not exist yet -- this is the
 * RED half of a paired implementation/test step for Tend's
 * doctor/lint/okf/migrate checklist and the relocated
 * `VaultPane.tsx:715` gate-note sentence. Loaded through a non-literal
 * dynamic `import()` specifier -- the same device `lanes/__tests__/LaneRail.test.tsx`
 * and `lanes/improve/__tests__/GateStage.test.tsx` used
 * for their own not-yet-existing modules: a literal `import { TendLane }
 * from "../TendLane"` would fail `tsc --noEmit` for the whole project the
 * moment this file lands; a dynamic import whose argument is not a string
 * literal is left unresolved by TypeScript, so the rest of the tree keeps
 * type-checking while this file fails at *runtime* with the missing-module
 * error the paired implementation step is gated on.
 *
 * Load-bearing assumptions the paired implementer may satisfy differently
 * (the paired
 * implementation wins on conflict):
 *
 *   1. `<TendLane client={...} vault={...} obsidianCtx={...} />` -- three
 *      props, no `topic` (Tend is per-vault, not per-topic --
 *      `CLAUDE.md`'s Home/Tend/Improve discriminator). **Declared change
 *     **: `TendLane` now also takes a required
 *      `topic` prop, threading it through to its fifth stage,
 *      `DriftStage.tsx` -- `notes`'s own MCP dispatcher rejects an empty
 *      topic, so the one stage that reaches it cannot stay vault-wide the
 *      way doctor/lint/okf/migrate do. `renderTendLane` below defaults it
 *      to a fixture constant; every existing assertion in this file is
 *      untouched by the addition.
 *   2. On mount, `TendLane` calls `client.doctorRun`, `client.vaultLint`,
 *      and `client.okfCheck` exactly once each, unconditionally -- the
 *      checklist rail shows all peers simultaneously (no more tabs), so all
 *      three checks must be fetched together rather than lazily per active
 *      tab. `vaultLint` is called vault-wide (Tend has no topic to scope
 *      lint to).
 *   3. Checklist state (`data-state`, C1) is the strict "clean" reading:
 *      `complete` iff the check is fully clean (doctor: fail==0 AND
 *      warn==0; lint: zero violations; okf: not failed, zero errors, zero
 *      notes); `blocked` otherwise, whenever the check has actually run.
 *      This is deliberately stricter than `VaultPane.tsx`'s own three-tier
 *      `ok`/`warn`/`bad` health-chip tone (which treats "warn" as distinct
 *      from "bad") -- C1 only has three states, and "blocked (needs a fix)"
 *      is the only non-`pending` state available for anything short of
 *      fully clean. The `ok`/`warn`/`bad` health-chip vocabulary is
 *      reused as a *decoration* on top of this (assumption 4 below), not as
 *      the source of `data-state`.
 *   4. `migrate` never calls the client (no MCP surface yet)
 *      and is always `pending`; it renders a
 *      copyable CLI handoff naming `knotica tend migrate --dry-run`.
 *   5. `doctor`, `lint`, and `okf`'s panel bodies are the moved-but-unedited
 *      `DoctorPanel`/`LintPanel`/`OkfStatus` content from `VaultPane.tsx`
 *      (the plan's own words: "logic unchanged -- behaviour-preserving
 *      move, not a rewrite") -- so a FAIL check's `remediation`, a lint
 *      violation's `path`, and an OKF error's `message` all still render as
 *      plain text findable by substring.
 *   6. OKF's dry-run/apply controls keep their exact current discipline
 *      (`VaultPane.tsx:676-709`), adjusted for the orchestrator's
 *      no-native-dialogs ruling (declared adjustment):
 *      dry-run fires immediately (`data-testid="tend-okf-repair-dry-run"`);
 *      apply (`data-testid="tend-okf-repair-apply"`) is gated behind an
 *      in-DOM two-click armed→confirm affordance -- first click arms,
 *      second click fires, a separate `"tend-okf-repair-apply-cancel"`
 *      un-arms -- rather than `window.confirm`, and never calls
 *      `client.okfRepair` when the user cancels.
 *   7. The relocated gate-note sentence (`VaultPane.tsx:715`, "Read-only
 *      here. Gating a candidate is billed and two-phase, and lives on the
 *      **Loop** pane...") renders inside a single element carrying
 *      `data-testid="tend-gate-note"`, with "Loop" replaced by "Improve"
 *      and nothing else changed -- so its normalized `textContent` can be
 *      compared verbatim rather than matched piecewise across the `<strong>`
 *      boundary (`getByText` does not recurse into child elements' own
 *      text -- a documented gotcha of this suite family).
 *
 * Not tested here (out of this step's scope, or a later milestone's job):
 * the `drift` stage's own rendering/collapse-budget/mutation behavior
 * (covered standalone by `DriftStage.test.tsx`; this file only pins that it
 * is wired in as the checklist's fifth row), any lane-level `outcome`/"Terminal" summary
 * banner (the design's own mockup reads "Terminal: clean"
 * while simultaneously showing a check that needs a fix -- inconsistent with
 * C3 as literally stated, flagged
 * rather than guessed at), and doctor's auto-repair-dry-run cascade
 * (`VaultPane.tsx`'s `doctorNeedsRepair` trigger) which is an orthogonal
 * mechanism this suite does not need to pin to prove the checklist contract.
 */

interface TendLaneProps {
  client: ToolClient | null;
  vault: string;
  topic: string;
  obsidianCtx: ObsidianContext;
}

type TendLaneComponent = (props: TendLaneProps) => JSX.Element;

interface TendLaneModule {
  TendLane: TendLaneComponent;
}

const TEND_LANE_MODULE_PATH = "../TendLane";

let TendLane: TendLaneComponent;

beforeAll(async () => {
  ({ TendLane } = (await import(TEND_LANE_MODULE_PATH)) as TendLaneModule);
});

afterEach(cleanup);

const VAULT = "main";

function baseDoctorReport(overrides: Partial<DoctorReport> = {}): DoctorReport {
  return {
    schema_version: 1,
    vault: VAULT,
    quick: false,
    ok: true,
    exit_code: 0,
    checks: [
      {
        name: "git",
        status: "PASS",
        message: "working tree clean",
        remediation: null,
      },
    ],
    summary: { pass: 1, warn: 0, fail: 0 },
    fix_guidance: null,
    ...overrides,
  };
}

function baseLintResult(
  overrides: Partial<VaultLintResult> = {},
): VaultLintResult {
  return { topic: "", violations: [], ...overrides };
}

function baseOkfResult(
  overrides: Partial<OkfCheckResult> = {},
): OkfCheckResult {
  return {
    status: "ok",
    failed: false,
    bundle_root: "kb",
    concept_files_checked: 12,
    reserved_files_checked: 3,
    errors: [],
    notes: [],
    strict_failures: [],
    ...overrides,
  };
}

function baseOkfRepairResult(
  overrides: Partial<OkfRepairResult> = {},
): OkfRepairResult {
  return {
    status: "ok",
    dry_run: true,
    mode: "dry-run",
    files_changed: [],
    notes: [],
    report_path: null,
    commit_sha: null,
    ...overrides,
  };
}

/** Boundary fake of `ToolClient` (`dashboard/CLAUDE.md`: "the single seam for MCP calls") --
 * only the four methods Tend's checklist reaches, all defaulted to a clean fixture so a
 * test that only cares about one check doesn't leave the other two's promises unresolved. */
function fakeClient(
  overrides: {
    doctor?: DoctorReport;
    lint?: VaultLintResult;
    okf?: OkfCheckResult;
    okfRepair?: OkfRepairResult;
  } = {},
) {
  const doctorRun = vi.fn(
    async (..._args: unknown[]) => overrides.doctor ?? baseDoctorReport(),
  );
  const vaultLint = vi.fn(
    async (..._args: unknown[]) => overrides.lint ?? baseLintResult(),
  );
  const okfCheck = vi.fn(
    async (..._args: unknown[]) => overrides.okf ?? baseOkfResult(),
  );
  const okfRepair = vi.fn(
    async (..._args: unknown[]) => overrides.okfRepair ?? baseOkfRepairResult(),
  );
  const client = {
    doctorRun,
    vaultLint,
    okfCheck,
    okfRepair,
  } as unknown as ToolClient;
  return { client, doctorRun, vaultLint, okfCheck, okfRepair };
}

const TOPIC = "agentic-systems";

// `DriftStage` (the fifth checklist row) defers every read until its own
// `[Check]` control is clicked -- no test in this file clicks it, so
// `fakeClient` above deliberately carries no `notesList`/`notesDrift` stub;
// see `DriftStage.test.tsx` for that stage's own behavior.
function renderTendLane(
  client: ToolClient,
  vault = VAULT,
  topic = TOPIC,
): Element {
  return render(
    <TendLane client={client} vault={vault} topic={topic} obsidianCtx={{}} />,
  ).container;
}

function stageNodes(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(".lane-stage"));
}

const DOCTOR = 0;
const LINT = 1;
const OKF = 2;
const MIGRATE = 3;
const DRIFT = 4;

describe("the checklist rail", () => {
  it("renders exactly the five doctor/lint/okf/migrate/drift stages, in that order", () => {
    const { client } = fakeClient();
    const container = renderTendLane(client);

    const nodes = stageNodes(container);
    expect(nodes).toHaveLength(5);
    expect(nodes[DOCTOR].textContent).toMatch(/doctor/i);
    expect(nodes[LINT].textContent).toMatch(/lint/i);
    expect(nodes[OKF].textContent).toMatch(/okf/i);
    expect(nodes[MIGRATE].textContent).toMatch(/migrate/i);
    expect(nodes[DRIFT].textContent).toMatch(/drift/i);
  });

  it("labels the stage list with the tend lane name", () => {
    const { client } = fakeClient();
    renderTendLane(client);

    expect(screen.getByRole("list", { name: "tend stages" })).toBeTruthy();
  });

  it("shows all five stages as pending before any check has resolved -- the honest loading state", () => {
    const { client } = fakeClient();
    const container = renderTendLane(client);

    expect(stageNodes(container).map((node) => node.dataset.state)).toEqual([
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("renders no tab bar -- VaultPane's CHECK_TABS pattern does not reappear", async () => {
    const { client, doctorRun } = fakeClient();
    const container = renderTendLane(client);
    await vi.waitFor(() => expect(doctorRun).toHaveBeenCalled());

    expect(container.querySelector(".check-tabs")).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "Vault checks" }),
    ).toBeNull();
  });

  it("fetches doctor, vault-wide lint, and okf for the given vault on mount", async () => {
    const { client, doctorRun, vaultLint, okfCheck } = fakeClient();
    renderTendLane(client, "kb-vault");

    await vi.waitFor(() => {
      expect(doctorRun).toHaveBeenCalled();
      expect(vaultLint).toHaveBeenCalled();
      expect(okfCheck).toHaveBeenCalled();
    });

    expect(doctorRun.mock.calls[0]).toContain("kb-vault");
    expect(vaultLint.mock.calls[0]).toContain("kb-vault");
    // Vault-wide: Tend has no topic to scope lint to.
    expect(
      vaultLint.mock.calls[0].some((arg) => arg === "" || arg === undefined),
    ).toBe(true);
    expect(okfCheck.mock.calls[0]).toContain("kb-vault");
  });
});

describe("doctor's checklist state follows the strict 'clean' rule (C1)", () => {
  it("is complete when every check passes", async () => {
    const { client } = fakeClient({
      doctor: baseDoctorReport({
        checks: [],
        summary: { pass: 0, warn: 0, fail: 0 },
      }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[DOCTOR].dataset.state).toBe("complete"),
    );
  });

  it("is blocked when a check fails, and surfaces that check's remediation", async () => {
    const failCheck: DoctorCheck = {
      name: "git",
      status: "FAIL",
      message: "3 files are dirty",
      remediation: "run `knotica tend doctor repair --apply`",
    };
    const { client } = fakeClient({
      doctor: baseDoctorReport({
        checks: [failCheck],
        summary: { pass: 0, warn: 0, fail: 1 },
      }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[DOCTOR].dataset.state).toBe("blocked"),
    );
    expect(
      within(stageNodes(container)[DOCTOR]).getByText(
        /knotica tend doctor repair --apply/,
      ),
    ).toBeTruthy();
  });

  it("is blocked when a check only warns -- 'complete' means fully clean, not merely non-failing", async () => {
    const warnCheck: DoctorCheck = {
      name: "unpushed",
      status: "WARN",
      message: "2 commits are unpushed",
      remediation: null,
    };
    const { client } = fakeClient({
      doctor: baseDoctorReport({
        checks: [warnCheck],
        summary: { pass: 0, warn: 1, fail: 0 },
      }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[DOCTOR].dataset.state).toBe("blocked"),
    );
  });
});

describe("lint's checklist state follows the strict 'clean' rule (C1)", () => {
  it("is complete with zero violations", async () => {
    const { client } = fakeClient({ lint: baseLintResult({ violations: [] }) });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[LINT].dataset.state).toBe("complete"),
    );
  });

  it("is blocked by even a single violation, and surfaces the violated path", async () => {
    const violation: LintViolation = {
      check: "missing_tags",
      path: "agentic-systems/mipro.md",
      line: null,
      message: "missing tags",
      fix: "add frontmatter tags",
    };
    const { client } = fakeClient({
      lint: baseLintResult({ violations: [violation] }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[LINT].dataset.state).toBe("blocked"),
    );
    expect(
      within(stageNodes(container)[LINT]).getByText(/mipro\.md/),
    ).toBeTruthy();
  });
});

describe("okf's checklist state follows the strict 'clean' rule (C1)", () => {
  it("is complete when compatible with zero errors and zero notes", async () => {
    const { client } = fakeClient({
      okf: baseOkfResult({ failed: false, errors: [], notes: [] }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[OKF].dataset.state).toBe("complete"),
    );
  });

  it("is blocked when the schema check fails, and surfaces the error message", async () => {
    const { client } = fakeClient({
      okf: baseOkfResult({
        failed: true,
        errors: [
          {
            path: "concepts/foo.md",
            code: "schema",
            message: "unknown field 'bar'",
            severity: "error",
          },
        ],
      }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[OKF].dataset.state).toBe("blocked"),
    );
    expect(
      within(stageNodes(container)[OKF]).getByText(/unknown field 'bar'/),
    ).toBeTruthy();
  });

  it("is blocked when only advisory notes are present -- not merely non-failing", async () => {
    const { client } = fakeClient({
      okf: baseOkfResult({
        failed: false,
        errors: [],
        notes: ["a reserved file drifted from its template"],
      }),
    });
    const container = renderTendLane(client);

    await vi.waitFor(() =>
      expect(stageNodes(container)[OKF].dataset.state).toBe("blocked"),
    );
  });
});

describe("the migrate stage has no MCP surface yet", () => {
  it("is always pending, regardless of the other three checks' results", async () => {
    const { client, doctorRun } = fakeClient();
    const container = renderTendLane(client);
    await vi.waitFor(() => expect(doctorRun).toHaveBeenCalled());

    expect(stageNodes(container)[MIGRATE].dataset.state).toBe("pending");
  });

  it("renders a copyable CLI handoff card naming the dry-run command, never a live poll", async () => {
    const { client, doctorRun } = fakeClient();
    const container = renderTendLane(client);
    await vi.waitFor(() => expect(doctorRun).toHaveBeenCalled());

    expect(
      within(stageNodes(container)[MIGRATE]).getByText(
        /knotica tend migrate --dry-run/,
      ),
    ).toBeTruthy();
  });

  it("never calls doctor, lint, or okf more than once -- ruling out an interval-based poll", async () => {
    const { client, doctorRun, vaultLint, okfCheck } = fakeClient();
    renderTendLane(client);
    await vi.waitFor(() => expect(doctorRun).toHaveBeenCalled());

    // Give a hidden interval/poll a chance to fire before asserting it never did.
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(doctorRun).toHaveBeenCalledTimes(1);
    expect(vaultLint).toHaveBeenCalledTimes(1);
    expect(okfCheck).toHaveBeenCalledTimes(1);
  });
});

describe("OKF repair keeps its two-phase discipline, gated by an in-DOM armed→confirm affordance (no native dialogs)", () => {
  // Declared adjustment: the dashboard-wide no-native-dialogs rule
  // landed after this suite was
  // drafted and bans `window.confirm()` dashboard-wide. The two tests below were rewritten to
  // pin the in-DOM two-click armed→confirm affordance `HealStage.tsx` established (one button:
  // first click arms, second click fires) instead of spying on `window.confirm`. The
  // `tend-okf-repair-apply`/`tend-okf-repair-dry-run` click targets are unchanged.

  it("fires the dry-run immediately, without arming anything", async () => {
    const { client, okfRepair } = fakeClient();
    renderTendLane(client);

    fireEvent.click(screen.getByTestId("tend-okf-repair-dry-run"));

    await vi.waitFor(() => expect(okfRepair).toHaveBeenCalled());
    expect(okfRepair.mock.calls[0][0]).toBe("dry-run");
  });

  it("never calls okfRepair when the user cancels the armed apply", async () => {
    const { client, okfRepair } = fakeClient();
    renderTendLane(client);

    fireEvent.click(screen.getByTestId("tend-okf-repair-apply"));
    fireEvent.click(screen.getByTestId("tend-okf-repair-apply-cancel"));

    expect(okfRepair).not.toHaveBeenCalled();
  });

  it("calls okfRepair with apply mode once the user confirms the armed apply (second click)", async () => {
    const { client, okfRepair } = fakeClient();
    renderTendLane(client, "kb-vault");

    fireEvent.click(screen.getByTestId("tend-okf-repair-apply"));
    fireEvent.click(screen.getByTestId("tend-okf-repair-apply"));

    await vi.waitFor(() => expect(okfRepair).toHaveBeenCalled());
    expect(okfRepair.mock.calls[0][0]).toBe("apply");
    expect(okfRepair.mock.calls[0]).toContain("kb-vault");
  });
});

describe("the relocated gate note is pinned verbatim (VaultPane.tsx:715, Improve substituted for Loop)", () => {
  const GATE_NOTE_TEXT =
    "Read-only here. Gating a candidate is billed and two-phase, and lives on the Improve pane so there is exactly one place it can be triggered from.";

  function normalizeWhitespace(text: string): string {
    return text.replace(/\s+/g, " ").trim();
  }

  it("renders the sentence verbatim except for the one named substitution", () => {
    const { client } = fakeClient();
    renderTendLane(client);

    const note = screen.getByTestId("tend-gate-note");
    expect(normalizeWhitespace(note.textContent ?? "")).toBe(GATE_NOTE_TEXT);
  });

  it("no longer names Loop -- the pane it narrates is Improve now", () => {
    const { client } = fakeClient();
    renderTendLane(client);

    const note = screen.getByTestId("tend-gate-note");
    expect(note.textContent ?? "").not.toMatch(/\bLoop\b/);
  });
});
