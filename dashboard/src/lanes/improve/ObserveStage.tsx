import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import {
  TwoPhaseConfirm,
  TwoPhaseOutcome,
  useTwoPhaseAction,
} from "../../TwoPhaseAction";
import type { ToolClient } from "../../toolClient";
import type {
  LoopCadenceConfig,
  LoopRunEvalResult,
  LoopRunnerLiveness,
} from "../../types";

/**
 * `observe` stage body (`INTERFACE_DESIGN.md §2.4`). Absorbs `LoopPane`'s
 * cadence controls, runner-liveness chip, `loop-progress`, the scalar chart,
 * `metrics_read`, and the billed two-phase `loop run_eval`. `Run eval now
 * (billed)` is the one primary control (§2.4's one-primary-control rule) and
 * stays at the top level alongside the summary facts (latest metrics,
 * runner, cadence); the chart and raw cadence editing live behind the single
 * `▸` disclosure.
 *
 * `status`/`metrics` are passed down from the lane's own read rather than
 * fetched independently here — the sibling `gate` stage reads the same
 * `status.loop` object, so the read is shared at the lane level. Cadence is
 * the one self-fetch this stage owns, exactly as `LoopPane` does today.
 */

interface ObserveStatus {
  loop: {
    stage: string | null;
    runner: LoopRunnerLiveness;
    baseline_scalar: number | null;
    // Narrower than `WikiStatus["loop"]["progress"]` (`LoopProgress`) on
    // purpose: only the fields this stage renders. A real `LoopProgress`
    // carries more fields, which still satisfies this shape structurally.
    progress: {
      phase: string;
      current: number;
      total: number;
      detail: string;
    } | null;
  };
}

interface ObserveMetricsRecord {
  generation: number;
  scalar: number;
  timestamp: string;
}

interface ObserveMetrics {
  records: ObserveMetricsRecord[];
}

type ObserveToolClient = Pick<ToolClient, "loopCadence" | "loopRunEval">;

const DEFAULT_EVAL_THREADS = "1";

