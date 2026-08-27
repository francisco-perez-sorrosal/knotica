/**
 * Improve's half of `ToolClient`: the topic-scoped measured loop.
 *
 * Every stage of the lane reaches for something here -- observe reads metrics
 * and cadence, heal runs the arena and the compile, instrument moves datasets,
 * prove and promote work the branch scoreboard, and gate runs the loop. It is
 * the largest group because it is the only lane that both measures and acts on
 * what it measured.
 *
 * Five of these calls bill: `compile action=run`, both `datasets` bootstraps,
 * and both `loop` runs. They carry `LLM_CALL_TIMEOUT_MS`; nothing else here
 * does, and the distinction is the file's one real invariant.
 */

import { LLM_CALL_TIMEOUT_MS, type ToolCallGroup } from "../../toolClientCore";

import type {
  ArenaHistory,
  ArenaStatus,
  BaselineProbeResult,
  BranchDeleteResult,
  BranchScoreboard,
  CompilePromoteResult,
  CompileRunResult,
  CompileStatus,
  DatasetRecords,
  DatasetsBootstrapResult,
  DatasetsBootstrapTrainResult,
  DatasetsFreezeResult,
  DatasetsInventory,
  GoldenCandidate,
  GoldenReview,
  GoldenSaveResult,
  LoopBaselinePolicyResult,
  LoopCadenceConfig,
  LoopOnceResult,
  LoopRebaselineResult,
  LoopRunEvalResult,
  LoopSetBaselineResult,
  MetricsWindow,
  PromptDiffResult,
} from "./types";

export interface ImproveToolCalls {
  metricsRead(topic: string, vault?: string): Promise<MetricsWindow>;
  arenaStatus(topic: string, vault?: string): Promise<ArenaStatus>;
  arenaHistory(
    topic: string,
    vault?: string,
    limit?: number,
  ): Promise<ArenaHistory>;
  compileStatus(topic: string, vault?: string): Promise<CompileStatus>;
  compileRun(
    topic: string,
    vault?: string,
    useMipro?: boolean,
  ): Promise<CompileRunResult>;
  compilePromote(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault?: string,
  ): Promise<CompilePromoteResult>;
  goldenReviewLoad(topic: string, vault?: string): Promise<GoldenReview>;
  goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault?: string,
  ): Promise<GoldenSaveResult>;
  datasetsInventory(topic: string, vault?: string): Promise<DatasetsInventory>;
  datasetsRecords(
    topic: string,
    role: string,
    vault?: string,
    limit?: number,
  ): Promise<DatasetRecords>;
  datasetsBootstrap(
    topic: string,
    vault?: string,
  ): Promise<DatasetsBootstrapResult>;
  datasetsBootstrapTrain(
    topic: string,
    target?: number,
    vault?: string,
  ): Promise<DatasetsBootstrapTrainResult>;
  datasetsFreeze(topic: string, vault?: string): Promise<DatasetsFreezeResult>;
  loopRunOnce(
    topic: string,
    confirm?: string,
    vault?: string,
  ): Promise<LoopOnceResult>;
  loopSetBaseline(
    topic: string,
    scalar: number,
    vault?: string,
  ): Promise<LoopSetBaselineResult>;
  loopBaselinePolicy(
    topic: string,
    policy: "latest" | "best",
    vault?: string,
  ): Promise<LoopBaselinePolicyResult>;
  loopRebaseline(
    topic: string,
    mode: "best" | "latest",
    vault?: string,
  ): Promise<LoopRebaselineResult>;
  baselineProbe(topic: string, vault?: string): Promise<BaselineProbeResult>;
  loopCadence(
    topic: string,
    overrides?: {
      evalMinIntervalHours?: number;
      evalWindow?: string;
      evalNumThreads?: number;
    },
    vault?: string,
  ): Promise<LoopCadenceConfig>;
  loopRunEval(
    topic: string,
    confirm?: string,
    numThreads?: number,
    vault?: string,
  ): Promise<LoopRunEvalResult>;
  branchScoreboard(topic: string, vault?: string): Promise<BranchScoreboard>;
  branchPromote(
    kind: "compile" | "loop",
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault?: string,
  ): Promise<CompilePromoteResult>;
  branchDelete(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault?: string,
  ): Promise<BranchDeleteResult>;
  promptDiff(
    topic: string,
    branch?: string,
    vault?: string,
    baseRef?: string,
    headRef?: string,
    historyId?: string,
    mode?: "git" | "compiled",
  ): Promise<PromptDiffResult>;
}

