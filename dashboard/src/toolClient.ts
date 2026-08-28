import { App } from "@modelcontextprotocol/ext-apps";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { resolveLaneFocus } from "./paneRouting";
import { installToolCallGroups } from "./toolClientCore";
import type { HostCapabilities, Mount } from "./lanes/hostCapabilities";
import { answerToolCalls, type AnswerToolCalls } from "./lanes/answer/client";
import { fillToolCalls, type FillToolCalls } from "./lanes/fill/client";
import { homeToolCalls, type HomeToolCalls } from "./lanes/home/client";
import {
  improveToolCalls,
  type ImproveToolCalls,
} from "./lanes/improve/client";
import { learnToolCalls, type LearnToolCalls } from "./lanes/learn/client";
import { tendToolCalls, type TendToolCalls } from "./lanes/tend/client";
import type { PaneId } from "./types";

/**
 * The single client type every lane component holds.
 *
 * Its per-lane halves are declared beside the lanes that call them
 * (`lanes/<lane>/client.ts`); what is declared here is what belongs to no one
 * lane -- vault and topic administration, which the shell owns, and the
 * transport surface itself. Composition is by interface extension, so the
 * member set is exactly the union: consumers see one type and import it from
 * one place, as they always have.
 */
export interface ToolClient
  extends
    HomeToolCalls,
    LearnToolCalls,
    AnswerToolCalls,
    ImproveToolCalls,
    FillToolCalls,
    TendToolCalls {
  vaultUse(name: string): Promise<Record<string, unknown>>;
  vaultCreate(
    name: string,
    path: string,
    topic?: string,
    makeDefault?: boolean,
  ): Promise<Record<string, unknown>>;
  createTopic(
    topic: string,
    description?: string,
    vault?: string,
  ): Promise<Record<string, unknown>>;
  /** Capabilities the current mount advertises -- `{}` off-bridge. Read once by `deriveDispatchTier`; no lane re-derives `mount` itself (`INTERFACE_DESIGN.md §3.5`). */
  readonly hostCapabilities: HostCapabilities;
  /** Which transport this client speaks over -- fixed for the client's lifetime. */
  readonly mount: Mount;
  /** `ui/message` -- starts a turn in the host's conversation. A no-op off-bridge. */
  sendMessage(text: string): Promise<void>;
  /** `ui/update-model-context` -- queues context for the next turn, no turn started. A no-op off-bridge. */
  updateModelContext(text: string): Promise<void>;
  close(): Promise<void>;
}

/**
 * Declaration merging is what makes the composition real rather than nominal:
 * the six lane groups are installed onto this prototype at module load, and
 * this interface tells the compiler they are there. `implements ToolClient`
 * below is then a genuine check over the whole merged member set, not just
 * over the handful of methods the class body declares.
 */
interface BaseToolClient
  extends
    HomeToolCalls,
    LearnToolCalls,
    AnswerToolCalls,
    ImproveToolCalls,
    FillToolCalls,
    TendToolCalls {}

/**
 * Everything a tool client does that is not a tool call: the transport hook
 * subclasses fill in, and the vault/topic administration the shell drives.
 */
abstract class BaseToolClient implements ToolClient {
  protected abstract call<T>(
    name: string,
    args: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T>;

  abstract readonly hostCapabilities: HostCapabilities;
  abstract readonly mount: Mount;
  abstract sendMessage(text: string): Promise<void>;
  abstract updateModelContext(text: string): Promise<void>;

  vaultUse(name: string): Promise<Record<string, unknown>> {
    return this.call("vault", { action: "use", name });
  }

  vaultCreate(
    name: string,
    path: string,
    topic = "",
    makeDefault = true,
  ): Promise<Record<string, unknown>> {
    return this.call("vault", {
      action: "create",
      name,
      path,
      topic,
      make_default: makeDefault,
    });
  }

  /**
   * `vault action=create` seeds at most one topic, so every topic after the
   * first needed chat or the CLI. A knowledge base is normally several topics,
   * which made "build a KB from the dashboard" false at the second topic.
   *
   * Deterministic and unbilled — no server-side model — so it keeps the default
   * request timeout rather than the LLM deadline.
   *
   * `create_topic` is not a registered tool: it is the `learn` lane's `source`
   * stage action, and `learn` is its primary lane per `docs/reference.md`'s
   * operator-verb table.
   */
  createTopic(
    topic: string,
    description = "",
    vault = "",
  ): Promise<Record<string, unknown>> {
    return this.call("learn", {
      action: "create_topic",
      topic,
      description,
      vault,
    });
  }

  abstract close(): Promise<void>;
}

installToolCallGroups(
  BaseToolClient.prototype,
  homeToolCalls,
  learnToolCalls,
  answerToolCalls,
  improveToolCalls,
  fillToolCalls,
  tendToolCalls,
);

/** Standalone client for the dashboard's own stateless streamable-HTTP mount. */
export class HttpToolClient extends BaseToolClient {
  private readonly client = new Client({
    name: "knotica-dashboard",
    version: "0.1.0",
  });
  private connected: Promise<void> | undefined;

