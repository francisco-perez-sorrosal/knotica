import type { JSX } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import { ArenaScorerSwitch } from "./ArenaScorerSwitch";
import { Icon } from "../../icons";
import { SectionCard } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { TermHint } from "../../TermHint";
import {
  TwoPhaseConfirm,
  TwoPhaseOutcome,
  useTwoPhaseAction,
} from "../../TwoPhaseAction";
import { Spinner } from "../../icons";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import type { ToolClient } from "../../toolClient";
import type {
  LoopCadenceConfig,
  LoopRunEvalResult,
  LoopRunnerLiveness,
} from "../../types";

/**
 * `observe` stage body. Absorbs `LoopPane`'s cadence controls,
 * runner-liveness chip, `loop-progress`, the scalar chart, `metrics_read`,
 * and the billed two-phase `loop run_eval`.
 *
 * The body is three `SectionCard`s in the stage-body grammar's fixed scan
 * order — MEASUREMENT (the numbers), EVAL RUN (the knobs plus the one
 * primary action, which sits in the footer of the card holding the settings
 * it spends against), SCALAR TREND (the chart, behind the single
 * `aria-expanded` disclosure this stage owns). `Run eval now (billed)` keeps
 * its label, its class and its two-phase semantics verbatim: the visible
 * `billed` chip is a *sibling* of the button, never a child, so the
 * accessible name is unchanged and a single click still cannot bill.
 *
 * EVAL RUN prints all four `[loop]` keys — the three cadence knobs plus
 * `arena_scorer` — and carries the one control that writes the fourth, in
 * the card body rather than the footer: the footer belongs to the billed
 * run, and a config write is not a spend. Every label hosts a `TermHint`
 * naming what the key does and what it defaults to.
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
        // A read carries no overrides, so the spend gate cannot fire and the
        // preview branch is unreachable here -- narrowed rather than asserted,
        // because a payload that surprises us should leave the default alone.
        if (cancelled || "confirm_nonce" in result) return;
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
      <SectionCard
        title="MEASUREMENT"
        icon="stage:observe"
        headerActions={
          <output class={`observe-chip ${runnerAlive ? "ok" : "warn"}`}>
            {/* The word `watching` is the state carrier; the glyph only
                seconds it, so a reduced-motion reader loses nothing. */}
            {runnerAlive ? <Spinner /> : null}
            {runnerAlive
              ? `runner: watching · pid ${runner?.pid ?? "?"}`
              : "runner: off"}
          </output>
        }
      >
        <>
          <StatGrid>
            <Stat label={hint("gen")} value={latest?.generation} />
            <Stat label={hint("scalar")} value={latest?.scalar} />
            <Stat label={hint("baseline")} value={baselineScalar} />
          </StatGrid>
          <p class="muted">
            {latest
              ? "The score the last cycle produced, next to the frozen stick it must beat."
              : "No eval has run for this topic yet."}
          </p>
          {progress ? (
            <p class="muted section-card-status observe-progress">
              <Icon name="state:running" size={16} />
              {`${progress.phase} · ${progress.current}/${progress.total}${
                progress.detail ? ` · ${progress.detail}` : ""
              }`}
            </p>
          ) : null}
          {actionNote ? (
            <p class="saved-note" role="status">
              {actionNote}
            </p>
          ) : null}
        </>
      </SectionCard>

      <SectionCard
        title="EVAL RUN"
        icon="refresh"
        footer={runEvalFooter(runEval, runEvalThreads, setRunEvalThreads)}
      >
        <>
          <StatGrid>
            <Stat
              label={hint("cadence")}
              value={
                cadence ? `every ${cadence.eval_min_interval_hours}h` : null
              }
            />
            <Stat label={hint("window")} value={cadence?.eval_window} />
            <Stat label={hint("threads")} value={cadence?.eval_num_threads} />
            <Stat label={hint("scorer")} value={cadence?.arena_scorer} />
          </StatGrid>
          <p class="muted">
            Running a cycle costs model tokens. The first click only quotes it.
          </p>
          <ArenaScorerSwitch
            client={client}
            topic={topic}
            vault={vault}
            current={cadence?.arena_scorer ?? null}
            testId="observe-arena-scorer"
            onSwitched={setCadence}
          />
        </>
      </SectionCard>

      <SectionCard
        title="SCALAR TREND"
        headerActions={
          <button
            type="button"
            class="observe-disclosure"
            aria-expanded={expanded}
            onClick={() => setExpanded((wasExpanded) => !wasExpanded)}
          >
            <span aria-hidden="true">▸</span>{" "}
            {expanded ? "Hide details" : "Show details"}
          </button>
        }
      >
        <>
          <p class="muted">
            The scalar for each generation, with the baseline drawn across it.
          </p>
          {expanded ? <div class="observe-details" ref={chartHost} /> : null}
        </>
      </SectionCard>
    </section>
  );
}

