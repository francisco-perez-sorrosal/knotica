import { useEffect, useRef, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type {
  DirtyEntry,
  DoctorRepairResult,
  DoctorReport,
  LoopOnceResult,
  OkfCheckResult,
  OkfRepairResult,
  VaultLintResult,
  WikiStatus,
} from "./types";

type ActionBusy =
  | null
  | "refresh"
  | "fix"
  | "doctor-dry"
  | "doctor-apply"
  | "okf-dry"
  | "okf-apply"
  | "loop";
type CheckTab = "doctor" | "lint" | "okf" | "loop";

/** Green = healthy, yellow = in progress / needs attention, red = broken. */
type Health = "ok" | "warn" | "bad";

const HEALTH_LABEL: Record<Health, string> = {
  ok: "OK",
  warn: "Watch",
  bad: "Fix",
};

const CHECK_TABS: Array<{ id: CheckTab; label: string }> = [
  { id: "doctor", label: "Doctor" },
  { id: "lint", label: "Lint" },
  { id: "okf", label: "OKF" },
  { id: "loop", label: "Loop" },
];

type TopicRow = WikiStatus["topics"][number];

export function VaultPane({
  client,
  catalog,
  status,
  topic,
  vault,
  onSelectTopic,
}: {
  client: ToolClient | null;
  catalog: WikiStatus | null;
  status: WikiStatus | null;
  topic: string;
  vault: string;
  onSelectTopic: (topic: string) => void;
}) {
  const [busy, setBusy] = useState<ActionBusy>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [checkTab, setCheckTab] = useState<CheckTab>("doctor");
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [doctorRepair, setDoctorRepair] = useState<DoctorRepairResult | null>(null);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [deleteUntracked, setDeleteUntracked] = useState(false);
  const [lint, setLint] = useState<VaultLintResult | null>(null);
  const [okf, setOkf] = useState<OkfCheckResult | null>(null);
  const [repair, setRepair] = useState<OkfRepairResult | null>(null);
  const [loopResult, setLoopResult] = useState<LoopOnceResult | null>(null);
  const [lintScope, setLintScope] = useState<"topic" | "vault">("topic");
  const inFlight = useRef(false);
  const loadGen = useRef(0);

  const totals = catalog?.totals;
  const threshold = catalog?.compile_ready_threshold ?? 20;
  const topics = catalog?.topics ?? [];
  const loop = status?.loop;
  const gate = status?.gate;
  const liveLoop = Boolean(
    loop?.stage && !["idle", "passed", "failed"].includes(loop.stage),
  );
  // Wait until wiki_status has named the vault — never call tools with the "…" placeholder.
  const vaultReady = Boolean(vault || catalog?.vault_name);

  const lintTone = lintToneFor(totals?.lint_violations);
  const unpushedTone = unpushedToneFor(catalog?.unpushed);
  const curatedTone = curatedToneFor(totals?.curated ?? 0, threshold, topics);
  const pagesTone: Health = (totals?.pages ?? 0) > 0 ? "ok" : "warn";
  const topicsTone: Health = (totals?.topics ?? 0) > 0 ? "ok" : "warn";
  const lastLintTone: Health = catalog?.last_lint ? "ok" : "warn";
  const vaultHealth = worstHealth(
    lintTone,
    unpushedTone,
    curatedTone,
    pagesTone,
    topicsTone,
    gateTone(gate?.state),
    liveLoop ? "warn" : "ok",
  );

  async function runAction<T>(
    kind: Exclude<ActionBusy, null>,
    work: () => Promise<T>,
    apply: (value: T) => void,
  ) {
    if (!client || inFlight.current) return;
    const gen = ++loadGen.current;
    inFlight.current = true;
    setBusy(kind);
    setActionError(null);
    try {
      const value = await work();
      if (gen === loadGen.current) apply(value);
    } catch (cause) {
      if (gen === loadGen.current) setActionError(formatActionError(cause));
    } finally {
      if (gen === loadGen.current) {
        inFlight.current = false;
        setBusy(null);
      }
    }
  }

  async function refreshCheck(tab: CheckTab = checkTab, withFix = false) {
    if (!client || !vaultReady) return;
    const vaultArg = vault || catalog?.vault_name || "";
    if (tab === "doctor") {
      await runAction(
        withFix ? "fix" : "refresh",
        () => client.doctorRun(vaultArg, false, withFix),
        setDoctor,
      );
      return;
    }
    if (tab === "lint") {
      const scopeTopic = lintScope === "topic" ? topic : "";
      await runAction("refresh", () => client.vaultLint(scopeTopic, vaultArg), setLint);
      return;
    }
    if (tab === "okf") {
      await runAction("refresh", () => client.okfCheck(vaultArg, false), setOkf);
    }
  }

  // Auto-load the active check once the vault identity is known (page refresh / tab change).
  useEffect(() => {
    if (!client || !vaultReady) return;
    if (checkTab === "loop") return;
    void refreshCheck(checkTab, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: refresh on tab/scope/topic/vault
  }, [client, checkTab, topic, vault, vaultReady, lintScope]);

  return (
    <main class="pane-main vault">
      <section class="ingest-hero">
        <div>
          <p class="eyebrow">Vault storage</p>
          <h2 class="ingest-heading">What lives in this wiki</h2>
          <p class="muted">
            Live inventory from <code>wiki_status</code> — the same facts as{" "}
            <code>knotica status</code>. Open a check below to inspect state; remediations sit on
            the side.
          </p>
        </div>
        <div
          class={`ingest-pulse health-${vaultHealth} ${liveLoop ? "live" : catalog ? "idle" : "empty"}`}
        >
          <span class="pulse-dot" aria-hidden="true" />
          <strong>
            {liveLoop
              ? `Loop · ${loop?.stage}`
              : catalog
                ? `Vault · ${HEALTH_LABEL[vaultHealth]}`
                : "Waiting"}
          </strong>
          <small>
            {liveLoop
              ? loop?.candidate_branch || "candidate in flight"
              : catalog
                ? healthSummary(vaultHealth, totals, catalog.unpushed)
                : "Status streams once MCP connects."}
          </small>
        </div>
      </section>

      <p class="health-legend" aria-label="Health legend">
        <span class="health-chip ok">OK</span>
        <span>healthy</span>
        <span class="health-chip warn">Watch</span>
        <span>intermediate</span>
        <span class="health-chip bad">Fix</span>
        <span>broken</span>
      </p>

      <section class="vault-stats" aria-label="Vault totals">
        <Stat
          label="Topics"
          value={totals?.topics ?? "—"}
          tone={topicsTone}
          hint={topicsTone === "ok" ? "topics present" : "no topics yet"}
        />
        <Stat
          label="Pages"
          value={totals?.pages ?? "—"}
          tone={pagesTone}
          hint={pagesTone === "ok" ? "content stored" : "empty wiki"}
        />
        <Stat
          label="Curated"
          value={totals?.curated ?? "—"}
          tone={curatedTone}
          hint={
            curatedTone === "ok"
              ? "compile-ready reached"
              : curatedTone === "warn"
                ? `building toward ${threshold}`
                : "no curated examples"
          }
        />
        <Stat
          label="Lint hits"
          value={totals?.lint_violations ?? "—"}
          tone={lintTone}
          hint={
            lintTone === "ok"
              ? "clean"
              : lintTone === "warn"
                ? "a few violations"
                : "many violations"
          }
        />
        <Stat
          label="Unpushed"
          value={catalog?.unpushed ?? "—"}
          tone={unpushedTone}
          hint={
            catalog?.unpushed == null
              ? "no upstream"
              : unpushedTone === "ok"
                ? "in sync"
                : "ahead of remote"
          }
        />
        <Stat
          label="Last lint"
          value={catalog?.last_lint || "never"}
          tone={lastLintTone}
          hint={lastLintTone === "ok" ? "lint has run" : "never linted"}
          small
        />
      </section>

      <div class="vault-layout">
        <section class="panel vault-topics">
          <header>
            <div>
              <h2>Topics</h2>
              <p>
                Red = lint problems · yellow = building toward {threshold} curated · green =
                compile-ready and clean
              </p>
            </div>
          </header>
          {topics.length === 0 ? (
            <p class="muted empty-timeline">No topics yet in this vault.</p>
          ) : (
            <ul class="topic-inventory">
              {topics.map((row) => {
                const health = topicHealth(row, threshold);
                const curatedPct = Math.min(100, Math.round((row.curated / threshold) * 100));
                const active = row.topic === topic;
                return (
                  <li key={row.topic}>
                    <button
                      type="button"
                      class={`topic-card health-${health} ${active ? "active" : ""}`}
                      onClick={() => onSelectTopic(row.topic)}
                    >
                      <span class="topic-card-top">
                        <strong>{row.topic}</strong>
                        <span class={`health-chip ${health}`}>{HEALTH_LABEL[health]}</span>
                      </span>
                      <span class="topic-card-meta">
                        {row.pages} pages ·{" "}
                        <span class={row.lint_violations > 0 ? "tone-bad" : "tone-ok"}>
                          {row.lint_violations} lint
                        </span>
                      </span>
                      <span class={`curate-track health-${health}`} aria-hidden="true">
                        <span class="curate-fill" style={{ width: `${curatedPct}%` }} />
                      </span>
                      <span class="topic-card-meta">
                        {row.curated}/{threshold} curated
                        {row.to_compile_ready > 0
                          ? ` · ${row.to_compile_ready} to go`
                          : " · compile-ready"}
                        {row.last_eval
                          ? ` · eval ${row.last_eval.scalar.toFixed(3)}`
                          : " · no eval yet"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section class="panel vault-checks">
          <header>
            <div>
              <h2>Checks</h2>
              <p>Inspect one surface at a time — remediations stay on the right</p>
            </div>
          </header>

          <nav class="check-tabs" aria-label="Vault checks">
            {CHECK_TABS.map((tab) => (
              <button
                type="button"
                key={tab.id}
                class={checkTab === tab.id ? "active" : ""}
                onClick={() => setCheckTab(tab.id)}
              >
                {tab.label}
                <span class={`health-chip ${tabChipTone(tab.id, doctor, lint, okf, gate, loop, liveLoop)}`}>
                  {HEALTH_LABEL[tabChipTone(tab.id, doctor, lint, okf, gate, loop, liveLoop)]}
                </span>
              </button>
            ))}
          </nav>

          <div class="check-workspace">
            <div class="check-status">
              {actionError ? <aside role="alert">Action failed: {actionError}</aside> : null}
              {checkTab === "doctor" ? (
                <DoctorPanel
                  report={doctor}
                  busy={!vaultReady || busy === "refresh" || busy === "fix"}
                  waitingVault={!vaultReady}
                />
              ) : null}
              {checkTab === "lint" ? (
                <LintPanel
                  result={lint}
                  busy={!vaultReady || busy === "refresh"}
                  waitingVault={!vaultReady}
                />
              ) : null}
              {checkTab === "okf" ? (
                <OkfStatus
                  okf={okf}
                  repair={repair}
                  busy={!vaultReady || busy === "refresh"}
                  waitingVault={!vaultReady}
                />
              ) : null}
              {checkTab === "loop" ? (
                <LoopStatus
                  topic={topic}
                  gate={gate}
                  loop={loop}
                  live={liveLoop}
                  result={loopResult}
                />
              ) : null}
            </div>

            <aside class="check-actions" aria-label="Remediations">
              <h3>Remediations</h3>
              {checkTab === "doctor" ? (
                <DoctorRemediations
                  client={client}
                  vault={vault || catalog?.vault_name || ""}
                  busy={busy}
                  doctorRepair={doctorRepair}
                  selectedPaths={selectedPaths}
                  deleteUntracked={deleteUntracked}
                  onRefresh={() => void refreshCheck("doctor", false)}
                  onFixGuidance={() => void refreshCheck("doctor", true)}
                  onDeleteUntrackedChange={setDeleteUntracked}
                  onTogglePath={(path) =>
                    setSelectedPaths((prev) =>
                      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
                    )
                  }
                  onSelectTracked={() => {
                    const tracked = (doctorRepair?.entries ?? [])
                      .filter((e) => e.tracked)
                      .map((e) => e.path);
                    setSelectedPaths(tracked);
                  }}
                  onDryRun={() =>
                    void runAction(
                      "doctor-dry",
                      () => client!.doctorRepair("dry-run", vault || catalog?.vault_name || ""),
                      (result) => {
                        setDoctorRepair(result);
                        const tracked = (result.entries ?? [])
                          .filter((e) => e.tracked)
                          .map((e) => e.path);
                        setSelectedPaths(tracked);
                      },
                    )
                  }
                  onApply={(allTracked) => {
                    const vaultArg = vault || catalog?.vault_name || "";
                    const paths = allTracked ? [] : selectedPaths;
                    const hasUntracked = paths.some((path) =>
                      (doctorRepair?.entries ?? []).some((e) => e.path === path && e.untracked),
                    );
                    const confirmMsg = allTracked
                      ? "Restore every tracked dirty path to HEAD? Untracked files stay. Same as `knotica doctor repair --apply --all-tracked`."
                      : `Restore ${paths.length} selected path(s) to HEAD? Same as \`knotica doctor repair --apply --paths …\`${
                          hasUntracked && deleteUntracked
                            ? " Untracked selections will be deleted."
                            : ""
                        }`;
                    if (!window.confirm(confirmMsg)) return;
                    void runAction(
                      "doctor-apply",
                      () =>
                        client!.doctorRepair(
                          "apply",
                          vaultArg,
                          paths,
                          allTracked,
                          deleteUntracked,
                        ),
                      (result) => {
                        setDoctorRepair(result);
                        setSelectedPaths([]);
                        void refreshCheck("doctor", false);
                      },
                    );
                  }}
                />
              ) : null}

              {checkTab === "lint" ? (
                <>
                  <p class="muted">Mechanical lint for the selected scope</p>
                  <label class="scope-picker">
                    <span>Scope</span>
                    <select
                      value={lintScope}
                      onChange={(event) =>
                        setLintScope((event.target as HTMLSelectElement).value as "topic" | "vault")
                      }
                    >
                      <option value="topic">Topic · {topic}</option>
                      <option value="vault">Whole vault</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={!client || busy !== null}
                    onClick={() => void refreshCheck("lint")}
                  >
                    {busy === "refresh" ? "Linting…" : "Refresh lint"}
                  </button>
                  <p class="action-note">
                    Fixes are listed per violation — edit pages via Claude / Obsidian; there is no
                    auto-lint repair.
                  </p>
                </>
              ) : null}

              {checkTab === "okf" ? (
                <>
                  <p class="muted">
                    Same as <code>knotica okf check|repair</code>
                  </p>
                  <button
                    type="button"
                    disabled={!client || busy !== null}
                    onClick={() => void refreshCheck("okf")}
                  >
                    {busy === "refresh" ? "Checking…" : "Refresh OKF check"}
                  </button>
                  <button
                    type="button"
                    disabled={!client || busy !== null}
                    onClick={() =>
                      void runAction(
                        "okf-dry",
                        () => client!.okfRepair("dry-run", vault, false),
                        setRepair,
                      )
                    }
                  >
                    {busy === "okf-dry" ? "Previewing…" : "Repair dry-run"}
                  </button>
                  <button
                    type="button"
                    class="danger"
                    disabled={!client || busy !== null}
                    onClick={() => {
                      if (
                        !window.confirm(
                          "Apply OKF repair? This writes files and creates one git commit (same as `knotica okf repair --apply`).",
                        )
                      ) {
                        return;
                      }
                      void runAction(
                        "okf-apply",
                        () => client!.okfRepair("apply", vault, false),
                        setRepair,
                      );
                    }}
                  >
                    {busy === "okf-apply" ? "Applying…" : "Repair apply"}
                  </button>
                </>
              ) : null}

              {checkTab === "loop" ? (
                <>
                  <p class="muted">
                    Same as <code>loop_runner.py --once</code> for <strong>{topic}</strong>
                  </p>
                  <button
                    type="button"
                    disabled={!client || busy !== null}
                    title="May run a full LLM eval"
                    onClick={() =>
                      void runAction(
                        "loop",
                        () => client!.loopRunOnce(topic, vault),
                        setLoopResult,
                      )
                    }
                  >
                    {busy === "loop" ? "Processing…" : "Process one candidate"}
                  </button>
                  <p class="action-note">
                    Watch stage / gate update live from <code>wiki_status</code>. Requires a frozen
                    baseline and a pending <code>loop/c/*</code> branch.
                  </p>
                </>
              ) : null}
            </aside>
          </div>
        </section>
      </div>
    </main>
  );
}

function Stat({
  label,
  value,
  tone,
  hint,
  small,
}: {
  label: string;
  value: string | number;
  tone: Health;
  hint: string;
  small?: boolean;
}) {
  return (
    <div class={`vault-stat health-${tone} ${small ? "small" : ""}`}>
      <span class="stat-label-row">
        <span>{label}</span>
        <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>
      </span>
      <strong>{value}</strong>
      <em class="stat-hint">{hint}</em>
    </div>
  );
}

function DoctorRemediations({
  client,
  vault,
  busy,
  doctorRepair,
  selectedPaths,
  deleteUntracked,
  onRefresh,
  onFixGuidance,
  onDeleteUntrackedChange,
  onTogglePath,
  onSelectTracked,
  onDryRun,
  onApply,
}: {
  client: ToolClient | null;
  vault: string;
  busy: ActionBusy;
  doctorRepair: DoctorRepairResult | null;
  selectedPaths: string[];
  deleteUntracked: boolean;
  onRefresh: () => void;
  onFixGuidance: () => void;
  onDeleteUntrackedChange: (value: boolean) => void;
  onTogglePath: (path: string) => void;
  onSelectTracked: () => void;
  onDryRun: () => void;
  onApply: (allTracked: boolean) => void;
}) {
  const entries: DirtyEntry[] = doctorRepair?.entries ?? [];
  const applyBusy = busy === "doctor-apply";
  const dryBusy = busy === "doctor-dry";
  const canApplySelected = selectedPaths.length > 0;
  const canApplyTracked = (doctorRepair?.tracked_paths?.length ?? 0) > 0;

  return (
    <>
      <p class="muted">
        Same as <code>knotica doctor</code> / <code>doctor repair</code>
      </p>
      <button type="button" disabled={!client || busy !== null} onClick={onRefresh}>
        {busy === "refresh" ? "Running…" : "Refresh doctor"}
      </button>
      <button type="button" disabled={!client || busy !== null} onClick={onFixGuidance}>
        {busy === "fix" ? "Loading…" : "doctor --fix (guidance)"}
      </button>
      <button type="button" disabled={!client || busy !== null} onClick={onDryRun}>
        {dryBusy ? "Listing…" : "Repair dry-run"}
      </button>
      <p class="action-note">
        <code>--fix</code> lists CLI commands only. Dry-run / apply restore path-scoped paths to
        HEAD (never <code>git restore .</code>).
      </p>

      {doctorRepair ? (
        <div class="doctor-repair-box">
          <p class="muted">
            {doctorRepair.mode === "apply"
              ? doctorRepair.message || `Restored ${(doctorRepair.restored ?? []).length} path(s).`
              : doctorRepair.dirty_count
                ? `${doctorRepair.dirty_count} dirty path(s)`
                : "Work tree clean"}
          </p>
          {entries.length > 0 ? (
            <>
              <div class="doctor-repair-toolbar">
                <button type="button" disabled={busy !== null} onClick={onSelectTracked}>
                  Select tracked
                </button>
                <label class="repair-flag">
                  <input
                    type="checkbox"
                    checked={deleteUntracked}
                    disabled={busy !== null}
                    onChange={(event) =>
                      onDeleteUntrackedChange((event.target as HTMLInputElement).checked)
                    }
                  />
                  Delete untracked
                </label>
              </div>
              <ul class="doctor-path-list">
                {entries.map((entry) => (
                  <li key={entry.path}>
                    <label>
                      <input
                        type="checkbox"
                        checked={selectedPaths.includes(entry.path)}
                        disabled={busy !== null}
                        onChange={() => onTogglePath(entry.path)}
                      />
                      <code class="path-code">{entry.code}</code>
                      <span class="path-name">{entry.path}</span>
                      <em>{entry.untracked ? "untracked" : "tracked"}</em>
                    </label>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                disabled={!client || busy !== null || !canApplySelected}
                onClick={() => onApply(false)}
              >
                {applyBusy ? "Restoring…" : `Apply selected (${selectedPaths.length})`}
              </button>
              <button
                type="button"
                class="danger"
                disabled={!client || busy !== null || !canApplyTracked}
                onClick={() => onApply(true)}
              >
                {applyBusy ? "Restoring…" : "Apply all tracked"}
              </button>
            </>
          ) : null}
          {doctorRepair.restored && doctorRepair.restored.length > 0 ? (
            <p class="action-note">Restored: {doctorRepair.restored.join(", ")}</p>
          ) : null}
        </div>
      ) : (
        <p class="action-note">
          Run repair dry-run to list dirty paths for vault{vault ? ` · ${vault}` : ""}.
        </p>
      )}
    </>
  );
}

function DoctorPanel({
  report,
  busy,
  waitingVault,
}: {
  report: DoctorReport | null;
  busy: boolean;
  waitingVault: boolean;
}) {
  if (!report) {
    return (
      <p class="muted empty-check">
        {waitingVault ? "Waiting for vault…" : busy ? "Running doctor…" : "No doctor result yet."}
      </p>
    );
  }
  const tone: Health =
    report.summary.fail > 0 ? "bad" : report.summary.warn > 0 ? "warn" : "ok";
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          Doctor · {report.summary.pass} pass / {report.summary.warn} warn / {report.summary.fail}{" "}
          fail
        </h3>
        <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>
      </div>
      <ul class="check-list">
        {report.checks.map((row) => (
          <li class={`check-${row.status.toLowerCase()}`} key={row.name}>
            <span class="check-status">{row.status}</span>
            <div>
              <strong>{row.name}</strong>
              <p>{row.message}</p>
              {row.remediation && row.status !== "PASS" ? (
                <p class="fix-hint">→ {row.remediation}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {report.fix_guidance ? (
        <div class="fix-guidance">
          <h4>doctor --fix (guidance)</h4>
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
  waitingVault,
}: {
  result: VaultLintResult | null;
  busy: boolean;
  waitingVault: boolean;
}) {
  if (!result) {
    return (
      <p class="muted empty-check">
        {waitingVault ? "Waiting for vault…" : busy ? "Linting…" : "No lint result yet."}
      </p>
    );
  }
  const tone = lintToneFor(result.violations.length);
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          Mechanical lint
          {result.topic ? ` · ${result.topic}` : " · vault"} · {result.violations.length} hit
          {result.violations.length === 1 ? "" : "s"}
        </h3>
        <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>
      </div>
      {result.violations.length === 0 ? (
        <p class="tone-ok">No mechanical violations.</p>
      ) : (
        <ul class="violation-list">
          {result.violations.slice(0, 40).map((row, index) => (
            <li class="health-bad" key={`${row.path}-${row.check}-${index}`}>
              <strong>
                {row.path}
                {row.line != null ? `:${row.line}` : ""}
              </strong>
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
  waitingVault,
}: {
  okf: OkfCheckResult | null;
  repair: OkfRepairResult | null;
  busy: boolean;
  waitingVault: boolean;
}) {
  if (!okf && !repair) {
    return (
      <p class="muted empty-check">
        {waitingVault ? "Waiting for vault…" : busy ? "Checking OKF…" : "No OKF result yet."}
      </p>
    );
  }
  return (
    <>
      {okf ? <OkfPanel result={okf} /> : null}
      {repair ? <RepairPanel result={repair} /> : null}
    </>
  );
}

function OkfPanel({ result }: { result: OkfCheckResult }) {
  const tone: Health =
    result.failed || result.errors.length > 0
      ? "bad"
      : result.notes.length > 0
        ? "warn"
        : "ok";
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          OKF check · {result.status}
          {result.failed ? " (failed)" : ""}
        </h3>
        <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>
      </div>
      <p class="muted">
        {result.concept_files_checked} concepts · {result.reserved_files_checked} reserved ·{" "}
        {result.errors.length} errors · {result.notes.length} notes
      </p>
      {result.errors.length > 0 ? (
        <ul class="violation-list">
          {result.errors.slice(0, 20).map((err, index) => (
            <li class="health-bad" key={`${err.path}-${index}`}>
              <strong>{err.path}</strong>
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

function RepairPanel({ result }: { result: OkfRepairResult }) {
  const tone: Health =
    result.files_changed.length === 0 ? "ok" : result.dry_run ? "warn" : "ok";
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>
          OKF repair · {result.mode}
          {result.dry_run ? " (preview)" : " (applied)"}
        </h3>
        <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>
      </div>
      <p class="muted">
        {result.files_changed.length} file{result.files_changed.length === 1 ? "" : "s"}
        {result.commit_sha ? ` · commit ${result.commit_sha.slice(0, 8)}` : ""}
        {result.report_path ? ` · report ${result.report_path}` : ""}
      </p>
      {result.files_changed.length === 0 ? (
        <p class="tone-ok">Nothing to change.</p>
      ) : (
        <ul class="violation-list">
          {result.files_changed.map((path) => (
            <li class={result.dry_run ? "health-warn" : "health-ok"} key={path}>
              <strong>{path}</strong>
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

function LoopStatus({
  topic,
  gate,
  loop,
  live,
  result,
}: {
  topic: string;
  gate: WikiStatus["gate"] | undefined;
  loop: WikiStatus["loop"] | undefined;
  live: boolean;
  result: LoopOnceResult | null;
}) {
  const tone = loopWatchHealth(gate?.state, loop?.stage, live);
  return (
    <div class={`remediation-panel health-${tone}`}>
      <div class="loop-watch-top">
        <h3>Loop · {topic}</h3>
        <span class={`health-chip ${tone}`}>{HEALTH_LABEL[tone]}</span>
      </div>
      <p>
        Gate <strong class={`tone-${gateTone(gate?.state)}`}>{gate?.state ?? "unknown"}</strong>
        {gate?.baseline != null ? ` · baseline ${gate.baseline.toFixed(4)}` : " · no baseline"}
        {gate?.last_scalar != null ? ` · last ${gate.last_scalar.toFixed(4)}` : ""}
      </p>
      <p>
        Stage{" "}
        <strong class={`tone-${stageTone(loop?.stage, live)}`}>{loop?.stage ?? "idle"}</strong>
        {loop?.candidate_branch ? ` · ${loop.candidate_branch}` : ""}
        {loop?.last_decision ? ` · last ${loop.last_decision}` : ""}
      </p>
      {result ? (
        <p
          class={`action-result tone-${result.acted && result.decision === "fail" ? "bad" : result.acted ? "ok" : "warn"}`}
        >
          {result.acted ? "Acted" : "No-op"}: {result.message}
          {result.scalar != null ? ` (scalar ${result.scalar.toFixed(4)})` : ""}
        </p>
      ) : (
        <p class="muted">Press Process one candidate to run a cycle.</p>
      )}
    </div>
  );
}

function tabChipTone(
  tab: CheckTab,
  doctor: DoctorReport | null,
  lint: VaultLintResult | null,
  okf: OkfCheckResult | null,
  gate: WikiStatus["gate"] | undefined,
  loop: WikiStatus["loop"] | undefined,
  live: boolean,
): Health {
  if (tab === "doctor") {
    if (!doctor) return "warn";
    if (doctor.summary.fail > 0) return "bad";
    if (doctor.summary.warn > 0) return "warn";
    return "ok";
  }
  if (tab === "lint") {
    if (!lint) return "warn";
    return lintToneFor(lint.violations.length);
  }
  if (tab === "okf") {
    if (!okf) return "warn";
    if (okf.failed || okf.errors.length > 0) return "bad";
    if (okf.notes.length > 0) return "warn";
    return "ok";
  }
  return loopWatchHealth(gate?.state, loop?.stage, live);
}

function topicHealth(row: TopicRow, threshold: number): Health {
  if (row.lint_violations > 0) return "bad";
  if (row.curated < threshold || !row.last_eval) return "warn";
  return "ok";
}

function lintToneFor(count: number | null | undefined): Health {
  const n = count ?? 0;
  if (n <= 0) return "ok";
  if (n < 10) return "warn";
  return "bad";
}

function unpushedToneFor(count: number | null | undefined): Health {
  if (count == null || count === 0) return "ok";
  return "warn";
}

function curatedToneFor(curated: number, threshold: number, topics: TopicRow[]): Health {
  if (curated <= 0) return "bad";
  const anyReady = topics.some((row) => row.curated >= threshold);
  if (anyReady) return "ok";
  return "warn";
}

function gateTone(state: string | null | undefined): Health {
  if (state === "pass") return "ok";
  if (state === "fail") return "bad";
  return "warn";
}

function stageTone(stage: string | null | undefined, live: boolean): Health {
  if (stage === "failed") return "bad";
  if (stage === "passed") return "ok";
  if (live || stage === "evaluating" || stage === "merging" || stage === "reverting") {
    return "warn";
  }
  return "ok";
}

function loopWatchHealth(
  gate: string | null | undefined,
  stage: string | null | undefined,
  live: boolean,
): Health {
  return worstHealth(gateTone(gate), stageTone(stage, live));
}

function worstHealth(...tones: Health[]): Health {
  if (tones.includes("bad")) return "bad";
  if (tones.includes("warn")) return "warn";
  return "ok";
}

function formatActionError(cause: unknown): string {
  if (cause instanceof Error && cause.message) return cause.message;
  if (typeof cause === "string" && cause.trim()) return cause;
  if (cause && typeof cause === "object") {
    const record = cause as Record<string, unknown>;
    const nested = record.error;
    if (nested && typeof nested === "object") {
      const message = (nested as Record<string, unknown>).message;
      if (typeof message === "string" && message.trim()) return message;
    }
    if (typeof record.message === "string" && record.message.trim()) return record.message;
  }
  return "Action failed";
}

function healthSummary(
  health: Health,
  totals: WikiStatus["totals"] | undefined,
  unpushed: number | null | undefined,
): string {
  const parts = [
    `${totals?.topics ?? 0} topics`,
    `${totals?.pages ?? 0} pages`,
    `${totals?.lint_violations ?? 0} lint`,
  ];
  if ((unpushed ?? 0) > 0) parts.push(`${unpushed} unpushed`);
  if (health === "ok") return `${parts.join(" · ")} · all clear`;
  if (health === "warn") return `${parts.join(" · ")} · something needs attention`;
  return `${parts.join(" · ")} · fix the red items`;
}
