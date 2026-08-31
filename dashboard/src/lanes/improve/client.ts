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
 *
 * Every call here goes to the **`improve` lane dispatcher** -- the verbs it
 * routes to (`metrics_read`, `arena`, `compile`, `golden`, `datasets`, `loop`,
 * `baseline_probe`, `branches`, `prompt_diff`) are lane actions, not registered
 * tools. A verb that owns a parameter literally named `action` forwards it as
 * `<verb>_action`, because the lane's own selector is already `action`
 * (`docs/reference.md`, "Operator verbs").
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
  LoopCadenceResult,
  LoopOnceResult,
  LoopRebaselineResult,
  LoopRunEvalResult,
  LoopSetBaselineResult,
  MetricsWindow,
  PromptDiffResult,
} from "./types";

/** The registered tool every call in this group dispatches through. */
const LANE = "improve";

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
  /**
   * The **read** half of `loop action=cadence`, split out so a read cannot
   * become a write by accident.
   *
   * `loopCadence` below is dual-mode: it reads when it passes no overrides and
   * writes `config.toml` when it passes any. That made the read-safety of
   * every mount effect a property of its *arguments* — a default value added
   * to one override parameter would silently turn every focus-mount of
   * `ObserveStage` into a config write (`td-059`). This signature has no
   * override and no `confirm` parameter, so there is nothing to default: the
   * read mode is structural. Its return type is narrowed to
   * `LoopCadenceConfig` for the same reason — the preview branch is
   * unreachable without an override, so callers need no `confirm_nonce`
   * narrowing.
   *
   * Every read-mount site calls this. `loopCadence` is for writes only.
   */
  loopCadenceRead(topic: string, vault?: string): Promise<LoopCadenceConfig>;
  /**
   * The **write** half. Writes additively; one override —
   * `arenaScorer: "eval"` — is spend-gated server-side: the bare call returns
   * a `LoopCadencePreview` (nothing written) and the call must be repeated
   * with that envelope's `confirm_nonce` as `confirm` to apply. Every other
   * write, `arenaScorer: "heuristic"` included, applies in one call.
   *
   * Passing no overrides still reads, because the server endpoint is what it
   * is — but a caller that wants a read should say so with
   * `loopCadenceRead`.
   */
  loopCadence(
    topic: string,
    overrides?: {
      evalMinIntervalHours?: number;
      evalWindow?: string;
      evalNumThreads?: number;
      arenaScorer?: string;
    },
    vault?: string,
    confirm?: string,
  ): Promise<LoopCadenceResult>;
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
    return this.call(LANE, { action: "metrics_read", topic, limit: 100, vault });
  },

  arenaStatus(topic: string, vault = ""): Promise<ArenaStatus> {
    return this.call(LANE, {
      action: "arena",
      arena_action: "status",
      topic,
      vault,
    });
  },

  arenaHistory(topic: string, vault = "", limit = 20): Promise<ArenaHistory> {
    return this.call(LANE, {
      action: "arena",
      arena_action: "history",
      topic,
      vault,
      limit,
    });
  },

  compileStatus(topic: string, vault = ""): Promise<CompileStatus> {
    return this.call(LANE, {
      action: "compile",
      compile_action: "status",
      topic,
      vault,
    });
  },

  compileRun(
    topic: string,
    vault = "",
    useMipro = true,
  ): Promise<CompileRunResult> {
    return this.call(
      LANE,
      {
        action: "compile",
        compile_action: "run",
        topic,
        vault,
        use_mipro: useMipro,
      },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  compilePromote(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<CompilePromoteResult> {
    return this.call(LANE, {
      action: "compile",
      compile_action: "promote",
      topic,
      branch,
      mode,
      vault,
    });
  },

  goldenReviewLoad(topic: string, vault = ""): Promise<GoldenReview> {
    return this.call(LANE, {
      action: "golden",
      golden_action: "load",
      topic,
      vault,
    });
  },

  goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault = "",
  ): Promise<GoldenSaveResult> {
    return this.call(LANE, {
      action: "golden",
      golden_action: "save",
      topic,
      vault,
      accepted_json: JSON.stringify(accepted),
    });
  },

  datasetsInventory(topic: string, vault = ""): Promise<DatasetsInventory> {
    return this.call(LANE, {
      action: "datasets",
      datasets_action: "inventory",
      topic,
      vault,
    });
  },

  datasetsRecords(
    topic: string,
    role: string,
    vault = "",
    limit = 200,
  ): Promise<DatasetRecords> {
    return this.call(LANE, {
      action: "datasets",
      datasets_action: "records",
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
      LANE,
      { action: "datasets", datasets_action: "bootstrap", topic, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  datasetsBootstrapTrain(
    topic: string,
    target = 30,
    vault = "",
  ): Promise<DatasetsBootstrapTrainResult> {
    return this.call(
      LANE,
      {
        action: "datasets",
        datasets_action: "bootstrap_train",
        topic,
        target,
        vault,
      },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  datasetsFreeze(topic: string, vault = ""): Promise<DatasetsFreezeResult> {
    return this.call(LANE, {
      action: "datasets",
      datasets_action: "freeze",
      topic,
      vault,
    });
  },

  /** Billed and two-phase: omit `confirm` to preview, pass the returned nonce to run. */
  loopRunOnce(
    topic: string,
    confirm = "",
    vault = "",
  ): Promise<LoopOnceResult> {
    return this.call(
      LANE,
      { action: "loop", loop_action: "run_once", topic, confirm, vault },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  loopSetBaseline(
    topic: string,
    scalar: number,
    vault = "",
  ): Promise<LoopSetBaselineResult> {
    return this.call(LANE, {
      action: "loop",
      loop_action: "set_baseline",
      topic,
      scalar,
      vault,
    });
  },

  loopBaselinePolicy(
    topic: string,
    policy: "latest" | "best",
    vault = "",
  ): Promise<LoopBaselinePolicyResult> {
    return this.call(LANE, {
      action: "loop",
      loop_action: "baseline_policy",
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
    return this.call(LANE, {
      action: "loop",
      loop_action: "rebaseline",
      topic,
      mode,
      vault,
    });
  },

  baselineProbe(topic: string, vault = ""): Promise<BaselineProbeResult> {
    return this.call(LANE, { action: "baseline_probe", topic, vault });
  },

  loopCadenceRead(topic: string, vault = ""): Promise<LoopCadenceConfig> {
    return this.call(LANE, {
      action: "loop",
      loop_action: "cadence",
      topic,
      confirm: "",
      vault,
    });
  },

  loopCadence(
    topic: string,
    overrides: {
      evalMinIntervalHours?: number;
      evalWindow?: string;
      evalNumThreads?: number;
      arenaScorer?: string;
    } = {},
    vault = "",
    confirm = "",
  ): Promise<LoopCadenceResult> {
    return this.call(LANE, {
      action: "loop",
      loop_action: "cadence",
      topic,
      eval_min_interval_hours: overrides.evalMinIntervalHours,
      eval_window: overrides.evalWindow,
      eval_num_threads: overrides.evalNumThreads,
      arena_scorer: overrides.arenaScorer,
      confirm,
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
      LANE,
      {
        action: "loop",
        loop_action: "run_eval",
        topic,
        confirm,
        num_threads: numThreads,
        vault,
      },
      LLM_CALL_TIMEOUT_MS,
    );
  },

  branchScoreboard(topic: string, vault = ""): Promise<BranchScoreboard> {
    return this.call(LANE, {
      action: "branches",
      branches_action: "scoreboard",
      topic,
      vault,
    });
  },

  branchPromote(
    kind: "compile" | "loop",
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<CompilePromoteResult> {
    return this.call(LANE, {
      action: "branches",
      branches_action: "promote",
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
    return this.call(LANE, {
      action: "branches",
      branches_action: "delete",
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
    return this.call(LANE, {
      action: "prompt_diff",
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
