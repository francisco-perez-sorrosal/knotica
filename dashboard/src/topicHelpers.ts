import type { MetricsWindow, WikiStatus } from "./types";

export type TopicRow = WikiStatus["topics"][number];

export type BaselineSource =
  | "frozen"
  | "loop"
  | "compile"
  | "last_metrics"
  | "naive_probe"
  | "none";

/** Harness tags for naive / legacy cold-start probes (never gate-quality eval). */
export const NAIVE_PROBE_HARNESS_VERSIONS = new Set([
  "naive-cold-start",
  /** @deprecated retrieval hit-rate — display only */
  "retrieval-cold-start",
  /** @deprecated train lexical — display only */
  "lexical-cold-start-train",
  /** @deprecated golden lexical — display only */
  "lexical-cold-start",
]);

function isNaiveProbeHarness(harness: string | null | undefined): boolean {
  return harness != null && NAIVE_PROBE_HARNESS_VERSIONS.has(harness);
}

/** Per-topic gate baseline for header/scoreboard display. */
export function resolveTopicBaseline(
  status: WikiStatus | null | undefined,
  metrics?: MetricsWindow | null,
  topicRow?: TopicRow | null,
): { baseline: number | null; source: BaselineSource } {
  if (status?.gate.baseline != null) {
    return { baseline: status.gate.baseline, source: "frozen" };
  }
  if (status?.loop.baseline_scalar != null) {
    return { baseline: status.loop.baseline_scalar, source: "loop" };
  }
  const compileScalar =
    status?.compile?.scalar_after ?? topicRow?.compiled?.scalar ?? null;
  if (compileScalar != null) {
    return { baseline: compileScalar, source: "compile" };
  }
  const lastEval =
    topicRow?.last_eval ??
    (metrics?.records.length ? metrics.records[metrics.records.length - 1] : null);
  if (lastEval && isNaiveProbeHarness(lastEval.harness_version)) {
    return { baseline: lastEval.scalar, source: "naive_probe" };
  }
  const lastScalar =
    status?.gate.last_scalar ??
    status?.loop.metrics_hint?.last_scalar ??
    lastEval?.scalar ??
    metrics?.records[0]?.scalar ??
    null;
  if (lastScalar != null) {
    return { baseline: lastScalar, source: "last_metrics" };
  }
  return { baseline: null, source: "none" };
}

/** Latest eval/compile scalar — ignores frozen gate baseline (for freeze-at-current). */
export function resolveTopicCurrentScore(
  status: WikiStatus | null | undefined,
  metrics?: MetricsWindow | null,
  topicRow?: TopicRow | null,
): {
  score: number | null;
  source: Exclude<BaselineSource, "frozen" | "loop"> | "none";
} {
  const compileScalar =
    status?.compile?.scalar_after ?? topicRow?.compiled?.scalar ?? null;
  if (compileScalar != null) {
    return { score: compileScalar, source: "compile" };
  }
  const lastEval =
    topicRow?.last_eval ??
    (metrics?.records.length ? metrics.records[metrics.records.length - 1] : null);
  if (lastEval && isNaiveProbeHarness(lastEval.harness_version)) {
    return { score: lastEval.scalar, source: "naive_probe" };
  }
  const lastScalar =
    status?.gate.last_scalar ??
    status?.loop.metrics_hint?.last_scalar ??
    lastEval?.scalar ??
    metrics?.records[0]?.scalar ??
    null;
  if (lastScalar != null) {
    return { score: lastScalar, source: "last_metrics" };
  }
  return { score: null, source: "none" };
}

export function currentScoreSourceLabel(
  source: Exclude<BaselineSource, "frozen" | "loop"> | "none",
): string {
  if (source === "compile") return "last compile scalar";
  if (source === "naive_probe") {
    return "naive cold-start (0.0 — not held-out eval)";
  }
  if (source === "last_metrics") return "last eval scalar";
  return "no score yet";
}

export function baselineChipPrefix(source: BaselineSource): string {
  if (source === "frozen" || source === "loop") return "Baseline";
  if (source === "naive_probe") return "Cold start";
  if (source === "compile" || source === "last_metrics") return "Last score";
  return "Baseline";
}

export function baselineChipTone(source: BaselineSource): "ok" | "warn" | "" {
  if (source === "frozen" || source === "loop") return "ok";
  if (source === "compile" || source === "last_metrics" || source === "naive_probe") {
    return "warn";
  }
  return "";
}

export function baselineChipTitle(topic: string, source: BaselineSource): string {
  const topicCtx = `for ${topic}`;
  switch (source) {
    case "frozen":
      return `Per-topic gate baseline (frozen) ${topicCtx}`;
    case "loop":
      return `Per-topic gate baseline from loop-state ${topicCtx}`;
    case "compile":
      return `Latest compile scalar ${topicCtx} — baseline not frozen yet`;
    case "last_metrics":
      return `Last metrics scalar ${topicCtx} — baseline not frozen yet`;
    case "naive_probe":
      return `Naive cold-start 0.0 ${topicCtx} — run knotica eval before freezing the gate`;
    default:
      return `No gate baseline ${topicCtx}`;
  }
}

/** Whether the current score is a cold-start probe only (not safe to freeze as gate). */
export function isNaiveProbeScore(
  status: WikiStatus | null | undefined,
  metrics?: MetricsWindow | null,
  topicRow?: TopicRow | null,
): boolean {
  return resolveTopicCurrentScore(status, metrics, topicRow).source === "naive_probe";
}

/** @deprecated Use {@link isNaiveProbeScore}. */
export const isRetrievalProbeScore = isNaiveProbeScore;
/** @deprecated Use {@link isNaiveProbeScore}. */
export const isTrainSmokeScore = isNaiveProbeScore;

/** Query-style train count (compile fuel); falls back to legacy ``curated``. */
export function queryTrainCount(row: TopicRow | null | undefined): number {
  if (!row) return 0;
  return row.trainset_n ?? row.curated ?? 0;
}

export function findTopicRow(
  status: WikiStatus | null | undefined,
  topic: string,
): TopicRow | null {
  if (!status?.topics.length) return null;
  return status.topics.find((row) => row.topic === topic) ?? status.topics[0] ?? null;
}
