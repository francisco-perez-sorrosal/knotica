import { App } from "@modelcontextprotocol/ext-apps";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import type {
  ArenaHistory,
  ArenaStatus,
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
  DoctorRepairResult,
  DoctorReport,
  GoldenCandidate,
  GoldenReview,
  GoldenSaveResult,
  IngestActivity,
  LoopBaselinePolicyResult,
  LoopOnceResult,
  LoopRebaselineResult,
  LoopSetBaselineResult,
  BaselineProbeResult,
  MetricsWindow,
  OkfCheckResult,
  OkfRepairResult,
  QueryAnswer,
  PromptDiffResult,
  SuggestionAction,
  SuggestionsReadResult,
  SuggestionsStatusFilter,
  SuggestionReviewResult,
  VaultLintResult,
  VaultMetadataTree,
  WikiStatus,
} from "./types";

export interface ToolClient {
  wikiStatus(topic: string, vault?: string): Promise<WikiStatus>;
  metricsRead(topic: string, vault?: string): Promise<MetricsWindow>;
  query(topic: string, question: string, vault?: string): Promise<QueryAnswer>;
  curateExample(
    topic: string,
    query: string,
    answer: string,
    verdict: "good" | "bad",
    pagesUsed?: string[],
    vault?: string,
  ): Promise<Record<string, unknown>>;
  arenaStatus(topic: string, vault?: string): Promise<ArenaStatus>;
  arenaHistory(topic: string, vault?: string, limit?: number): Promise<ArenaHistory>;
  compileStatus(topic: string, vault?: string): Promise<CompileStatus>;
  compileRun(topic: string, vault?: string, useMipro?: boolean): Promise<CompileRunResult>;
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
  datasetsBootstrap(topic: string, vault?: string): Promise<DatasetsBootstrapResult>;
  datasetsBootstrapTrain(
    topic: string,
    target?: number,
    vault?: string,
  ): Promise<DatasetsBootstrapTrainResult>;
  datasetsFreeze(topic: string, vault?: string): Promise<DatasetsFreezeResult>;
  ingestActivityRead(topic: string, vault?: string, runId?: string): Promise<IngestActivity>;
  doctorRun(vault?: string, quick?: boolean, fix?: boolean): Promise<DoctorReport>;
  doctorRepair(
    mode: "dry-run" | "apply",
    vault?: string,
    paths?: string[],
    allTracked?: boolean,
    deleteUntracked?: boolean,
  ): Promise<DoctorRepairResult>;
  vaultLint(topic?: string, vault?: string): Promise<VaultLintResult>;
  vaultMetadataTree(vault?: string, topic?: string): Promise<VaultMetadataTree>;
  okfCheck(vault?: string, strict?: boolean): Promise<OkfCheckResult>;
  okfRepair(mode: "dry-run" | "apply", vault?: string, force?: boolean): Promise<OkfRepairResult>;
  loopRunOnce(topic: string, vault?: string): Promise<LoopOnceResult>;
  loopSetBaseline(topic: string, scalar: number, vault?: string): Promise<LoopSetBaselineResult>;
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
  suggestionsRead(
    topic: string,
    status?: SuggestionsStatusFilter,
    cursor?: string,
    limit?: number,
    vault?: string,
  ): Promise<SuggestionsReadResult>;
  suggestionsReview(
    topic: string,
    suggestionId: string,
    action: SuggestionAction,
    mode: "dry-run" | "apply",
    reason?: string,
    vault?: string,
  ): Promise<SuggestionReviewResult>;
  close(): Promise<void>;
}

/** Shared tool wrappers — subclasses only implement transport ``call``. */
abstract class BaseToolClient implements ToolClient {
  protected abstract call<T>(name: string, args: Record<string, unknown>): Promise<T>;

  wikiStatus(topic: string, vault = ""): Promise<WikiStatus> {
    return this.call("wiki_status", { topic, vault });
  }

  metricsRead(topic: string, vault = ""): Promise<MetricsWindow> {
    return this.call("metrics_read", { topic, limit: 100, vault });
  }

  query(topic: string, question: string, vault = ""): Promise<QueryAnswer> {
    return this.call("query", { topic, question, vault });
  }

  curateExample(
    topic: string,
    query: string,
    answer: string,
    verdict: "good" | "bad",
    pagesUsed: string[] = [],
    vault = "",
  ): Promise<Record<string, unknown>> {
    return this.call("curate_example", {
      topic,
      query,
      answer,
      verdict,
      pages_used: pagesUsed,
      vault,
    });
  }

  arenaStatus(topic: string, vault = ""): Promise<ArenaStatus> {
    return this.call("arena_status", { topic, vault });
  }

  arenaHistory(topic: string, vault = "", limit = 20): Promise<ArenaHistory> {
    return this.call("arena_history", { topic, vault, limit });
  }

