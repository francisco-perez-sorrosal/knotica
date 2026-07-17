import { useEffect, useRef } from "preact/hooks";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import type { MetricsWindow, WikiStatus } from "./types";

const STAGES = ["idle", "evaluating", "passed", "failed", "merging", "reverting"] as const;

export function LoopPane({
  status,
  metrics,
}: {
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
}) {
  const chartHost = useRef<HTMLDivElement>(null);
  const records = metrics?.records ?? [];
  const baseline = status?.gate.baseline ?? null;

  useEffect(() => {
    const host = chartHost.current;
    if (!host || records.length === 0) return;

    const generations = records.map((record) => record.generation);
    const scalars = records.map((record) => record.scalar);
    const series: uPlot.Options["series"] = [
      {},
      { label: "Scalar", stroke: "var(--accent)", width: 2, points: { show: true, size: 6 } },
    ];
    const data: uPlot.AlignedData = [generations, scalars];
    if (baseline !== null) {
      series.push({
        label: "Gate baseline",
        stroke: "var(--warn)",
        width: 1,
        dash: [6, 4],
        points: { show: false, size: 0 },
      });
      data.push(records.map(() => baseline));
    }

    const chart = new uPlot(
      {
        width: host.clientWidth,
        height: 260,
        scales: { x: { time: false } },
        axes: [
          { stroke: "var(--muted)", grid: { stroke: "var(--line)" } },
          {
            stroke: "var(--muted)",
            grid: { stroke: "var(--line)" },
            values: (_, values) => values.map((value) => value.toFixed(3)),
          },
        ],
        series,
      },
      data,
      host,
    );
    const resize = new ResizeObserver(() => chart.setSize({ width: host.clientWidth, height: 260 }));
    resize.observe(host);
    return () => {
      resize.disconnect();
      chart.destroy();
    };
  }, [baseline, records]);

  const stage = status?.loop.stage ?? "idle";
  return (
    <main class="pane-main">
      <section class="summary" aria-label="Loop summary">
        <div>
          <p class="eyebrow">Composition loop</p>
          <h2 class="loop-heading">Scalar gate</h2>
          <p class="muted">
            Topic progress and eval history for the active vault. Gate state is unknown until the
            loop runner freezes a baseline.
          </p>
        </div>
        <div class={`gate gate-${status?.gate.state ?? "unknown"}`}>
          <span>Gate</span>
          <strong>{status?.gate.state ?? "unknown"}</strong>
          <small>{baseline === null ? "baseline pending" : baseline.toFixed(4)}</small>
        </div>
      </section>

      <section class="panel">
        <header>
          <div>
            <h2>Scalar over generations</h2>
            <p>
              {records.length === 0
                ? "No evaluation history yet. The chart will appear after the first metrics record."
                : `${records.length} recorded generation${records.length === 1 ? "" : "s"}`}
            </p>
          </div>
          {status?.gate.last_scalar !== null && status?.gate.last_scalar !== undefined && (
            <output>{status.gate.last_scalar.toFixed(4)}</output>
          )}
        </header>
        {records.length > 0 ? (
          <div class="chart" ref={chartHost} />
        ) : (
          <div class="empty-chart">Awaiting metrics</div>
        )}
      </section>

      <section class="stages" aria-label="Loop stage">
        {STAGES.map((candidate) => (
          <article class={`stage ${candidate === stage ? "current" : ""}`} key={candidate}>
            <span class="dot" aria-hidden="true" />
            <strong>{candidate}</strong>
            {candidate === stage && <small>current</small>}
          </article>
        ))}
      </section>

      {(status?.loop.candidate_branch || status?.loop.last_decision) && (
        <p class="muted loop-detail">
          {status.loop.candidate_branch
            ? `Candidate ${status.loop.candidate_branch}`
            : "No active candidate"}
          {status.loop.last_decision ? ` · last decision ${status.loop.last_decision}` : ""}
          {" · trigger a cycle from the Vault pane or "}
          <code>loop_runner.py --once</code>
        </p>
      )}
    </main>
  );
}