  /** No host over HTTP -- nothing to advertise. */
  readonly hostCapabilities: HostCapabilities = {};
  readonly mount: Mount = "http";

  constructor(private readonly endpoint: string) {
    super();
  }

  async close(): Promise<void> {
    await this.client.close();
  }

  /**
   * `deriveDispatchTier` resolves tier D for every HTTP-mounted client
   * regardless of capabilities, and tier D never invokes this method -- there
   * is no host to send to. A no-op keeps the interface total rather than
   * throwing on a path a correctly-tiered caller cannot reach.
   */
  async sendMessage(): Promise<void> {}

  /** See `sendMessage` -- same reasoning, same unreachable-by-tier-D guarantee. */
  async updateModelContext(): Promise<void> {}

  protected async call<T>(
    name: string,
    args: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T> {
    await this.connect();
    const result = await this.client.callTool(
      { name, arguments: args },
      undefined,
      timeoutMs === undefined ? undefined : { timeout: timeoutMs },
    );
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
  readonly hostCapabilities: HostCapabilities;
  readonly mount: Mount = "bridge";

  constructor(private readonly app: App) {
    super();
    this.ready = Promise.resolve();
    // Optional-called: fakes standing in for `App` in tests (recording hosts
    // that only implement `callServerTool`) predate this capability, and a
    // real host that hasn't upgraded should degrade to "advertises nothing"
    // rather than throw at construction.
    this.hostCapabilities = this.app.getHostCapabilities?.() ?? {};
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

  /** Starts a turn in the host's conversation (`ui/message`, tier A). */
  async sendMessage(text: string): Promise<void> {
    await this.app.sendMessage({
      role: "user",
      content: [{ type: "text", text }],
    });
  }

  /** Queues context for the next turn, without starting one (`ui/update-model-context`, tier B). */
  async updateModelContext(text: string): Promise<void> {
    await this.app.updateModelContext({ content: [{ type: "text", text }] });
  }

  protected async call<T>(
    name: string,
    args: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<T> {
    await this.ready;
    const result = await this.app.callServerTool(
      { name, arguments: args },
      timeoutMs === undefined ? undefined : { timeout: timeoutMs },
    );
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
  const toolResult =
    isRecord(result) && "toolResult" in result ? result.toolResult : result;
  if (isRecord(toolResult) && toolResult.isError) {
    throw new Error(formatToolFailure(readResultText(toolResult), name));
  }
  const payload =
    isRecord(toolResult) &&
    "structuredContent" in toolResult &&
    toolResult.structuredContent != null
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
      const fix =
        typeof err.fix === "string" && err.fix.trim()
          ? ` To fix: ${err.fix}`
          : "";
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

/**
 * Read `lane`/`focus` from an ``open_dashboard`` tool-input payload and resolve
 * the pane to open.
 *
 * Mirrors `topicFromToolInput`/`vaultFromToolInput` for the reading half: no
 * `lane` in the payload (absent, non-string, or blank) keeps the caller's
 * fallback — a stray `focus` alone is not enough to navigate. It deliberately
 * breaks the mirror for the resolution half: a `lane` that *is* present but
 * unrecognised degrades through `resolveLaneFocus` to home's own pane
 * (`dec-092`), never to whatever pane the app happened to be showing.
 */
export function paneFromToolInput(input: unknown, fallback: PaneId): PaneId {
  if (!isRecord(input)) return fallback;
  const args = isRecord(input.arguments) ? input.arguments : input;
  const lane = args.lane;
  if (typeof lane !== "string" || !lane.trim()) return fallback;
  const focus = typeof args.focus === "string" ? args.focus : "";
  return resolveLaneFocus(lane, focus);
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
