import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import {
  ObsidianFileLink,
  type ObsidianContext,
} from "../../obsidianLinks";
import { formatToolFailure, type ToolClient } from "../../toolClient";
import type {
  DoctorReport,
  OkfCheckResult,
  OkfRepairResult,
  VaultLintResult,
} from "../../types";
import { deriveChecklistStages } from "../laneRailState";
import type { StageState } from "../laneRailState";

/**
 * `TendLane` (`INTERFACE_DESIGN.md §2.6`) — the per-vault mechanical checklist
 * absorbing `VaultPane.tsx`'s "Checks" tabs (doctor/lint/okf/loop). `VaultPane`
 * itself is untouched here (its own deletion is a later, dedicated step); the
 * three read panels below are a **behaviour-preserving move**, not a rewrite
 * — `DoctorPanel`/`LintPanel`/`OkfStatus` (with its `OkfPanel`/`RepairPanel`
 * halves) keep `VaultPane.tsx`'s exact logic, just re-homed. `DoctorRemediations`
 * (the interactive select-paths/apply-repair workflow) is **not** ported in
 * this step: the paired test-engineer's `LEARNINGS_test-engineer_step66.md`
 * explicitly scoped doctor's auto-repair cascade as "an orthogonal mechanism
 * this suite does not need to pin," and porting it would add a second
 * untested mutating-apply surface beyond what this step's RED contract
 * requires. Doctor's own per-check `remediation` text (already CLI-shaped,
 * e.g. "run `knotica tend doctor repair --apply`") still carries the fix
 * forward — recorded as a scoped-down, documented decision in
 * `LEARNINGS_implementer_step65.md`, not a silent omission.
 *
 * Two rulings this step resolves (full reasoning in
 * `LEARNINGS_implementer_step65.md`):
 *
 *   (a) OKF's "Repair apply" no longer gates on `window.confirm()` — the
 *       orchestrator's no-native-dialogs ruling (`LEARNINGS.md`) forbids it
 *       dashboard-wide. It gates on the same in-DOM two-click armed→confirm
 *       affordance `HealStage.tsx` established (one button: first click
 *       arms, second click fires; a separate Cancel un-arms).
 *   (b) The checklist `kind` has no lane-level "Terminal" outcome summary —
 *       C3's clean-iff-every-check-complete rule is a derived property of
 *       the four stages' own states, not a fifth thing to render, and this
 *       kind never derives `active` (no per-lane UI focus concept exists
 *       yet). `INTERFACE_DESIGN.md §2.6`'s own mockup text is inconsistent
 *       with C3 on this point (flagged, not followed) — no outcome banner
 *       renders here.
 */

type CheckStatus = "complete" | "blocked" | "pending";

const MIGRATE_DRY_RUN_COMMAND = "knotica tend migrate --dry-run";

/** Green = healthy, yellow = in progress / needs attention, red = broken (`VaultPane.tsx`). */
type Health = "ok" | "warn" | "bad";

const HEALTH_LABEL: Record<Health, string> = {
  ok: "OK",
  warn: "Watch",
  bad: "Fix",
};

type TendBusy = null | "okf-dry" | "okf-apply";

function stageGlyph(state: StageState, position: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(position);
}

function doctorCheckStatus(report: DoctorReport | null): CheckStatus {
  if (!report) return "pending";
  return report.summary.fail === 0 && report.summary.warn === 0 ? "complete" : "blocked";
}

function doctorTone(report: DoctorReport): Health {
  if (report.summary.fail > 0) return "bad";
  return report.summary.warn > 0 ? "warn" : "ok";
}

function lintCheckStatus(result: VaultLintResult | null): CheckStatus {
  if (!result) return "pending";
  return result.violations.length === 0 ? "complete" : "blocked";
}

function lintToneFor(count: number): Health {
  if (count <= 0) return "ok";
  return count < 10 ? "warn" : "bad";
}

function okfCheckStatus(result: OkfCheckResult | null): CheckStatus {
  if (!result) return "pending";
  return !result.failed && result.errors.length === 0 && result.notes.length === 0
    ? "complete"
    : "blocked";
}

function checkStatusTone(status: string): Health {
  if (status === "PASS") return "ok";
  return status === "WARN" ? "warn" : "bad";
}

function formatActionError(cause: unknown): string {
  if (cause instanceof Error && cause.message) return cause.message;
  return formatToolFailure(cause, "action");
}

/** Fetches doctor/lint/okf once per (client, vault) and owns OKF's dry-run/armed-apply flow —
 * kept out of `TendLane` itself so the component body stays render-only. */