export function ObserveStage({
  client,
  topic,
  vault,
  status,
  metrics,
}: {
  client: ObserveToolClient | null;
  topic: string;
  vault: string;
  status: ObserveStatus | null;
  metrics: ObserveMetrics | null;
}): JSX.Element {
  const chartHost = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [cadence, setCadence] = useState<LoopCadenceConfig | null>(null);
  const [runEvalThreads, setRunEvalThreads] = useState(DEFAULT_EVAL_THREADS);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const records = metrics?.records ?? [];
  const latest = records.length > 0 ? records[records.length - 1] : null;
  const runner = status?.loop.runner ?? null;
  const runnerAlive = Boolean(runner?.alive);
  const progress = status?.loop.progress ?? null;
  const baselineScalar = status?.loop.baseline_scalar ?? null;

  useEffect(() => {
    if (!client) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await client.loopCadence(topic, {}, vault);
        if (cancelled) return;
        setCadence(result);
        setRunEvalThreads(String(result.eval_num_threads));
      } catch {
        // Cadence config is best-effort display; leave the default on failure.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, topic, vault]);

  /** The operator's thread override — always a concrete count, never absent. */
  function chosenEvalThreads(): number {
    const threads = Number(runEvalThreads);
    return Number.isInteger(threads) && threads > 0 ? threads : 1;
  }

  const runEval = useTwoPhaseAction<LoopRunEvalResult>({
    preview: async () => {
      if (!client) throw new Error("Connect MCP to run an eval.");
      return client.loopRunEval(topic, "", chosenEvalThreads(), vault);
    },
    confirm: async (nonce, quoted) => {
      if (!client) throw new Error("Connect MCP to run an eval.");
      const result = await client.loopRunEval(
        topic,
        nonce,
        quoted.num_threads,
        vault,
      );
      setActionNote(result.message ?? "Eval run finished");
      return result;
    },
    onError: setActionNote,
  });

  const chartRecords = useMemo(
    () =>
      records.map((record) => ({
        generation: record.generation,
        scalar: record.scalar,
      })),
    [records],
  );

  useEffect(() => {
    const host = chartHost.current;
    if (!host || chartRecords.length === 0) return;

    const palette = readObserveChartPalette(host);
    const generations = chartRecords.map((record) => record.generation);
    const scalars = chartRecords.map((record) => record.scalar);
    const series: uPlot.Options["series"] = [
      {},
      { label: "Scalar", stroke: palette.series, width: 2 },
    ];
    const data: uPlot.AlignedData = [generations, scalars];
    if (baselineScalar !== null) {
      series.push({
        label: "Baseline",
        stroke: palette.baseline,
        width: 1,
        dash: [6, 4],
      });
      data.push(chartRecords.map(() => baselineScalar));
    }

    const chart = new uPlot(
      { width: host.clientWidth, height: 180, legend: { show: true }, series },
      data,
      host,
    );
    const resize = new ResizeObserver(() =>
      chart.setSize({ width: host.clientWidth, height: 180 }),
    );
    resize.observe(host);
    return () => {
      resize.disconnect();
      // A test double may not implement `destroy` — real `uPlot` always does.
      chart.destroy?.();
    };
  }, [baselineScalar, chartRecords]);

  return (
    <section class="pane-main observe-stage" aria-label="Observe">
      <header class="observe-toolbar">
        <div class="observe-facts">
          {latest ? (
            <span>
              Latest: gen <strong>{latest.generation}</strong> · scalar{" "}
              <strong>{latest.scalar}</strong>
            </span>
          ) : (
            <span class="muted">No eval observations yet.</span>
          )}
          <output class={`observe-chip ${runnerAlive ? "ok" : "warn"}`}>
            {runnerAlive
              ? `runner: watching · pid ${runner?.pid ?? "?"}`
              : "runner: off"}
          </output>
          {cadence ? (
            <span class="muted">
              cadence: every {cadence.eval_min_interval_hours}h · window{" "}
              {cadence.eval_window} · threads {cadence.eval_num_threads}
            </span>
          ) : null}
        </div>
        <div class="observe-actions">
          {runEvalControls(runEval, runEvalThreads, setRunEvalThreads)}
        </div>
      </header>

      {progress ? (
        <p class="muted observe-progress">
          {progress.phase} · {progress.current}/{progress.total}
          {progress.detail ? ` · ${progress.detail}` : ""}
        </p>
      ) : null}
      {actionNote ? <p class="saved-note">{actionNote}</p> : null}

      <button
        type="button"
        class="observe-disclosure"
        aria-expanded={expanded}
        onClick={() => setExpanded((wasExpanded) => !wasExpanded)}
      >
        <span aria-hidden="true">▸</span>{" "}
        {expanded ? "Hide details" : "Show details"}
      </button>

      {expanded ? <div class="observe-details" ref={chartHost} /> : null}
    </section>
  );
}

function runEvalControls(
  runEval: ReturnType<typeof useTwoPhaseAction<LoopRunEvalResult>>,
  runEvalThreads: string,
  setRunEvalThreads: (value: string) => void,
): JSX.Element {
  const { preview, outcome, busy } = runEval.state;

  if (outcome) {
    return (
      <TwoPhaseOutcome
        tone={outcome.acted ? "" : "no-charge"}
        onDismiss={runEval.reset}
      >
        {outcome.acted ? (
          <>
            <strong>Eval ran — this billed.</strong>{" "}
            {outcome.message || "No further detail was reported."}
          </>
        ) : (
          <>
            <strong>Nothing ran, nothing was billed.</strong>{" "}
            {outcome.message || "The loop declined this observation."}
          </>
        )}
      </TwoPhaseOutcome>
    );
  }

  if (preview) {
    return (
      <TwoPhaseConfirm
        busy={busy}
        busyLabel="Running"
        onConfirm={runEval.confirm}
        onCancel={runEval.reset}
      >
        Preview: worker <strong>{preview.worker}</strong>, judge{" "}
        <strong>{preview.judge}</strong>, threads{" "}
        <strong>{preview.num_threads}</strong>.
        {preview.estimated_cost ? ` ${preview.estimated_cost}.` : ""} This has
        NOT billed yet — confirm to run and bill.
      </TwoPhaseConfirm>
    );
  }

  return (
    <div class="observe-run-eval">
      <label class="observe-inline-field">
        <span>threads</span>
        <input
          type="number"
          step="1"
          min="1"
          value={runEvalThreads}
          disabled={busy !== null}
          onInput={(event) =>
            setRunEvalThreads((event.currentTarget as HTMLInputElement).value)
          }
        />
      </label>
      <button
        type="button"
        class="primary"
        disabled={busy !== null}
        onClick={() => void runEval.preview()}
      >
        {busy === "preview" ? "Estimating…" : "Run eval now (billed)"}
      </button>
    </div>
  );
}

type ObserveChartPalette = { series: string; baseline: string };

/** uPlot draws on canvas — resolve theme tokens to concrete colors at runtime. */
function readObserveChartPalette(host: HTMLElement): ObserveChartPalette {
  const cs = getComputedStyle(host);
  const pick = (token: string, fallback: string) =>
    cs.getPropertyValue(token).trim() || fallback;
  return {
    series: pick("--chart-series", pick("--accent", "#268bd2")),
    baseline: pick("--chart-baseline", pick("--warn", "#b58900")),
  };
}
