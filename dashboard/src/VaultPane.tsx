import { useEffect, useRef, useState } from "preact/hooks";

import { CompilePanel } from "./CompilePanel";
import { MetadataTreePanel } from "./MetadataTreePanel";
import { ScoreboardPanel } from "./ScoreboardPanel";
import {
  ObsidianFileLink,
  type ObsidianContext,
} from "./obsidianLinks";
import { formatToolFailure, type ToolClient } from "./toolClient";
import { queryTrainCount } from "./topicHelpers";
import type {
  DirtyEntry,
  DoctorRepairResult,
  DoctorReport,
  LlmAvailability,
  LoopOnceResult,
  LoopProgress,
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
  | "loop"
  | "bootstrap-train";
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

/** Ephemeral lint run recorded in-dashboard (`vault_lint` does not mutate the vault). */
type LintSessionMeta = {
  at: Date;
  scope: "topic" | "vault";
  scopeLabel: string;
  violations: number;
};

/**
 * Result of the last "Bootstrap trainset" action, kept independent of `to_compile_ready` so
 * it survives the topic flipping to compile-ready on the next status poll. Cleared on
 * dismiss or when the pane unmounts.
 */
type BootstrapNote = {
  topic: string;
  kind: "ok" | "error";
  text: string;
};

export function VaultPane({
  client,
  catalog,
  status,
  topic,
  vault,
  obsidianCtx,
  onSelectTopic,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  catalog: WikiStatus | null;
  status: WikiStatus | null;
  topic: string;
  vault: string;
  obsidianCtx: ObsidianContext;
  onSelectTopic: (topic: string) => void;
  onStatusRefresh?: () => void | Promise<void>;
}) {
  const [busy, setBusy] = useState<ActionBusy>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [checkTab, setCheckTab] = useState<CheckTab>("doctor");
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [doctorRepair, setDoctorRepair] = useState<DoctorRepairResult | null>(null);
  const [lastDoctorApply, setLastDoctorApply] = useState<{
    restored: string[];
    message?: string;
  } | null>(null);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [deleteUntracked, setDeleteUntracked] = useState(false);
  const [lint, setLint] = useState<VaultLintResult | null>(null);
  const [okf, setOkf] = useState<OkfCheckResult | null>(null);
  const [repair, setRepair] = useState<OkfRepairResult | null>(null);
  const [loopResult, setLoopResult] = useState<LoopOnceResult | null>(null);
  const [lintScope, setLintScope] = useState<"topic" | "vault">("topic");
  const [lastLintSession, setLastLintSession] = useState<LintSessionMeta | null>(null);
  const [bootstrapNote, setBootstrapNote] = useState<BootstrapNote | null>(null);
  const inFlight = useRef(false);
  const loadGen = useRef(0);

  const totals = catalog?.totals;
  const threshold = catalog?.compile_ready_threshold ?? 30;
  const goldenFloor = catalog?.eval_min_golden ?? 20;
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
  const vaultKey = vault || catalog?.vault_name || "";
  const lastLintValue = lastLintSession
    ? formatLintSessionTime(lastLintSession.at)
    : catalog?.last_lint || "never";
  const lastLintHint = lastLintSession
    ? lintSessionHint(lastLintSession)
    : catalog?.last_lint
      ? "from vault log (mutating lint ops)"
      : "never linted this session";
  const lastLintTone: Health = lastLintSession || catalog?.last_lint ? "ok" : "warn";
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
    apply: (value: T) => void | Promise<void>,
  ) {
    if (!client || inFlight.current) return;
    const gen = ++loadGen.current;
    inFlight.current = true;
    setBusy(kind);
    setActionError(null);
    try {
      const value = await work();
      if (gen === loadGen.current) {
        inFlight.current = false;
        setBusy(null);
        await apply(value);
      }
    } catch (cause) {
      if (gen === loadGen.current) setActionError(formatActionError(cause));
    } finally {
      if (gen === loadGen.current) {
        inFlight.current = false;
        setBusy(null);
      }
    }
  }

  async function bootstrapTrainset(topicName: string) {
    if (!client || busy !== null) return;
    setBusy("bootstrap-train");
    try {
      const result = await client.datasetsBootstrapTrain(topicName, undefined, vault);
      setBootstrapNote({
        topic: topicName,
        kind: "ok",
        text: `seeded ${result.appended} examples from ${result.pages_read} pages`,
      });
      await onStatusRefresh?.();
    } catch (cause) {
      setBootstrapNote({ topic: topicName, kind: "error", text: formatActionError(cause) });
    } finally {
      setBusy(null);
    }
  }

  async function runDoctorRepairDryRun() {
    if (!client || !vaultReady) return;
    const vaultArg = vault || catalog?.vault_name || "";
    await runAction(
      "doctor-dry",
      () => client.doctorRepair("dry-run", vaultArg),
      (result) => {
        setDoctorRepair(result);
        const tracked = (result.entries ?? [])
          .filter((e) => e.tracked)
          .map((e) => e.path);
        setSelectedPaths(tracked);
      },
    );
  }

  async function refreshCheck(tab: CheckTab = checkTab, withFix = false) {
    if (!client || !vaultReady) return;
    const vaultArg = vault || catalog?.vault_name || "";
    if (tab === "doctor") {
      await runAction(
        withFix ? "fix" : "refresh",
        () => client.doctorRun(vaultArg, false, withFix),
        async (report) => {
          setDoctor(report);
          if (withFix || doctorNeedsRepair(report)) {
            await runDoctorRepairDryRun();
          }
        },
      );
      return;
    }
    if (tab === "lint") {
      const scopeTopic = lintScope === "topic" ? topic : "";
      await runAction(
        "refresh",
        () => client.vaultLint(scopeTopic, vaultArg),
        (result) => {
          setLint(result);
          setLastLintSession({
            at: new Date(),
            scope: lintScope,
            scopeLabel: lintScope === "topic" ? topic : "whole vault",
            violations: result.violations.length,
          });
        },
      );
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

  useEffect(() => {
    setLastLintSession(null);
  }, [vaultKey]);

  return (
    <main class="pane-main vault">

      <section class="ingest-hero">
        <div>
          <p class="eyebrow">Vault storage</p>
          <h2 class="ingest-heading">What lives in this wiki</h2>
          <p class="muted">
            Inventory + compile flywheel for the selected topic. Heal (Arena) and Compile both prove
            out in Ask — engines stay invisible.
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
          label={lastLintSession ? "Last lint (session)" : "Last lint"}
          value={lastLintValue}
          tone={lastLintTone}
          hint={lastLintHint}
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
          <MetadataTreePanel
            client={client}
            vault={vault || catalog?.vault_name || ""}
            topic={topic}
            vaultReady={vaultReady}
            obsidianCtx={obsidianCtx}
          />
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
                        <span class="topic-card-actions">
                          <ObsidianFileLink
                            ctx={obsidianCtx}
                            relativePath={`${row.topic}/SCHEMA.md`}
                            className="obsidian-icon-link"
                            title="Open topic schema in Obsidian"
                            onClick={(event) => event.stopPropagation()}
                          >
                            Obsidian
                          </ObsidianFileLink>
                          <span class={`health-chip ${health}`}>{HEALTH_LABEL[health]}</span>
                        </span>
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
                      <span
                        class="topic-card-meta"
                        title={
                          `Trainset (qa.jsonl query-style): ${queryTrainCount(row)} ` +
                          `(compile needs ≥${threshold}). ` +
                          `Held-out (golden.jsonl): ${row.golden_n ?? 0} ` +
                          `(needs ≥${goldenFloor}).`
                        }
                      >
                        Trainset {queryTrainCount(row)}
                        {queryTrainCount(row) >= threshold
                          ? ` (≥${threshold} ✓)`
                          : ` of ≥${threshold} for compile`}
                        {" · "}
                        Held-out {row.golden_n ?? 0}
                        {(row.golden_n ?? 0) >= goldenFloor
                          ? ` (≥${goldenFloor} ✓)`
                          : ` of ≥${goldenFloor}`}
                        {row.compile_ready
                          ? " · compile-ready"
                          : row.to_compile_ready > 0
                            ? ` · ${row.to_compile_ready} train more`
                            : ""}
                        {row.compiled?.present ? " · Compiled" : ""}
                        {row.last_eval
                          ? ` · eval ${row.last_eval.scalar.toFixed(3)}`
                          : " · no eval yet"}
                      </span>
                    </button>
                    {active ? (
                      <div class="topic-bootstrap-train">
                        {!row.compile_ready ? (
                          <>
                            <button
                              type="button"
                              class="ghost"
                              disabled={
                                !client ||
                                !vaultReady ||
                                busy !== null ||
                                (status ?? catalog)?.llm?.available === false
                              }
                              title={llmUnavailableTooltip((status ?? catalog)?.llm)}
                              onClick={() => void bootstrapTrainset(row.topic)}
                            >
                              {busy === "bootstrap-train"
                                ? bootstrapBusyLabel((status ?? catalog)?.loop.progress)
                                : "Bootstrap trainset"}
                            </button>
                            {!bootstrapNote || bootstrapNote.topic !== row.topic ? (
                              <p class="muted topic-bootstrap-hint">
                                Cold-start: examples are generated from this topic&apos;s own
                                pages; your curated answers replace them over time.
                              </p>
                            ) : null}
                          </>
                        ) : null}
                        {bootstrapNote && bootstrapNote.topic === row.topic ? (
                          <p
                            class={`muted topic-bootstrap-note ${
                              bootstrapNote.kind === "error" ? "tone-bad" : ""
                            }`}
                          >
                            {bootstrapNote.kind === "ok"
                              ? `cold-start: ${bootstrapNote.text}`
                              : bootstrapNote.text}
                            <button
                              type="button"
                              class="topic-bootstrap-dismiss"
                              aria-label="Dismiss"
                              onClick={() => setBootstrapNote(null)}
                            >
                              ×
                            </button>
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
          <CompilePanel
            client={client}
            topic={topic}
            vault={vault}
            status={status ?? catalog}
            onStatusRefresh={onStatusRefresh}
          />
          <ScoreboardPanel
            client={client}
            topic={topic}
            vault={vault}
            status={status ?? catalog}
            onStatusRefresh={onStatusRefresh}
          />
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
            <div class="check-pane">
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
                  obsidianCtx={obsidianCtx}
                />
              ) : null}
              {checkTab === "okf" ? (
                <OkfStatus
                  okf={okf}
                  repair={repair}
                  busy={!vaultReady || busy === "refresh"}
                  waitingVault={!vaultReady}
                  obsidianCtx={obsidianCtx}
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
                  obsidianCtx={obsidianCtx}
                  busy={busy}
                  doctorRepair={doctorRepair}
                  lastApply={lastDoctorApply}
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
                  onDryRun={() => void runDoctorRepairDryRun()}
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
                      async (result) => {
                        setLastDoctorApply({
                          restored: result.restored ?? [],
                          message: result.message,
                        });
                        await runDoctorRepairDryRun();
                        await refreshCheck("doctor", false);
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
  obsidianCtx,
  busy,
  doctorRepair,
  lastApply,
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
  obsidianCtx: ObsidianContext;
  busy: ActionBusy;
  doctorRepair: DoctorRepairResult | null;
  lastApply: { restored: string[]; message?: string } | null;
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
        Restore dirty paths here via <strong>Repair dry-run → Apply</strong> (same as{" "}
        <code>knotica doctor repair</code>). Never runs <code>git restore .</code>.
      </p>
      <button type="button" class="primary" disabled={!client || busy !== null} onClick={onDryRun}>
        {dryBusy ? "Listing…" : "Repair dry-run"}
      </button>
      <button type="button" disabled={!client || busy !== null} onClick={onRefresh}>
        {busy === "refresh" ? "Running…" : "Refresh doctor"}
      </button>
      <button type="button" disabled={!client || busy !== null} onClick={onFixGuidance}>
        {busy === "fix" ? "Loading…" : "Show fix guidance"}
      </button>
      <p class="action-note">
        <strong>Show fix guidance</strong> mirrors <code>knotica doctor --fix</code> (CLI commands
        only — not a restore). When git is dirty, dry-run runs automatically so you can apply
        selected paths or all tracked.
      </p>

      {lastApply && lastApply.restored.length > 0 ? (
        <p class="action-result tone-ok">
          {lastApply.message || `Restored ${lastApply.restored.length} path(s).`}{" "}
          {lastApply.restored.join(", ")}
        </p>
      ) : null}

      {doctorRepair ? (
        <div class="doctor-repair-box">
          <p class="muted">
            {doctorRepair.dirty_count
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
                      <ObsidianFileLink ctx={obsidianCtx} relativePath={entry.path} className="path-name">
                        {entry.path}
                      </ObsidianFileLink>
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
        </div>
      ) : (
        <p class="action-note">
          Repair dry-run lists dirty paths for vault{vault ? ` · ${vault}` : ""} (auto-runs when git
          check fails).
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
  waitingVault,
  obsidianCtx,
}: {
  result: VaultLintResult | null;
  busy: boolean;
  waitingVault: boolean;
  obsidianCtx: ObsidianContext;
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
  waitingVault,
  obsidianCtx,
}: {
  okf: OkfCheckResult | null;
  repair: OkfRepairResult | null;
  busy: boolean;
  waitingVault: boolean;
  obsidianCtx: ObsidianContext;
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
}) {
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
}) {
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

function formatLintSessionTime(at: Date): string {
  return at.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function lintSessionHint(meta: LintSessionMeta): string {
  const hits = meta.violations === 1 ? "1 hit" : `${meta.violations} hits`;
  return `${hits} · ${meta.scopeLabel}`;
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

function checkStatusTone(status: string): Health {
  if (status === "PASS") return "ok";
  if (status === "WARN") return "warn";
  return "bad";
}

function doctorNeedsRepair(report: DoctorReport): boolean {
  if (report.fix_guidance) return true;
  return report.checks.some((row) => row.name === "git" && row.status !== "PASS");
}

function formatActionError(cause: unknown): string {
  if (cause instanceof Error && cause.message) return cause.message;
  return formatToolFailure(cause, "action");
}

/** Reason-aware tooltip for LLM-gated actions; `undefined` when LLM is available. */
function llmUnavailableTooltip(llm: LlmAvailability | undefined | null): string | undefined {
  if (!llm || llm.available !== false) return undefined;
  return llm.reason === "deps"
    ? "credentials found but eval dependencies are missing — restart with: uv run --group evals knotica mcp …"
    : "needs LLM credentials";
}

const BOOTSTRAP_DETAIL_MAX = 50;

/** Live "synthesizing page k/n — path" label while `datasets_bootstrap_train` progress streams in. */
function bootstrapBusyLabel(progress: LoopProgress | null | undefined): string {
  if (progress?.phase !== "bootstrap-train" || progress.total <= 0) {
    return "synthesizing from pages…";
  }
  const detail =
    progress.detail.length > BOOTSTRAP_DETAIL_MAX
      ? `${progress.detail.slice(0, BOOTSTRAP_DETAIL_MAX - 1)}…`
      : progress.detail;
  return `synthesizing page ${progress.current}/${progress.total}${detail ? ` — ${detail}` : ""}`;
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