function useTendChecks(client: ToolClient | null, vault: string) {
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [lint, setLint] = useState<VaultLintResult | null>(null);
  const [okf, setOkf] = useState<OkfCheckResult | null>(null);
  const [repair, setRepair] = useState<OkfRepairResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<TendBusy>(null);
  const [okfApplyArmed, setOkfApplyArmed] = useState(false);

  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    void (async () => {
      try {
        const [nextDoctor, nextLint, nextOkf] = await Promise.all([
          client.doctorRun(vault),
          client.vaultLint("", vault),
          client.okfCheck(vault),
        ]);
        if (cancelled) return;
        setDoctor(nextDoctor);
        setLint(nextLint);
        setOkf(nextOkf);
      } catch (cause) {
        if (!cancelled) setError(formatActionError(cause));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, vault]);

  useEffect(() => {
    setOkfApplyArmed(false);
  }, [vault]);

  async function runOkfDryRun() {
    if (!client || busy !== null) return;
    setBusy("okf-dry");
    setError(null);
    try {
      setRepair(await client.okfRepair("dry-run", vault));
    } catch (cause) {
      setError(formatActionError(cause));
    } finally {
      setBusy(null);
    }
  }

  async function runOkfApply() {
    if (!client) return;
    setBusy("okf-apply");
    setError(null);
    try {
      setRepair(await client.okfRepair("apply", vault));
    } catch (cause) {
      setError(formatActionError(cause));
    } finally {
      setBusy(null);
      setOkfApplyArmed(false);
    }
  }

  function handleOkfApplyClick() {
    if (busy !== null) return;
    if (!okfApplyArmed) {
      setOkfApplyArmed(true);
      return;
    }
    void runOkfApply();
  }

  return {
    doctor,
    lint,
    okf,
    repair,
    loading,
    error,
    busy,
    okfApplyArmed,
    runOkfDryRun,
    handleOkfApplyClick,
    cancelOkfApply: () => setOkfApplyArmed(false),
  };
}

export function TendLane({
  client,
  vault,
  obsidianCtx,
}: {
  client: ToolClient | null;
  vault: string;
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  const {
    doctor,
    lint,
    okf,
    repair,
    loading,
    error,
    busy,
    okfApplyArmed,
    runOkfDryRun,
    handleOkfApplyClick,
    cancelOkfApply,
  } = useTendChecks(client, vault);

  const checklist = deriveChecklistStages([
    { id: "doctor", title: "Doctor", status: doctorCheckStatus(doctor) },
    { id: "lint", title: "Lint", status: lintCheckStatus(lint) },
    { id: "okf", title: "OKF", status: okfCheckStatus(okf) },
    { id: "migrate", title: "Migrate", status: "pending" },
  ]);
  const [doctorStage, lintStage, okfStage, migrateStage] = checklist;

  return (
    <main class="pane-main tend">
      {error ? (
        <aside role="alert" class="action-note tone-bad">
          Action failed: {error}
        </aside>
      ) : null}

      <ol class="lane-rail" aria-label="tend stages">
        <StageShell
          state={doctorStage.state}
          position={1}
          title="Doctor"
          healthChip={doctor ? <HealthChip tone={doctorTone(doctor)} /> : null}
        >
          <DoctorPanel report={doctor} busy={loading} />
        </StageShell>

        <StageShell
          state={lintStage.state}
          position={2}
          title="Lint"
          healthChip={lint ? <HealthChip tone={lintToneFor(lint.violations.length)} /> : null}
        >
          <LintPanel result={lint} busy={loading} obsidianCtx={obsidianCtx} />
        </StageShell>

        <StageShell state={okfStage.state} position={3} title="OKF">
          <OkfStatus okf={okf} repair={repair} busy={loading} obsidianCtx={obsidianCtx} />
          <OkfActions
            client={client}
            busy={busy}
            armed={okfApplyArmed}
            onDryRun={() => void runOkfDryRun()}
            onApplyClick={handleOkfApplyClick}
            onCancel={cancelOkfApply}
          />
        </StageShell>

        <StageShell state={migrateStage.state} position={4} title="Migrate">
          <MigrateHandoff />
        </StageShell>
      </ol>

      <p class="muted" data-testid="tend-gate-note">
        Read-only here. Gating a candidate is billed and two-phase, and lives on the{" "}
        <strong>Improve</strong> pane so there is exactly one place it can be triggered from.
      </p>
    </main>
  );
}

function HealthChip({ tone }: { tone: Health }): JSX.Element {
  return <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>;
}

/** The shared `.lane-rail`/`.lane-stage` shell every checklist stage renders through — factored
 * out after the initial draft repeated this wrapper four times (DRY, self-review). */
function StageShell({
  state,
  position,
  title,
  healthChip,
  children,
}: {
  state: StageState;
  position: number;
  title: string;
  healthChip?: JSX.Element | null;
  children: JSX.Element | Array<JSX.Element | null>;
}): JSX.Element {
  return (
    <li class="lane-stage" data-state={state}>
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(state, position)}
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{title}</strong>
          <span class="lane-state-label muted">{state}</span>
          {healthChip ?? null}
        </div>
        <div class="lane-stage-body">{children}</div>
      </div>
    </li>
  );
}

function OkfActions({
  client,
  busy,
  armed,
  onDryRun,
  onApplyClick,
  onCancel,
}: {
  client: ToolClient | null;
  busy: TendBusy;
  armed: boolean;
  onDryRun: () => void;
  onApplyClick: () => void;
  onCancel: () => void;
}): JSX.Element {
  return (
    <>
      <div class="tend-actions">
        <button
          type="button"
          data-testid="tend-okf-repair-dry-run"
          disabled={!client || busy !== null}
          onClick={onDryRun}
        >
          {busy === "okf-dry" ? "Previewing…" : "Repair dry-run"}
        </button>
        <button
          type="button"
          class="danger"
          data-testid="tend-okf-repair-apply"
          disabled={!client || busy !== null}
          onClick={onApplyClick}
        >
          {busy === "okf-apply" ? "Applying…" : armed ? "Confirm apply — writes files" : "Repair apply"}
        </button>
        {armed && busy === null ? (
          <button
            type="button"
            class="ghost"
            data-testid="tend-okf-repair-apply-cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
        ) : null}
      </div>
      <p class="action-note">
        Same as <code>knotica okf check|repair</code>. Apply writes files and creates one git
        commit.
      </p>
    </>
  );
}

/** No MCP surface exists for `migrate` yet (`INTERFACE_DESIGN.md §2.6`) — an honest "not
 * checked" stage rather than a fake poll, offering the CLI dry-run as a copyable handoff. */
function MigrateHandoff(): JSX.Element {
  return (
    <div class="remediation-panel">
      <p class="muted">No dashboard surface yet — run the CLI directly to preview a migration.</p>
      <div class="tend-cli">
        <code>{MIGRATE_DRY_RUN_COMMAND}</code>
        <button
          type="button"
          class="ghost"
          onClick={() => copyToClipboard(MIGRATE_DRY_RUN_COMMAND)}
        >
          Copy
        </button>
      </div>
    </div>
  );
}

function copyToClipboard(text: string): void {
  // Clipboard permission can be silently denied by the browser or MCP-App host; the command
  // text stays visible and selectable regardless, so a denied write has no user-facing cost.
  void navigator.clipboard?.writeText(text).catch(() => undefined);
}

function DoctorPanel({
  report,
  busy,
}: {
  report: DoctorReport | null;
  busy: boolean;
}): JSX.Element {
  if (!report) {
    return <p class="muted empty-check">{busy ? "Running doctor…" : "No doctor result yet."}</p>;
  }
  const tone = doctorTone(report);
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          Doctor · {report.summary.pass} pass / {report.summary.warn} warn / {report.summary.fail}{" "}
          fail
        </h3>
        <HealthChip tone={tone} />
      </div>
      <ul class="check-list" aria-label="Doctor checks">
        {report.checks.map((row) => (
          <li class={`check-row check-${row.status.toLowerCase()}`} key={row.name}>
            <span
              class={`health-chip ${checkStatusTone(row.status)}`}
              aria-label={`Status: ${row.status}`}
            >
              {row.status}
            </span>
            <div class="check-body">
              <div class="check-line">
                <strong class="check-name">{row.name}</strong>
                <span class="check-message">{row.message}</span>
              </div>
              {row.remediation && row.status !== "PASS" ? (
                <p class="fix-hint">→ {row.remediation}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {report.fix_guidance ? (
        <div class="fix-guidance">
          <h4>Fix guidance (CLI only — not a restore)</h4>
          <p>{report.fix_guidance.summary}</p>
          <ul>
            {report.fix_guidance.commands.map((command) => (
              <li key={command}>
                <code>{command}</code>
              </li>
            ))}
          </ul>
          <p class="action-note">{report.fix_guidance.note}</p>
        </div>
      ) : null}
    </div>
  );
}

function LintPanel({
  result,
  busy,
  obsidianCtx,
}: {
  result: VaultLintResult | null;
  busy: boolean;
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  if (!result) {
    return <p class="muted empty-check">{busy ? "Linting…" : "No lint result yet."}</p>;
  }
  const tone = lintToneFor(result.violations.length);
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          Mechanical lint · vault · {result.violations.length} hit
          {result.violations.length === 1 ? "" : "s"}
        </h3>
        <HealthChip tone={tone} />
      </div>
      {result.violations.length === 0 ? (
        <p class="tone-ok">No mechanical violations.</p>
      ) : (
        <ul class="violation-list">
          {result.violations.slice(0, 40).map((row, index) => (
            <li class="health-bad" key={`${row.path}-${row.check}-${index}`}>
              <ObsidianFileLink ctx={obsidianCtx} relativePath={row.path}>
                <strong>
                  {row.path}
                  {row.line != null ? `:${row.line}` : ""}
                </strong>
              </ObsidianFileLink>
              <span class="check-code">{row.check}</span>
              <p>{row.message}</p>
              <p class="fix-hint">→ {row.fix}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function OkfStatus({
  okf,
  repair,
  busy,
  obsidianCtx,
}: {
  okf: OkfCheckResult | null;
  repair: OkfRepairResult | null;
  busy: boolean;
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  if (!okf && !repair) {
    return <p class="muted empty-check">{busy ? "Checking OKF…" : "No OKF result yet."}</p>;
  }
  return (
    <>
      {okf ? <OkfPanel result={okf} obsidianCtx={obsidianCtx} /> : null}
      {repair ? <RepairPanel result={repair} obsidianCtx={obsidianCtx} /> : null}
    </>
  );
}

function OkfPanel({
  result,
  obsidianCtx,
}: {
  result: OkfCheckResult;
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  const tone: Health =
    result.failed || result.errors.length > 0 ? "bad" : result.notes.length > 0 ? "warn" : "ok";
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          OKF check · {result.status}
          {result.failed ? " (failed)" : ""}
        </h3>
        <HealthChip tone={tone} />
      </div>
      <p class="muted">
        {result.concept_files_checked} concepts · {result.reserved_files_checked} reserved ·{" "}
        {result.errors.length} errors · {result.notes.length} notes
      </p>
      {result.errors.length > 0 ? (
        <ul class="violation-list">
          {result.errors.slice(0, 20).map((err, index) => (
            <li class="health-bad" key={`${err.path}-${index}`}>
              <ObsidianFileLink ctx={obsidianCtx} relativePath={err.path}>
                <strong>{err.path}</strong>
              </ObsidianFileLink>
              <span class="check-code">{err.code}</span>
              <p>{err.message}</p>
            </li>
          ))}
        </ul>
      ) : null}
      {result.notes.length > 0 ? (
        <ul class="violation-list">
          {result.notes.slice(0, 12).map((warning) => (
            <li class="health-warn" key={warning}>
              <p>{warning}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function RepairPanel({
  result,
  obsidianCtx,
}: {
  result: OkfRepairResult;
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  const tone: Health = result.files_changed.length === 0 ? "ok" : result.dry_run ? "warn" : "ok";
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          OKF repair · {result.mode}
          {result.dry_run ? " (preview)" : " (applied)"}
        </h3>
        <HealthChip tone={tone} />
      </div>
      <p class="muted">
        {result.files_changed.length} file{result.files_changed.length === 1 ? "" : "s"}
        {result.commit_sha ? ` · commit ${result.commit_sha.slice(0, 8)}` : ""}
        {result.report_path ? (
          <>
            {" · report "}
            <ObsidianFileLink ctx={obsidianCtx} relativePath={result.report_path}>
              {result.report_path}
            </ObsidianFileLink>
          </>
        ) : null}
      </p>
      {result.files_changed.length === 0 ? (
        <p class="tone-ok">Nothing to change.</p>
      ) : (
        <ul class="violation-list">
          {result.files_changed.map((path) => (
            <li class={result.dry_run ? "health-warn" : "health-ok"} key={path}>
              <ObsidianFileLink ctx={obsidianCtx} relativePath={path}>
                <strong>{path}</strong>
              </ObsidianFileLink>
            </li>
          ))}
        </ul>
      )}
      {result.notes.length > 0 ? (
        <ul class="violation-list">
          {result.notes.slice(0, 12).map((warning) => (
            <li class="health-warn" key={warning}>
              <p>{warning}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
