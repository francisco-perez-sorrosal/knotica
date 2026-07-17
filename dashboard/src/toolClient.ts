import { App } from "@modelcontextprotocol/ext-apps";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

import type {
  DoctorRepairResult,
  DoctorReport,
  GoldenCandidate,
  GoldenReview,
  GoldenSaveResult,
  IngestActivity,
  LoopOnceResult,
  MetricsWindow,
  OkfCheckResult,
  OkfRepairResult,
  VaultLintResult,
  WikiStatus,
} from "./types";

export interface ToolClient {
  wikiStatus(topic: string, vault?: string): Promise<WikiStatus>;
  metricsRead(topic: string, vault?: string): Promise<MetricsWindow>;
  goldenReviewLoad(topic: string, vault?: string): Promise<GoldenReview>;
  goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault?: string,
  ): Promise<GoldenSaveResult>;
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
  okfCheck(vault?: string, strict?: boolean): Promise<OkfCheckResult>;
  okfRepair(mode: "dry-run" | "apply", vault?: string, force?: boolean): Promise<OkfRepairResult>;
  loopRunOnce(topic: string, vault?: string): Promise<LoopOnceResult>;
  close(): Promise<void>;
}

/** Standalone client for the dashboard's own stateless streamable-HTTP mount. */
export class HttpToolClient implements ToolClient {
  private readonly client = new Client({ name: "knotica-dashboard", version: "0.1.0" });
  private connected: Promise<void> | undefined;

  constructor(private readonly endpoint: string) {}

  async wikiStatus(topic: string, vault = ""): Promise<WikiStatus> {
    return this.call<WikiStatus>("wiki_status", { topic, vault });
  }

  async metricsRead(topic: string, vault = ""): Promise<MetricsWindow> {
    return this.call<MetricsWindow>("metrics_read", { topic, limit: 100, vault });
  }

  async goldenReviewLoad(topic: string, vault = ""): Promise<GoldenReview> {
    return this.call<GoldenReview>("golden_review_load", { topic, vault });
  }

  async goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault = "",
  ): Promise<GoldenSaveResult> {
    return this.call<GoldenSaveResult>("golden_review_save", {
      topic,
      vault,
      accepted_json: JSON.stringify(accepted),
    });
  }

  async ingestActivityRead(topic: string, vault = "", runId = ""): Promise<IngestActivity> {
    return this.call<IngestActivity>("ingest_activity_read", {
      topic,
      vault,
      run_id: runId,
      limit: 120,
    });
  }

  async doctorRun(vault = "", quick = false, fix = false): Promise<DoctorReport> {
    return this.call<DoctorReport>("doctor_run", { vault, quick, fix });
  }

  async doctorRepair(
    mode: "dry-run" | "apply",
    vault = "",
    paths: string[] = [],
    allTracked = false,
    deleteUntracked = false,
  ): Promise<DoctorRepairResult> {
    return this.call<DoctorRepairResult>("doctor_repair", {
      mode,
      vault,
      paths_json: JSON.stringify(paths),
      all_tracked: allTracked,
      delete_untracked: deleteUntracked,
    });
  }

  async vaultLint(topic = "", vault = ""): Promise<VaultLintResult> {
    return this.call<VaultLintResult>("vault_lint", { topic, vault });
  }

  async okfCheck(vault = "", strict = false): Promise<OkfCheckResult> {
    return this.call<OkfCheckResult>("okf_check", { vault, strict });
  }

  async okfRepair(
    mode: "dry-run" | "apply",
    vault = "",
    force = false,
  ): Promise<OkfRepairResult> {
    return this.call<OkfRepairResult>("okf_repair", { mode, vault, force });
  }

  async loopRunOnce(topic: string, vault = ""): Promise<LoopOnceResult> {
    return this.call<LoopOnceResult>("loop_run_once", { topic, vault });
  }

  async close(): Promise<void> {
    await this.client.close();
  }

  private async call<T>(name: string, args: Record<string, unknown>): Promise<T> {
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
export class BridgeToolClient implements ToolClient {
  private readonly ready: Promise<void>;

  constructor(private readonly app: App) {
    this.ready = Promise.resolve();
  }

  /** Connect a fresh App instance and return a client ready for tool calls. */
  static async connect(): Promise<{ client: BridgeToolClient; app: App }> {
    const app = new App({ name: "knotica-dashboard", version: "0.1.0" });
    await app.connect();
    return { client: new BridgeToolClient(app), app };
  }

  async wikiStatus(topic: string, vault = ""): Promise<WikiStatus> {
    return this.call<WikiStatus>("wiki_status", { topic, vault });
  }

  async metricsRead(topic: string, vault = ""): Promise<MetricsWindow> {
    return this.call<MetricsWindow>("metrics_read", { topic, limit: 100, vault });
  }

  async goldenReviewLoad(topic: string, vault = ""): Promise<GoldenReview> {
    return this.call<GoldenReview>("golden_review_load", { topic, vault });
  }

  async goldenReviewSave(
    topic: string,
    accepted: GoldenCandidate[],
    vault = "",
  ): Promise<GoldenSaveResult> {
    return this.call<GoldenSaveResult>("golden_review_save", {
      topic,
      vault,
      accepted_json: JSON.stringify(accepted),
    });
  }

  async ingestActivityRead(topic: string, vault = "", runId = ""): Promise<IngestActivity> {
    return this.call<IngestActivity>("ingest_activity_read", {
      topic,
      vault,
      run_id: runId,
      limit: 120,
    });
  }

  async doctorRun(vault = "", quick = false, fix = false): Promise<DoctorReport> {
    return this.call<DoctorReport>("doctor_run", { vault, quick, fix });
  }

  async doctorRepair(
    mode: "dry-run" | "apply",
    vault = "",
    paths: string[] = [],
    allTracked = false,
    deleteUntracked = false,
  ): Promise<DoctorRepairResult> {
    return this.call<DoctorRepairResult>("doctor_repair", {
      mode,
      vault,
      paths_json: JSON.stringify(paths),
      all_tracked: allTracked,
      delete_untracked: deleteUntracked,
    });
  }

  async vaultLint(topic = "", vault = ""): Promise<VaultLintResult> {
    return this.call<VaultLintResult>("vault_lint", { topic, vault });
  }

  async okfCheck(vault = "", strict = false): Promise<OkfCheckResult> {
    return this.call<OkfCheckResult>("okf_check", { vault, strict });
  }

  async okfRepair(
    mode: "dry-run" | "apply",
    vault = "",
    force = false,
  ): Promise<OkfRepairResult> {
    return this.call<OkfRepairResult>("okf_repair", { mode, vault, force });
  }

  async loopRunOnce(topic: string, vault = ""): Promise<LoopOnceResult> {
    return this.call<LoopOnceResult>("loop_run_once", { topic, vault });
  }

  async close(): Promise<void> {
    // The host owns the postMessage transport lifetime; nothing to tear down.
  }

  private async call<T>(name: string, args: Record<string, unknown>): Promise<T> {
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
function formatToolFailure(payload: unknown, name: string): string {
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

export type { CallToolResult };
