import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { isCompileActive } from "./compileStages";
import { PromptDiff } from "./PromptDiff";
import { ScoreboardPanel } from "./ScoreboardPanel";
import { ObsidianFileLink, type ObsidianContext } from "./obsidianLinks";
import {
  currentScoreSourceLabel,
  findTopicRow,
  isNaiveProbeScore,
  resolveTopicCurrentScore,
} from "./topicHelpers";
import type { ToolClient } from "./toolClient";
import type {
  CompileStatus,
  LoopCadenceConfig,
  LoopProgress,
  LoopRunEvalResult,
  MetricsRecord,
  MetricsWindow,
  WikiStatus,
} from "./types";

/** Story-facing stage path (full enum still lives on wiki_status). */
const STORY_STAGES = [
  "idle",
  "evaluating",
  "racing",
  "promoting",
  "compiling",
  "passed",
  "failed",
] as const;

export function LoopPane({
  status,
  metrics,
  client,
  topic,
  vault,
  obsidianCtx,
  onOpenArena,
  onOpenAsk,
  onOpenVault,
  onStatusRefresh,
}: {
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
  client?: ToolClient | null;
  topic?: string;
  vault?: string;
  obsidianCtx?: ObsidianContext;
  onOpenArena?: () => void;
  onOpenAsk?: () => void;
  onOpenVault?: () => void;
      onStatusRefresh?: () => void | Promise<void>;
}) {
  const chartHost = useRef<HTMLDivElement>(null);
  const records = metrics?.records ?? [];
  const chartRecords = useMemo(
    () => buildChartRecords(records, status?.compile ?? null),
    [records, status?.compile],
  );
  const chartFromCompile = records.length === 0 && chartRecords.length > 0;
  const stage = status?.loop.stage ?? "idle";
  const gate = status?.gate.state ?? "unknown";
  const topicName = topic || status?.topics[0]?.topic || "agentic-systems";
  const topicRow = findTopicRow(status, topicName);
  const { score: currentScore, source: currentScoreSource } = resolveTopicCurrentScore(
    status,
    metrics,
    topicRow,
  );
  const gateBaseline = status?.gate.baseline ?? status?.loop.baseline_scalar ?? null;
  const baseline = gateBaseline;
  const compileStage = status?.compile?.stage ?? "idle";
  const compiling = isCompileActive(compileStage);
  const compiled = Boolean(topicRow?.compiled?.present);
  const compileScalar =
    status?.compile?.scalar_after ?? topicRow?.compiled?.scalar ?? null;
  const pendingCandidates = status?.loop.pending_candidates ?? [];
  const pendingCount = pendingCandidates.filter((row) => row.pending).length;
  const topicReady = Boolean(topicRow);
  const naiveProbeOnly = isNaiveProbeScore(status, metrics, topicRow);
  const baselineFrozen = status?.loop.baseline_frozen ?? baseline !== null;
  const baselinePolicy = status?.loop.baseline_policy ?? "latest";
  const metricsHighWater = useMemo(() => metricsHighWaterMark(records), [records]);
  const canRebaselineToBest =
    metricsHighWater != null &&
    baseline != null &&
    metricsHighWater > baseline + BASELINE_EPS;
  const arenaStage = status?.loop.arena_stage ?? "idle";
  const runner = status?.loop.runner ?? null;
  const runnerAlive = Boolean(runner?.alive);
  // Other progress-carrying operations (e.g. datasets_bootstrap_train) share this channel —
  // only render it here when it actually describes the loop's own eval-on-clone phases.
  const rawProgress = status?.loop.progress ?? null;
  const evalProgress =
    rawProgress?.phase === "preparing" || rawProgress?.phase === "evaluating"
      ? rawProgress
      : null;
  const [baselineInput, setBaselineInput] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [cadence, setCadence] = useState<LoopCadenceConfig | null>(null);
  const [cadenceInputs, setCadenceInputs] = useState({
    intervalHours: "",
    window: "",
    numThreads: "",
  });
  const [runEvalThreads, setRunEvalThreads] = useState("");
  const [runEvalPreview, setRunEvalPreview] = useState<LoopRunEvalResult | null>(null);
  const autoProbeAttempted = useRef(false);
  const measureDisabled = !client || busy !== null || !topicReady;
  const vShape = detectVShape(
    chartRecords.map((r) => r.scalar),
    baseline,
  );

  useEffect(() => {
    if (currentScore != null) setBaselineInput(String(currentScore));
  }, [currentScore]);

  useEffect(() => {
    autoProbeAttempted.current = false;
  }, [topicName, vault]);

  useEffect(() => {
    if (!client || !topicReady) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await client.loopCadence(topicName, {}, vault ?? "");
        if (cancelled) return;
        setCadence(result);
        setCadenceInputs({
          intervalHours: String(result.eval_min_interval_hours),
          window: result.eval_window,
          numThreads: String(result.eval_num_threads),
        });
        setRunEvalThreads(String(result.eval_num_threads));
      } catch {
        // Cadence config is best-effort display; leave inputs blank on failure.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, topicName, vault, topicReady]);

  useEffect(() => {
    if (!client || !status || autoProbeAttempted.current || busy) return;
    if (!topicReady || currentScore != null) return;
    autoProbeAttempted.current = true;
    void (async () => {
      try {
        const result = await client.baselineProbe(topicName, vault ?? "");
        setActionNote(
          `Anchored cold start at ${result.scalar.toFixed(4)} (${result.runner_mode})`,
        );
        await onStatusRefresh?.();
      } catch {
        // Manual "Set cold start (0)" remains; server-side hooks may have already probed.
      }
    })();
  }, [client, status, topicName, vault, topicReady, currentScore, busy, onStatusRefresh]);

  useEffect(() => {
    const host = chartHost.current;
    if (!host || chartRecords.length === 0) return;

    const palette = readChartPalette(host);
    const axisFont = readChartAxisFont(host);
    const generations = chartRecords.map((record) => record.generation);
    const scalars = chartRecords.map((record) => record.scalar);
    const series: uPlot.Options["series"] = [
      {},
      {
        label: "Scalar",
        stroke: palette.series,
        width: 3,
        points: {
          show: true,
          size: 10,
          fill: palette.pointFill,
          stroke: palette.pointStroke,
          width: 2,
        },
      },
    ];
    const data: uPlot.AlignedData = [generations, scalars];
    if (baseline !== null) {
      series.push({
        label: "Gate baseline",
        stroke: palette.baseline,
        width: 2,
        dash: [8, 5],
        points: { show: false, size: 0 },
      });
      data.push(chartRecords.map(() => baseline));
    }
    if (compileScalar != null && !chartFromCompile) {
      series.push({
        label: "Compiled",
        stroke: palette.compiled,
        width: 2,
        dash: [4, 4],
        points: { show: false, size: 0 },
      });
      data.push(chartRecords.map(() => compileScalar));
    }

    const axisStroke = {
      stroke: palette.axis,
      ticks: { stroke: palette.axis, width: 1, size: 5 },
      grid: { stroke: palette.grid, width: 1 },
      font: axisFont,
    };

    const chart = new uPlot(
      {
        width: host.clientWidth,
        height: 280,
        legend: { show: true },
        scales: { x: { time: false } },
        axes: [
          axisStroke,
          {
            ...axisStroke,
            values: (_, values) => values.map((value) => value.toFixed(3)),
          },
        ],
        series,
      },
      data,
      host,
    );
    const resize = new ResizeObserver(() =>
      chart.setSize({ width: host.clientWidth, height: 280 }),
    );
    resize.observe(host);
    return () => {
      resize.disconnect();
      chart.destroy();
    };
  }, [baseline, chartFromCompile, chartRecords, compileScalar]);

  const racing = stage === "racing" || arenaStage === "racing";
  const healed = stage === "passed" || arenaStage === "completed";
  const displayStage = compiling ? "compiling" : normalizeStage(stage);
  const arenaLive = arenaStage !== "idle" && arenaStage != null;
  const queryPromptPath = `${topicName}/.knotica/prompts/query.md`;

  async function runHealAction(label: string, action: () => Promise<{ message?: string }>) {
    if (!client || busy) return;
    setBusy(label);
    setActionNote(null);
    try {
      const result = await action();
      setActionNote(result.message ?? `${label} finished`);
      onStatusRefresh?.();
    } catch (cause) {
      setActionNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function measureCurrentScore() {
    if (!client || busy) return;
    await runHealAction("measure", async () => {
      const result = await client.baselineProbe(topicName, vault ?? "");
      return {
        message: `Anchored cold start at ${result.scalar.toFixed(4)} (${result.runner_mode})`,
      };
    });
  }

  async function setDefendPolicy(policy: "latest" | "best") {
    if (!client || busy || policy === baselinePolicy) return;
    await runHealAction("policy", () => client.loopBaselinePolicy(topicName, policy, vault));
  }

  async function rebaselineToBest() {
    if (!client || busy) return;
    await runHealAction("rebaseline", () => client.loopRebaseline(topicName, "best", vault));
  }

  async function writeCadenceField(
    field: "intervalHours" | "window" | "numThreads",
    raw: string,
  ) {
    if (!client || busy || !cadence) return;
    const overrides: Parameters<ToolClient["loopCadence"]>[1] = {};
    if (field === "intervalHours") {
      const hours = Number(raw);
      if (!Number.isFinite(hours) || hours < 0 || String(hours) === String(cadence.eval_min_interval_hours)) {
        return;
      }
      overrides.evalMinIntervalHours = hours;
    } else if (field === "window") {
      if (!raw.trim() || raw === cadence.eval_window) return;
      overrides.evalWindow = raw.trim();
    } else {
      const threads = Number(raw);
      if (!Number.isInteger(threads) || threads < 1 || threads === cadence.eval_num_threads) {
        return;
      }
      overrides.evalNumThreads = threads;
    }
    await runHealAction("cadence", async () => {
      const result = await client.loopCadence(topicName, overrides, vault ?? "");
      setCadence(result);
      setCadenceInputs({
        intervalHours: String(result.eval_min_interval_hours),
        window: result.eval_window,
        numThreads: String(result.eval_num_threads),
      });
      return { message: "Cadence updated" };
    });
  }

  /** Phase 1: preview only — never bills. Phase 2 (confirm) is a separate explicit click. */
  async function previewRunEval() {
    if (!client || busy) return;
    const threads = Number(runEvalThreads);
    const numThreads = Number.isInteger(threads) && threads > 0 ? threads : undefined;
    setBusy("run-eval-preview");
    setActionNote(null);
    try {
      const preview = await client.loopRunEval(topicName, "", numThreads, vault ?? "");
      setRunEvalPreview(preview);
    } catch (cause) {
      setActionNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function confirmRunEval() {
    if (!client || busy || !runEvalPreview?.confirm_nonce) return;
    setBusy("run-eval-confirm");
    setActionNote(null);
    try {
      const result = await client.loopRunEval(
        topicName,
        runEvalPreview.confirm_nonce,
        runEvalPreview.num_threads,
        vault ?? "",
      );
      setRunEvalPreview(null);
      setActionNote(result.message ?? "Eval run finished");
      onStatusRefresh?.();
    } catch (cause) {
      setActionNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  function cancelRunEvalPreview() {
    setRunEvalPreview(null);
  }

  function defendPolicyControls() {
    return (
      <div class="heal-policy-controls">
        <div class="heal-policy-toggle" role="group" aria-label="Baseline defend policy">
          <span class="heal-policy-toggle-label">defend:</span>
          <button
            type="button"
            class={`heal-policy-btn ${baselinePolicy === "latest" ? "active" : ""}`}
            disabled={!client || busy !== null}
            onClick={() => void setDefendPolicy("latest")}
          >
            {busy === "policy" && baselinePolicy !== "latest" ? "…" : "latest"}
          </button>
          <button
            type="button"
            class={`heal-policy-btn ${baselinePolicy === "best" ? "active" : ""}`}
            disabled={!client || busy !== null}
            onClick={() => void setDefendPolicy("best")}
          >
            {busy === "policy" && baselinePolicy !== "best" ? "…" : "best"}
          </button>
        </div>
        <p class="muted heal-hint">
          {baselinePolicy === "best"
            ? "High-water mark — a better observation ratchets the baseline up; anything below it is a regression the arena fights."
            : "Baseline tracks reality — auto-freeze and instrument re-freeze only, no ratchet."}
        </p>
        {canRebaselineToBest ? (
          <button
            type="button"
            class="heal-freeze-secondary"
            disabled={!client || busy !== null}
            title={
              metricsHighWater != null
                ? `Metrics history shows ${metricsHighWater.toFixed(4)} — above the current baseline`
                : undefined
            }
            onClick={() => void rebaselineToBest()}
          >
            {busy === "rebaseline"
              ? "Re-freezing…"
              : `Re-freeze at best${metricsHighWater != null ? ` (${metricsHighWater.toFixed(4)})` : ""}`}
          </button>
        ) : null}
      </div>
    );
  }

  function cadenceControls() {
    return (
      <div class="heal-policy-controls">
        <div class="heal-cadence-toggle" role="group" aria-label="Eval cadence">
          <span class="heal-policy-toggle-label">cadence:</span>
          <label class="heal-inline-field heal-cadence-field">
            <span>min interval (h)</span>
            <input
              type="number"
              step="1"
              min="0"
              value={cadenceInputs.intervalHours}
              disabled={!client || busy !== null || !cadence}
              onInput={(event) =>
                setCadenceInputs((prev) => ({
                  ...prev,
                  intervalHours: (event.currentTarget as HTMLInputElement).value,
                }))
              }
              onBlur={(event) =>
                void writeCadenceField("intervalHours", (event.currentTarget as HTMLInputElement).value)
              }
            />
          </label>
          <label class="heal-inline-field heal-cadence-field">
            <span>window</span>
            <input
              type="text"
              value={cadenceInputs.window}
              disabled={!client || busy !== null || !cadence}
              onInput={(event) =>
                setCadenceInputs((prev) => ({
                  ...prev,
                  window: (event.currentTarget as HTMLInputElement).value,
                }))
              }
              onBlur={(event) =>
                void writeCadenceField("window", (event.currentTarget as HTMLInputElement).value)
              }
            />
          </label>
          <label class="heal-inline-field heal-cadence-field">
            <span>threads</span>
            <input
              type="number"
              step="1"
              min="1"
              value={cadenceInputs.numThreads}
              disabled={!client || busy !== null || !cadence}
              onInput={(event) =>
                setCadenceInputs((prev) => ({
                  ...prev,
                  numThreads: (event.currentTarget as HTMLInputElement).value,
                }))
              }
              onBlur={(event) =>
                void writeCadenceField("numThreads", (event.currentTarget as HTMLInputElement).value)
              }
            />
          </label>
        </div>
        {busy === "cadence" ? <small class="muted">saving…</small> : null}
      </div>
    );
  }

  function runEvalControls() {
    if (runEvalPreview) {
      return (
        <div class="heal-policy-controls heal-run-eval-confirm">
          <p class="heal-step-body">
            Preview: worker <strong>{runEvalPreview.worker}</strong>, judge{" "}
            <strong>{runEvalPreview.judge}</strong>, threads{" "}
            <strong>{runEvalPreview.num_threads}</strong>.
            {runEvalPreview.estimated_cost ? ` ${runEvalPreview.estimated_cost}.` : ""} This has NOT
            billed yet — confirm to run and bill.
          </p>
          <button
            type="button"
            class="heal-freeze-primary"
            disabled={!client || busy !== null}
            onClick={() => void confirmRunEval()}
          >
            {busy === "run-eval-confirm" ? "Running…" : "Confirm — run and bill"}
          </button>
          <button
            type="button"
            class="ghost"
            disabled={busy !== null}
            onClick={cancelRunEvalPreview}
          >
            Cancel
          </button>
        </div>
      );
    }
    return (
      <div class="heal-policy-controls">
        <label class="heal-inline-field heal-cadence-field">
          <span>threads</span>
          <input
            type="number"
            step="1"
            min="1"
            value={runEvalThreads}
            disabled={!client || busy !== null}
            onInput={(event) =>
              setRunEvalThreads((event.currentTarget as HTMLInputElement).value)
            }
          />
        </label>
        <button
          type="button"
          class="heal-freeze-secondary"
          disabled={!client || busy !== null || !topicReady}
          onClick={() => void previewRunEval()}
        >
          {busy === "run-eval-preview" ? "Estimating…" : "Run eval now"}
        </button>
      </div>
    );
  }

  function measureScoreBody() {
    return (
      <div class="heal-step-actions">
        <p class="heal-step-body muted">
          No cold-start anchor yet. Auto-sets to <strong>0.0</strong> when the topic
          exists (pre-training floor; no train/golden scoring). Full{" "}
          <code>knotica eval</code> is the gate-quality scalar.
        </p>
        <button
          type="button"
          class="heal-freeze-primary"
          disabled={measureDisabled}
          title={
            !topicReady
              ? "Select a topic first"
              : !client
                ? "Connect MCP client first"
                : undefined
          }
          onClick={() => void measureCurrentScore()}
        >
          {busy === "measure" ? "Anchoring…" : "Set cold start (0)"}
        </button>
      </div>
    );
  }

  const currentScoreLabel = currentScore != null ? currentScore.toFixed(4) : null;
  const frozenBaselineLabel = baseline != null ? baseline.toFixed(4) : null;
  const customBaselineValue = Number(baselineInput);
  const customBaselineValid =
    baselineInput.trim() !== "" &&
    Number.isFinite(customBaselineValue) &&
    customBaselineValue >= 0 &&
    customBaselineValue <= 1;
  const customIntentReference = currentScore ?? baseline;
  const customIntent = customBaselineValid
    ? resolveCustomBaselineIntent(customBaselineValue, customIntentReference)
    : "invalid";
  const customValueLabel = customBaselineValid ? customBaselineValue.toFixed(4) : null;
  const currentAboveFrozen =
    currentScore != null && baseline != null && currentScore > baseline + BASELINE_EPS;
  const currentMatchesFrozen =
    currentScore != null && baseline != null && Math.abs(currentScore - baseline) <= BASELINE_EPS;
  const primaryFreezeDisabled =
    !client ||
    busy !== null ||
    currentScore == null ||
    naiveProbeOnly ||
    (baselineFrozen && currentMatchesFrozen);

  function freezeBaselineBody() {
    const freezeAt = (scalar: number, label: string) =>
      runHealAction(label, () => client!.loopSetBaseline(topicName, scalar, vault));

    const customIntentCopy = customIntentLabels(customValueLabel, customIntent);
    const customField = (
      <details class="heal-custom-baseline">
        <summary>Override gate value (advanced)</summary>
        <div class="heal-custom-baseline-body">
          <label class="heal-inline-field">
            <span>Gate scalar</span>
            <input
              type="number"
              step="0.0001"
              min="0"
              max="1"
              value={baselineInput}
              disabled={!client || busy !== null}
              onInput={(event) =>
                setBaselineInput((event.currentTarget as HTMLInputElement).value)
              }
            />
          </label>
          {customIntent === "match" ? (
            <p class="heal-custom-intent-note heal-custom-intent-match">
              Matches {intentReferenceLabel(customIntentReference)} — use the primary freeze button
              above.
            </p>
          ) : customIntentCopy ? (
            <>
              <button
                type="button"
                class={`heal-freeze-secondary ${customIntentCopy.buttonClass}`}
                disabled={!client || busy !== null}
                onClick={() => void freezeAt(customBaselineValue, "freeze")}
              >
                {busy === "freeze" ? "Freezing…" : customIntentCopy.buttonLabel}
              </button>
              <p class={`heal-custom-intent-note ${customIntentCopy.noteClass}`}>
                {customIntentCopy.warning}
              </p>
            </>
          ) : (
            <p class="muted heal-custom-intent-note">
              Enter a scalar between 0 and 1 to override the gate floor.
            </p>
          )}
          <ul class="heal-custom-intent-help muted">
            <li>
              <strong>Match current score</strong> — normal freeze; lock the gate at today&apos;s
              eval.
            </li>
            <li>
              <strong>Higher than current</strong> — raise the bar; candidates must clear a tougher
              floor.
            </li>
            <li>
              <strong>Lower than current</strong> — demo only; weakens the gate vs today&apos;s
              score.
            </li>
          </ul>
        </div>
      </details>
    );

    const primaryFreezeLabel = (() => {
      if (busy === "freeze") return "Freezing…";
      if (baselineFrozen && currentAboveFrozen && currentScoreLabel) {
        return `Raise bar to current score (${currentScoreLabel})`;
      }
      if (baselineFrozen && currentScoreLabel) {
        return `Re-freeze at current score (${currentScoreLabel})`;
      }
      if (currentScoreLabel) {
        return `Freeze at current score (${currentScoreLabel})`;
      }
      return baselineFrozen ? "Re-freeze at current score" : "Freeze at current score";
    })();

    if (baselineFrozen) {
      return (
        <div class="heal-step-actions heal-freeze-actions">
          <div class="heal-frozen-compare">
            <p class="heal-step-body heal-frozen-score">
              Gate frozen at <strong>{frozenBaselineLabel}</strong>
            </p>
            {currentScoreLabel ? (
              <p class="heal-step-body heal-current-score">
                Current score <strong>{currentScoreLabel}</strong>
                <span class="muted"> ({currentScoreSourceLabel(currentScoreSource)})</span>
                {currentAboveFrozen ? (
                  <span class="heal-score-delta delta-up"> · above frozen gate</span>
                ) : currentMatchesFrozen ? (
                  <span class="muted"> · matches frozen gate</span>
                ) : (
                  <span class="heal-score-delta delta-down"> · below frozen gate</span>
                )}
              </p>
            ) : (
              measureScoreBody()
            )}
          </div>
          <button
            type="button"
            class="heal-freeze-primary"
            disabled={primaryFreezeDisabled}
            title={
              naiveProbeOnly
                ? "Cold start 0.0 is not gate-quality — run knotica eval or compile first"
                : currentScore == null
                  ? "Set cold start (0) or run eval first"
                  : currentMatchesFrozen
                    ? "Gate already matches today's score"
                    : undefined
            }
            onClick={() => currentScore != null && void freezeAt(currentScore, "freeze")}
          >
            {primaryFreezeLabel}
          </button>
          {customField}
          <p class="muted heal-hint">
            Same as <code>loop_runner.py --set-baseline</code>. Primary action locks (or raises) the
            gate to a real eval or compile scalar — not cold start 0.0.
          </p>
          {defendPolicyControls()}
          {cadenceControls()}
          {runEvalControls()}
        </div>
      );
    }

    return (
      <div class="heal-step-actions heal-freeze-actions">
        {currentScoreLabel ? (
          <>
            <p class="heal-step-body heal-current-score">
              Current score <strong>{currentScoreLabel}</strong>
              <span class="muted"> ({currentScoreSourceLabel(currentScoreSource)})</span>
            </p>
            <button
              type="button"
              class="heal-freeze-primary"
              disabled={primaryFreezeDisabled}
              title={
                naiveProbeOnly
                  ? "Cold start 0.0 is not gate-quality — run knotica eval or compile first"
                  : undefined
              }
              onClick={() => currentScore != null && void freezeAt(currentScore, "freeze")}
            >
              {primaryFreezeLabel}
            </button>
            {naiveProbeOnly ? (
              <p class="heal-step-body heal-train-probe-warn">
                Cold start 0.0 only — run <code>knotica eval</code> or compile before freezing the
                gate. Advanced override below if you must.
              </p>
            ) : null}
          </>
        ) : (
          measureScoreBody()
        )}
        {customField}
        <p class="muted heal-hint">
          Same as <code>loop_runner.py --set-baseline</code>. Primary action locks the gate at a
          real eval or compile scalar — not cold start 0.0.
        </p>
        {defendPolicyControls()}
        {cadenceControls()}
        {runEvalControls()}
      </div>
    );
  }

  const merged = stage === "merging" || (stage === "passed" && baselineFrozen);

  const healSteps = [
    {
      id: "observe",
      title: "Observe",
      ready: baselineFrozen,
      current: stage === "evaluating" || !baselineFrozen,
      body: freezeBaselineBody(),
    },
    {
      id: "gate",
      title: "Gate",
      ready: pendingCandidates.length > 0,
      current: baselineFrozen && pendingCandidates.length === 0,
      body:
        pendingCandidates.length > 0 ? (
          <div class="heal-step-body">
            <ul class="candidate-list">
              {pendingCandidates.map((row) => (
                <li key={row.branch}>
                  <code>{row.branch}</code>
                  <span class={`candidate-tag ${row.pending ? "pending" : "done"}`}>
                    {row.pending ? "pending" : "processed"} · {row.sha}
                  </span>
                  {row.pending && client ? (
                    <PromptDiff
                      client={client}
                      topic={topicName}
                      vault={vault ?? ""}
                      branch={row.branch}
                      label="Show query.md diff"
                    />
                  ) : null}
                </li>
              ))}
            </ul>
            <button
              type="button"
              disabled={!client || busy !== null || !baselineFrozen || pendingCount === 0}
              title="Nudges the watcher to gate the next candidate now, instead of waiting for its next tick"
              onClick={() =>
                void runHealAction("process", () => client!.loopRunOnce(topicName, vault))
              }
            >
              {busy === "process" ? "Gating…" : "Gate next candidate now"}
            </button>
            <p class="muted heal-hint">
              The watcher gates each <code>loop/c/*</code> tip automatically on its next tick — this
              button just runs one cycle immediately. Gate pass merges; gate fail opens Arena.
            </p>
          </div>
        ) : (
          <div class="heal-step-body">
            <p class="muted">
              Push new content to a local <code>loop/c/*</code> branch — the watcher picks it up,
              evals it on a clone, and gates the result against the baseline. Nothing to do here
              manually; this card fills in once a candidate is pending.
            </p>
          </div>
        ),
    },
    {
      id: "heal",
      title: "Heal",
      ready: arenaLive || racing || healed,
      current: racing || arenaStage === "promoting",
      body: (
        <div class="heal-step-actions">
          <p class="heal-step-body">
            {arenaLive || racing ? (
              <>
                Stage <strong>{arenaStage ?? stage}</strong>
                {status?.loop.arena_race_id ? ` · race ${status.loop.arena_race_id}` : ""}
                {status?.loop.last_decision ? ` · last ${status.loop.last_decision}` : ""}
              </>
            ) : (
              <span class="muted">
                Opens after a gate fail — the watcher races prompt variants in the arena until one
                clears baseline, or reverts if none do.
              </span>
            )}
          </p>
          {onOpenArena ? (
            <button type="button" onClick={onOpenArena} disabled={!arenaLive && !racing && !healed}>
              Open Arena
            </button>
          ) : null}
        </div>
      ),
    },
    {
      id: "merged",
      title: "Merged",
      ready: merged,
      current: stage === "merging",
      body: (
        <div class="heal-step-body">
          {merged ? (
            <p class="muted">
              Candidate cleared the gate and merged to the default branch
              {status?.loop.candidate_branch ? ` (${status.loop.candidate_branch})` : ""}.
            </p>
          ) : (
            <span class="muted">A gate pass merges the candidate automatically — nothing manual.</span>
          )}
          {onOpenAsk ? (
            <button type="button" class="ghost" onClick={onOpenAsk} disabled={!merged}>
              Prove in Ask
            </button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <main class="pane-main">

      <section class="summary" aria-label="Loop summary">
        <div>
          <p class="eyebrow">Composition loop</p>
          <h2 class="loop-heading">Refuse to get worse</h2>
          <p class="muted">
            Gate protects every candidate. Red opens Arena (heal). Curated trainset unlocks Compile
            (flywheel). Both prove out in Ask.
          </p>
        </div>
        <div class={`gate gate-${gate}`}>
          <span>Gate</span>
          <strong>{gate}</strong>
          <small>
            {baseline === null
              ? "no baseline yet — first observation freezes it automatically"
              : baseline.toFixed(4)}
          </small>
        </div>
      </section>

      <section class="panel heal-stepper" aria-label="Heal process">
        <header>
          <div>
            <h2>Autonomous loop</h2>
            <p>
              The watcher observes new content, evals it on a clone, and gates it against the
              baseline; regressions trigger an arena prompt-heal. Live status from{" "}
              <code>wiki_status</code>.
            </p>
          </div>
          <div class="heal-stepper-chips">
            <output class={`health-chip ${gate === "fail" ? "bad" : gate === "pass" ? "ok" : "warn"}`}>
              gate {gate}
            </output>
            <output
              class={`health-chip ${runnerAlive ? "ok" : "warn"}`}
              title={
                runnerAlive
                  ? runner?.beat_at
                    ? `Last heartbeat ${runner.beat_at}`
                    : undefined
                  : `Start it with: knotica loop --topic ${topicName}`
              }
            >
              {runnerAlive ? `runner: watching · pid ${runner?.pid ?? "?"}` : "runner: off"}
            </output>
          </div>
        </header>
        {!runnerAlive ? (
          <p class="muted heal-hint">
            No watcher running for <code>{topicName}</code>. Start one with{" "}
            <code>knotica loop --topic {topicName}</code>.
          </p>
        ) : null}
        <ol class="heal-steps">
          {healSteps.map((step, index) => (
            <li
              key={step.id}
              class={`heal-step ${step.current ? "current" : ""} ${step.ready ? "ready" : ""}`}
            >
              <span class="heal-step-index" aria-hidden="true">
                {index + 1}
              </span>
              <div class="heal-step-content">
                <strong>{step.title}</strong>
                {step.body}
              </div>
            </li>
          ))}
        </ol>
        {actionNote ? <p class="heal-action-note">{actionNote}</p> : null}
        {pendingCandidates.length > 0 ? (
          <p class="muted loop-detail">
            {pendingCount} pending candidate{pendingCount === 1 ? "" : "s"}
            {status?.loop.last_decision ? ` · last decision ${status.loop.last_decision}` : ""}
          </p>
        ) : null}
      </section>

      {compiling ? (
        <aside class="loop-banner tone-compile">
          <strong>Compiling</strong>
          <span>
            {status?.compile?.message || "MIPRO optimizing on a clone…"}
            {status?.compile?.trial_total
              ? ` · trial ${status.compile.trial}/${status.compile.trial_total}`
              : ""}
          </span>
          {onOpenVault ? (
            <button type="button" onClick={onOpenVault}>
              Vault progress
            </button>
          ) : null}
        </aside>
      ) : null}

      {status?.compile?.stage === "completed" && status.compile.branch ? (
        <aside class="loop-banner tone-heal">
          <strong>Compile branch ready</strong>
          <span>
            <code>{status.compile.branch}</code>
            {status.compile.scalar_before != null && status.compile.scalar_after != null
              ? ` · scalar ${status.compile.scalar_before.toFixed(3)} → ${status.compile.scalar_after.toFixed(3)}`
              : ""}
            . Merge, then prove in Ask.
          </span>
          {onOpenAsk ? (
            <button type="button" onClick={onOpenAsk}>
              Prove in Ask
            </button>
          ) : null}
        </aside>
      ) : null}

      {compiled && !compiling && status?.compile?.stage !== "completed" ? (
        <aside class="loop-banner tone-ready">
          <strong>Compiled artifact active</strong>
          <span>
            Query serves the optimized engine silently
            {compileScalar != null ? ` · scalar ${compileScalar.toFixed(3)}` : ""}.
          </span>
          {onOpenAsk ? (
            <button type="button" onClick={onOpenAsk}>
              Prove in Ask
            </button>
          ) : null}
        </aside>
      ) : null}

      {gate === "fail" || racing ? (
        <aside class={`loop-banner tone-${racing ? "argue" : "regression"}`}>
          <strong>{racing ? "Arguing with itself" : "Regression detected"}</strong>
          <span>
            {racing
              ? "Prompt variants are racing — open the Arena for the live leaderboard."
              : "Scalar dropped under the gate. The watcher will race prompts in Arena on its next tick."}
          </span>
          {onOpenArena ? (
            <button type="button" onClick={onOpenArena}>
              Open Arena
            </button>
          ) : null}
        </aside>
      ) : null}

      {healed || vShape ? (
        <aside class="loop-banner tone-heal">
          <strong>{vShape ? "V-shape recovery" : "Healed"}</strong>
          <span>
            {vShape
              ? "Timeline shows baseline → dip → recovery. Prove it by asking the same question again."
              : "Winner cleared the gate. Re-ask in Ask to feel the better answer."}
          </span>
          {onOpenAsk ? (
            <button type="button" onClick={onOpenAsk}>
              Prove in Ask
            </button>
          ) : null}
        </aside>
      ) : null}

      <ScoreboardPanel
        client={client ?? null}
        topic={topicName}
        vault={vault ?? ""}
        status={status}
        onStatusRefresh={onStatusRefresh}
      />

      <section class="panel">
        <header>
          <div>
            <h2>Scalar over generations</h2>
            <p>
              {chartRecords.length === 0
                ? "No evaluation history yet. The chart appears after the first metrics record or a completed compile."
                : chartFromCompile
                  ? `${chartRecords.length} compile generation${chartRecords.length === 1 ? "" : "s"} from compile-state (metrics.jsonl still empty)`
                  : `${chartRecords.length} generation${chartRecords.length === 1 ? "" : "s"} · ${
                      vShape ? "V-shape visible" : "awaiting regression/heal"
                    }${compileScalar != null ? " · compiled line" : ""}`}
            </p>
          </div>
          {status?.gate.last_scalar != null ? (
            <output class={gate === "fail" ? "scalar-bad" : gate === "pass" ? "scalar-good" : ""}>
              {status.gate.last_scalar.toFixed(4)}
            </output>
          ) : null}
        </header>
        {chartRecords.length > 0 ? (
          <div class="chart" ref={chartHost} />
        ) : (
          <div class="empty-chart">Awaiting metrics</div>
        )}
        {status?.compile?.scalar_before != null && status?.compile?.scalar_after != null ? (
          <p class="compile-chart-note">
            Last compile delta:{" "}
            <strong>
              {status.compile.scalar_before.toFixed(3)} → {status.compile.scalar_after.toFixed(3)}
            </strong>{" "}
            (
            {status.compile.scalar_after - status.compile.scalar_before >= 0 ? "+" : ""}
            {(status.compile.scalar_after - status.compile.scalar_before).toFixed(3)})
          </p>
        ) : null}
      </section>

      <section class="stages story-stages" aria-label="Loop stage">
        {STORY_STAGES.map((candidate) => (
          <article
            class={`stage ${displayStage === candidate ? "current" : ""} stage-${candidate}`}
            key={candidate}
          >
            <span class="dot" aria-hidden="true" />
            <strong>{candidate}</strong>
            {displayStage === candidate ? <small>now</small> : null}
            {candidate === "evaluating" && displayStage === "evaluating" && evalProgress
              ? evalProgressBody(evalProgress)
              : null}
          </article>
        ))}
      </section>

      {(status?.loop.candidate_branch ||
        status?.loop.last_decision ||
        status?.compile?.branch) && (
        <p class="muted loop-detail">
          {status?.loop.candidate_branch
            ? `Candidate ${status.loop.candidate_branch}`
            : "No active loop candidate"}
          {status?.loop.last_decision ? ` · last decision ${status.loop.last_decision}` : ""}
          {status?.loop.arena_race_id ? ` · arena ${status.loop.arena_race_id}` : ""}
          {status?.compile?.branch ? ` · compile ${status.compile.branch}` : ""}
        </p>
      )}
    </main>
  );
}

function buildChartRecords(
  metricsRecords: MetricsRecord[],
  compile: CompileStatus | null,
): MetricsRecord[] {
  if (metricsRecords.length > 0) {
    return metricsRecords;
  }
  if (!compile) {
    return [];
  }

  const history = compile.history ?? [];
  const promoted = history
    .filter((entry) => entry.promoted && entry.scalar_after != null)
    .sort((left, right) =>
      (left.created_at ?? left.updated_at ?? "").localeCompare(
        right.created_at ?? right.updated_at ?? "",
      ),
    );
  if (promoted.length > 0) {
    return promoted.map((entry, index) =>
      syntheticCompileMetric(compile.topic, index + 1, entry.scalar_after as number),
    );
  }

  const points: MetricsRecord[] = [];
  if (compile.scalar_before != null) {
    points.push(syntheticCompileMetric(compile.topic, 1, compile.scalar_before));
  }
  if (
    compile.scalar_after != null &&
    (compile.scalar_before == null ||
      Math.abs(compile.scalar_after - compile.scalar_before) > 1e-9)
  ) {
    points.push(
      syntheticCompileMetric(compile.topic, points.length + 1, compile.scalar_after),
    );
  }
  return points;
}

function syntheticCompileMetric(topic: string, generation: number, scalar: number): MetricsRecord {
  return {
    schema_version: 1,
    topic,
    timestamp: "",
    generation,
    harness_version: "compile-post-eval",
    scalar,
    components: {
      qa_accuracy: scalar,
      citation_validity: 1,
      lint_violations: 0,
      token_cost: 0,
    },
    n_examples: 0,
    corpus_ref: "compile-state",
    artifact_ref: null,
  };
}

function normalizeStage(stage: string): (typeof STORY_STAGES)[number] {
  if (stage === "merging") return "promoting";
  if (stage === "reverting") return "failed";
  if ((STORY_STAGES as readonly string[]).includes(stage)) {
    return stage as (typeof STORY_STAGES)[number];
  }
  return "idle";
}

const PROGRESS_DETAIL_MAX = 80;

function truncateDetail(text: string): string {
  if (text.length <= PROGRESS_DETAIL_MAX) return text;
  return `${text.slice(0, PROGRESS_DETAIL_MAX - 1)}…`;
}

function evalSubstageLabel(progress: LoopProgress): string | null {
  if (progress.substage === "answering") return "answering…";
  if (progress.substage === "judging") {
    return progress.sub_total > 0 && progress.sub_current > 0
      ? `judging ${progress.sub_current}/${progress.sub_total}`
      : "judging…";
  }
  return null;
}

function evalProgressBody(progress: LoopProgress) {
  if (progress.phase === "preparing" || progress.total <= 0) {
    return <small class="stage-progress-hint">preparing clone + golden set…</small>;
  }
  const pct = Math.min(100, Math.round((progress.current / progress.total) * 100));
  const substageLabel = evalSubstageLabel(progress);
  return (
    <div class="stage-progress">
      <small class="stage-progress-count">
        question {progress.current}/{progress.total}
      </small>
      <div class="stage-progress-track" aria-hidden="true">
        <div class="stage-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      {substageLabel ? (
        <small class="muted stage-progress-substage">{substageLabel}</small>
      ) : null}
      {progress.detail ? (
        <small class="muted stage-progress-detail">{truncateDetail(progress.detail)}</small>
      ) : null}
    </div>
  );
}

const BASELINE_EPS = 1e-6;

/** Highest scalar among metrics records sharing the newest record's harness_version — cross-instrument scalars are never comparable. */
function metricsHighWaterMark(records: MetricsRecord[]): number | null {
  if (records.length === 0) return null;
  const newestVersion = records[records.length - 1].harness_version;
  const sameInstrument = records.filter((row) => row.harness_version === newestVersion);
  if (sameInstrument.length === 0) return null;
  return Math.max(...sameInstrument.map((row) => row.scalar));
}

type CustomBaselineIntent = "raise" | "lower" | "match" | "invalid" | "unknown";

function resolveCustomBaselineIntent(
  value: number,
  reference: number | null,
): CustomBaselineIntent {
  if (reference == null) return "unknown";
  if (Math.abs(value - reference) <= BASELINE_EPS) return "match";
  if (value > reference) return "raise";
  return "lower";
}

function intentReferenceLabel(reference: number | null): string {
  if (reference == null) return "the reference score";
  return `current score (${reference.toFixed(4)})`;
}

function customIntentLabels(
  valueLabel: string | null,
  intent: CustomBaselineIntent,
): {
  buttonLabel: string;
  buttonClass: string;
  noteClass: string;
  warning: string;
} | null {
  if (!valueLabel || intent === "invalid" || intent === "unknown" || intent === "match") {
    return null;
  }
  if (intent === "raise") {
    return {
      buttonLabel: `Raise the bar to ${valueLabel} (harder to pass)`,
      buttonClass: "heal-freeze-intent-warn",
      noteClass: "heal-custom-intent-warn",
      warning:
        "Candidates must beat a higher floor than today's score — use only when you want a stricter gate.",
    };
  }
  return {
    buttonLabel: `Lower the bar to ${valueLabel} (demo / easier gate)`,
    buttonClass: "heal-freeze-intent-danger",
    noteClass: "heal-custom-intent-danger",
    warning:
      "This weakens the gate vs today's score — demo and recovery only; do not ship a lower floor by mistake.",
  };
}

type ChartPalette = {
  series: string;
  pointFill: string;
  pointStroke: string;
  baseline: string;
  compiled: string;
  axis: string;
  grid: string;
};

/** uPlot draws on canvas — resolve theme tokens to concrete colors at runtime. */
function readChartPalette(host: HTMLElement): ChartPalette {
  const cs = getComputedStyle(host);
  const pick = (token: string, fallback: string) => cs.getPropertyValue(token).trim() || fallback;
  const series = pick("--chart-series", pick("--accent", "#268bd2"));
  return {
    series,
    pointFill: pick("--chart-point-fill", pick("--surface", "#fffaf0")),
    pointStroke: pick("--chart-point-stroke", series),
    baseline: pick("--chart-baseline", pick("--warn", "#b58900")),
    compiled: pick("--chart-compiled", pick("--good", "#859900")),
    axis: pick("--chart-axis", pick("--text", "#657b83")),
    grid: pick("--chart-grid", pick("--line", "#d9d1ba")),
  };
}

function readChartAxisFont(host: HTMLElement): string {
  const cs = getComputedStyle(host);
  const family = cs.getPropertyValue("--mono").trim() || "ui-monospace, monospace";
  return `12px ${family}`;
}

/** True when scalars show a dip below baseline then a recovery at/above it. */
function detectVShape(scalars: number[], baseline: number | null): boolean {
  if (baseline === null || scalars.length < 3) return false;
  let sawDip = false;
  let sawRecovery = false;
  for (const value of scalars) {
    if (value < baseline - 1e-6) sawDip = true;
    if (sawDip && value >= baseline - 1e-6) sawRecovery = true;
  }
  return sawDip && sawRecovery;
}