  compileStatus(topic: string, vault = ""): Promise<CompileStatus> {
    return this.call("compile_status", { topic, vault });
  }

  compileRun(topic: string, vault = "", useMipro = true): Promise<CompileRunResult> {
    return this.call("compile_run", { topic, vault, use_mipro: useMipro });
  }

  compilePromote(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<CompilePromoteResult> {
    return this.call("compile_promote", { topic, branch, mode, vault });
  }

  goldenReviewLoad(topic: string, vault = ""): Promise<GoldenReview> {
    return this.call("golden_review_load", { topic, vault });
  }

  goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault = "",
  ): Promise<GoldenSaveResult> {
    return this.call("golden_review_save", {
      topic,
      vault,
      accepted_json: JSON.stringify(accepted),
    });
  }

  datasetsInventory(topic: string, vault = ""): Promise<DatasetsInventory> {
    return this.call("datasets_inventory", { topic, vault });
  }

  datasetsRecords(
    topic: string,
    role: string,
    vault = "",
    limit = 200,
  ): Promise<DatasetRecords> {
    return this.call("datasets_records", { topic, role, vault, limit });
  }

  datasetsBootstrap(topic: string, vault = ""): Promise<DatasetsBootstrapResult> {
    return this.call("datasets_bootstrap", { topic, vault });
  }

  datasetsBootstrapTrain(
    topic: string,
    target = 30,
    vault = "",
  ): Promise<DatasetsBootstrapTrainResult> {
    return this.call("datasets_bootstrap_train", { topic, target, vault });
  }

  datasetsFreeze(topic: string, vault = ""): Promise<DatasetsFreezeResult> {
    return this.call("datasets_freeze", { topic, vault });
  }

  ingestActivityRead(topic: string, vault = "", runId = ""): Promise<IngestActivity> {
    return this.call("ingest_activity_read", {
      topic,
      vault,
      run_id: runId,
      limit: 120,
    });
  }

  doctorRun(vault = "", quick = false, fix = false): Promise<DoctorReport> {
    return this.call("doctor_run", { vault, quick, fix });
  }

  doctorRepair(
    mode: "dry-run" | "apply",
    vault = "",
    paths: string[] = [],
    allTracked = false,
    deleteUntracked = false,
  ): Promise<DoctorRepairResult> {
    return this.call("doctor_repair", {
      mode,
      vault,
      paths_json: JSON.stringify(paths),
      all_tracked: allTracked,
      delete_untracked: deleteUntracked,
    });
  }

  vaultLint(topic = "", vault = ""): Promise<VaultLintResult> {
    return this.call("vault_lint", { topic, vault });
  }

  vaultMetadataTree(vault = "", topic = ""): Promise<VaultMetadataTree> {
    return this.call("vault_metadata_tree", { vault, topic });
  }

  okfCheck(vault = "", strict = false): Promise<OkfCheckResult> {
    return this.call("okf_check", { vault, strict });
  }

  okfRepair(mode: "dry-run" | "apply", vault = "", force = false): Promise<OkfRepairResult> {
    return this.call("okf_repair", { mode, vault, force });
  }

  loopRunOnce(topic: string, vault = ""): Promise<LoopOnceResult> {
    return this.call("loop_run_once", { topic, vault });
  }

  loopSetBaseline(topic: string, scalar: number, vault = ""): Promise<LoopSetBaselineResult> {
    return this.call("loop_set_baseline", { topic, scalar, vault });
  }

  loopBaselinePolicy(
    topic: string,
    policy: "latest" | "best",
    vault = "",
  ): Promise<LoopBaselinePolicyResult> {
    return this.call("loop_baseline_policy", { topic, policy, vault });
  }

  loopRebaseline(
    topic: string,
    mode: "best" | "latest" = "best",
    vault = "",
  ): Promise<LoopRebaselineResult> {
    return this.call("loop_rebaseline", { topic, mode, vault });
  }

  baselineProbe(topic: string, vault = ""): Promise<BaselineProbeResult> {
    return this.call("baseline_probe", { topic, vault });
  }

  branchScoreboard(topic: string, vault = ""): Promise<BranchScoreboard> {
    return this.call("branch_scoreboard", { topic, vault });
  }

  branchPromote(
    kind: "compile" | "loop",
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<CompilePromoteResult> {
    return this.call("branch_promote", { kind, topic, branch, mode, vault });
  }

  branchDelete(
    topic: string,
    branch: string,
    mode: "dry-run" | "apply",
    vault = "",
  ): Promise<BranchDeleteResult> {
    return this.call("branch_delete", { topic, branch, mode, vault });
  }

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
  }

  suggestionsRead(
    topic: string,
    status: SuggestionsStatusFilter = "pending",
    cursor = "",
    limit = 20,
    vault = "",
  ): Promise<SuggestionsReadResult> {
    return this.call("suggestions_read", { topic, status, cursor, limit, vault });
  }