export const improveToolCalls: ToolCallGroup<ImproveToolCalls> = {
  metricsRead(topic: string, vault = ""): Promise<MetricsWindow> {
    return this.call("metrics_read", { topic, limit: 100, vault });
  },

  arenaStatus(topic: string, vault = ""): Promise<ArenaStatus> {
    return this.call("arena", { action: "status", topic, vault });
  },

  arenaHistory(topic: string, vault = "", limit = 20): Promise<ArenaHistory> {
    return this.call("arena", { action: "history", topic, vault, limit });
  },

  compileStatus(topic: string, vault = ""): Promise<CompileStatus> {
    return this.call("compile", { action: "status", topic, vault });
  },

  compileRun(
    topic: string,
    vault = "",
    useMipro = true,
  ): Promise<CompileRunResult> {
    return this.call(
      "compile",
      { action: "run", topic, vault, use_mipro: useMipro },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  compilePromote(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<CompilePromoteResult> {
    return this.call("compile", {
      action: "promote",
      topic,
      branch,
      mode,
      vault,
    });
  },

  goldenReviewLoad(topic: string, vault = ""): Promise<GoldenReview> {
    return this.call("golden", { action: "load", topic, vault });
  },

  goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault = "",
  ): Promise<GoldenSaveResult> {
    return this.call("golden", {
      action: "save",
      topic,
      vault,
      accepted_json: JSON.stringify(accepted),
    });
  },

  datasetsInventory(topic: string, vault = ""): Promise<DatasetsInventory> {
    return this.call("datasets", { action: "inventory", topic, vault });
  },

  datasetsRecords(
    topic: string,
    role: string,
    vault = "",
    limit = 200,
  ): Promise<DatasetRecords> {
    return this.call("datasets", {
      action: "records",
      topic,
      role,
      vault,
      limit,
    });
  },

  datasetsBootstrap(
    topic: string,
    vault = "",
  ): Promise<DatasetsBootstrapResult> {
    return this.call(
      "datasets",
      { action: "bootstrap", topic, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  datasetsBootstrapTrain(
    topic: string,
    target = 30,
    vault = "",
  ): Promise<DatasetsBootstrapTrainResult> {
    return this.call(
      "datasets",
      { action: "bootstrap_train", topic, target, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  datasetsFreeze(topic: string, vault = ""): Promise<DatasetsFreezeResult> {
    return this.call("datasets", { action: "freeze", topic, vault });
  },

  /** Billed and two-phase: omit `confirm` to preview, pass the returned nonce to run. */
  loopRunOnce(
    topic: string,
    confirm = "",
    vault = "",
  ): Promise<LoopOnceResult> {
    return this.call(
      "loop",
      { action: "run_once", topic, confirm, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  loopSetBaseline(
    topic: string,
    scalar: number,
    vault = "",
  ): Promise<LoopSetBaselineResult> {
    return this.call("loop", { action: "set_baseline", topic, scalar, vault });
  },

  loopBaselinePolicy(
    topic: string,
    policy: "latest" | "best",
    vault = "",
  ): Promise<LoopBaselinePolicyResult> {
    return this.call("loop", {
      action: "baseline_policy",
      topic,
      policy,
      vault,
    });
  },

  loopRebaseline(
    topic: string,
    mode: "best" | "latest" = "best",
    vault = "",
  ): Promise<LoopRebaselineResult> {
    return this.call("loop", { action: "rebaseline", topic, mode, vault });
  },

  baselineProbe(topic: string, vault = ""): Promise<BaselineProbeResult> {
    return this.call("baseline_probe", { topic, vault });
  },

  loopCadence(
    topic: string,
    overrides: {
      evalMinIntervalHours?: number;
      evalWindow?: string;
      evalNumThreads?: number;
    } = {},
    vault = "",
  ): Promise<LoopCadenceConfig> {
    return this.call("loop", {
      action: "cadence",
      topic,
      eval_min_interval_hours: overrides.evalMinIntervalHours,
      eval_window: overrides.evalWindow,
      eval_num_threads: overrides.evalNumThreads,
      vault,
    });
  },

  loopRunEval(
    topic: string,
    confirm = "",
    numThreads?: number,
    vault = "",
  ): Promise<LoopRunEvalResult> {
    return this.call(
      "loop",
      { action: "run_eval", topic, confirm, num_threads: numThreads, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  branchScoreboard(topic: string, vault = ""): Promise<BranchScoreboard> {
    return this.call("branches", { action: "scoreboard", topic, vault });
  },

  branchPromote(
    kind: "compile" | "loop",
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<CompilePromoteResult> {
    return this.call("branches", {
      action: "promote",
      kind,
      topic,
      branch,
      mode,
      vault,
    });
  },

  branchDelete(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<BranchDeleteResult> {
    return this.call("branches", {
      action: "delete",
      topic,
      branch,
      mode,
      vault,
    });
  },

  promptDiff(
    topic: string,
    branch = "",
    vault = "",
    baseRef = "",
    headRef = "",
    historyId = "",
    mode: "git" | "compiled" = "git",
  ): Promise<PromptDiffResult> {
    return this.call("prompt_diff", {
      topic,
      branch,
      vault,
      base_ref: baseRef,
      head_ref: headRef,
      history_id: historyId,
      mode,
    });
  },
};