/**
 * The explanatory copy behind each stat label's `TermHint`. Held as data so
 * the three cards above read as structure rather than prose.
 */
const OBSERVE_HINTS = {
  gen: {
    term: "LATEST GEN",
    title: "Latest gen",
    body: "Each finished eval cycle bumps the generation by one. Generations are per topic and are never reused, so gen 12 here and gen 12 in another topic are unrelated.",
  },
  scalar: {
    term: "LATEST SCALAR",
    title: "Latest scalar",
    body: "The score the last eval cycle got on the held-out set. Higher is better. It only means something next to the baseline — a scalar with no baseline is a number, not a verdict.",
  },
  baseline: {
    term: "BASELINE",
    title: "Baseline",
    body: "The frozen measuring stick this topic is scored against. A candidate has to beat it to pass the gate. It is set when a golden set is frozen in Instrument.",
  },
  cadence: {
    term: "CADENCE",
    title: "Cadence — [loop] eval_min_interval_hours",
    body: "The shortest gap the background watcher leaves between two eval cycles, counted from when the last one started. It does not stop you running one right now. Defaults to 0 — no throttle, every eligible tick evaluates.",
  },
  window: {
    term: "WINDOW",
    title: "Quiet window — [loop] eval_window",
    body: 'The local-clock window an unattended eval is allowed to start in, written "HH:MM-HH:MM" (a range crossing midnight, like "22:00-02:00", is fine). Defaults to unset — no window restriction. It only gates the watcher; a cycle you start by hand ignores it.',
  },
  threads: {
    term: "DEFAULT THREADS",
    title: "Default threads — [loop] eval_num_threads",
    body: "How many eval questions run in parallel on the watcher's own schedule. Defaults to 4, bounded 1–8. The box below overrides it for this run only — the confirm quote will show the number you actually set.",
  },
  scorer: {
    term: "SCORER",
    title: "Arena scorer — [loop] arena_scorer",
    body: 'What the prompt arena races variants with. Defaults to "heuristic": a free, deterministic keyword scorer whose scalars share no scale with the eval-derived gate baseline, so the arena aborts a race rather than ranking against it. "eval" runs the real golden-set harness per variant — gate-comparable, and billed one full eval per variant on every race, including ones the watcher starts unattended.',
  },
} as const;

function hint(key: keyof typeof OBSERVE_HINTS): JSX.Element {
  return <TermHint id={`observe-${key}`} {...OBSERVE_HINTS[key]} />;
}

/**
 * The EVAL RUN card's footer. The quote and the outcome replace this row in
 * place — the two-phase confirm never moves out of the card that owns the
 * control, so the answer always lands where the question was asked.
 */
function runEvalFooter(
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
        {/* Inside `TwoPhaseOutcome`'s own live region and its own `<p>`, so
            this contributes phrasing content and the sixth answer only. */}
        <ProcessOutcome process="improve.run_eval" />
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
    <>
      <label class="observe-inline-field">
        <span>threads for this run</span>
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
      {/* Sibling of the button, never a child: the accessible name stays
          `Run eval now (billed)` and the two-phase contract is untouched.
          The brief carries the spend chip plus why this cycle is necessary
          and what it will do. */}
      <ProcessBrief process="improve.run_eval" />
      <button
        type="button"
        class="primary"
        disabled={busy !== null}
        aria-busy={busy === "preview" || undefined}
        onClick={() => void runEval.preview()}
      >
        {busy === "preview" ? (
          <>
            <Spinner />
            Estimating…
          </>
        ) : (
          "Run eval now (billed)"
        )}
      </button>
    </>
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