  suggestionsReview(
    topic: string,
    suggestionId: string,
    action: SuggestionAction,
    mode: "dry-run" | "apply" = "dry-run",
    reason = "",
    vault = "",
  ): Promise<SuggestionReviewResult> {
    return this.call("suggestions_review", {
      topic,
      suggestion_id: suggestionId,
      action,
      mode,
      reason,
      vault,
    });
  }

  abstract close(): Promise<void>;
}

/** Standalone client for the dashboard's own stateless streamable-HTTP mount. */
export class HttpToolClient extends BaseToolClient {
  private readonly client = new Client({ name: "knotica-dashboard", version: "0.1.0" });
  private connected: Promise<void> | undefined;

  constructor(private readonly endpoint: string) {
    super();
  }

  async close(): Promise<void> {
    await this.client.close();
  }

  protected async call<T>(name: string, args: Record<string, unknown>): Promise<T> {
    await this.connect();
    const result = await this.client.callTool({ name, arguments: args });
    return extractToolPayload<T>(result, name);
  }

  private connect(): Promise<void> {
    this.connected ??= this.client.connect(
      new StreamableHTTPClientTransport(new URL(this.endpoint)),
    );
    return this.connected;
  }
}

/**
 * MCP-App client: JSON-RPC over postMessage via ``@modelcontextprotocol/ext-apps``.
 * Used inside the sandboxed ``ui://`` iframe (Claude Desktop / claude.ai).
 */
export class BridgeToolClient extends BaseToolClient {
  private readonly ready: Promise<void>;

  constructor(private readonly app: App) {
    super();
    this.ready = Promise.resolve();
  }

  /** Connect a fresh App instance and return a client ready for tool calls. */
  static async connect(): Promise<{ client: BridgeToolClient; app: App }> {
    const app = new App({ name: "knotica-dashboard", version: "0.1.0" });
    await app.connect();
    return { client: new BridgeToolClient(app), app };
  }

  async close(): Promise<void> {
    // Host owns the postMessage transport lifetime.
  }

  protected async call<T>(name: string, args: Record<string, unknown>): Promise<T> {
    await this.ready;
    const result = await this.app.callServerTool({ name, arguments: args });
    return extractToolPayload<T>(result, name);
  }
}

/** Prefer bridge when framed (ui:// iframe); HTTP when top-level (browser mount). */
export function preferBridgeMount(): boolean {
  const forced = new URLSearchParams(window.location.search).get("mount");
  if (forced === "bridge") return true;
  if (forced === "http") return false;
  return window.parent !== window;
}

export function extractToolPayload<T>(result: unknown, name: string): T {
  const toolResult = isRecord(result) && "toolResult" in result ? result.toolResult : result;
  if (isRecord(toolResult) && toolResult.isError) {
    throw new Error(formatToolFailure(readResultText(toolResult), name));
  }
  const payload =
    isRecord(toolResult) && "structuredContent" in toolResult && toolResult.structuredContent != null
      ? toolResult.structuredContent
      : readResultText(toolResult);
  if (payload === undefined) {
    throw new Error(`${name} returned no structured content`);
  }
  if (isRecord(payload) && "error" in payload) {
    throw new Error(formatToolFailure(payload, name));
  }
  return payload as T;
}

/** Turn MCP error envelopes / JSON blobs into a readable message (never "[object Object]"). */
export function formatToolFailure(payload: unknown, name: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (isRecord(payload)) {
    const err = isRecord(payload.error) ? payload.error : payload;
    if (typeof err.message === "string" && err.message.trim()) {
      const fix = typeof err.fix === "string" && err.fix.trim() ? ` To fix: ${err.fix}` : "";
      return `${err.message}${fix}`;
    }
  }
  return `${name} failed`;
}

/** Read topic from an ``open_dashboard`` tool-input payload when the host supplies one. */
export function topicFromToolInput(input: unknown, fallback: string): string {
  if (!isRecord(input)) return fallback;
  const args = isRecord(input.arguments) ? input.arguments : input;
  const topic = args.topic;
  if (typeof topic !== "string") return fallback;
  const cleaned = topic.trim().replace(/^\/+|\/+$/g, "");
  return cleaned || fallback;
}

export function vaultFromToolInput(input: unknown, fallback: string): string {
  if (!isRecord(input)) return fallback;
  const args = isRecord(input.arguments) ? input.arguments : input;
  const vault = args.vault;
  if (typeof vault !== "string") return fallback;
  return vault.trim();
}

function readResultText(result: unknown): unknown {
  if (!isRecord(result) || !Array.isArray(result.content)) return undefined;
  const text = result.content.find(
    (item): item is { type: string; text: string } =>
      isRecord(item) && item.type === "text" && typeof item.text === "string",
  )?.text;
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
