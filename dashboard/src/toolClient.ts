import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import type { MetricsWindow, WikiStatus } from "./types";

export interface ToolClient {
  wikiStatus(topic: string): Promise<WikiStatus>;
  metricsRead(topic: string): Promise<MetricsWindow>;
  close(): Promise<void>;
}

/** Standalone client for the dashboard's own stateless streamable-HTTP mount. */
export class HttpToolClient implements ToolClient {
  private readonly client = new Client({ name: "knotica-dashboard", version: "0.1.0" });
  private connected: Promise<void> | undefined;

  constructor(private readonly endpoint: string) {}

  async wikiStatus(topic: string): Promise<WikiStatus> {
    return this.call<WikiStatus>("wiki_status", { topic });
  }

  async metricsRead(topic: string): Promise<MetricsWindow> {
    return this.call<MetricsWindow>("metrics_read", { topic, limit: 100 });
  }

  async close(): Promise<void> {
    await this.client.close();
  }

  private async call<T>(name: string, args: Record<string, unknown>): Promise<T> {
    await this.connect();
    const result = await this.client.callTool({ name, arguments: args });
    const toolResult = "toolResult" in result ? result.toolResult : result;
    if (isRecord(toolResult) && toolResult.isError) {
      throw new Error(String(readResult(toolResult) ?? `${name} failed`));
    }
    const payload =
      isRecord(toolResult) && "structuredContent" in toolResult
        ? toolResult.structuredContent
        : readResult(toolResult);
    if (payload === undefined) {
      throw new Error(`${name} returned no structured content`);
    }
    return payload as T;
  }

  private connect(): Promise<void> {
    this.connected ??= this.client.connect(
      new StreamableHTTPClientTransport(new URL(this.endpoint)),
    );
    return this.connected;
  }
}

/** Reserved for the ext-apps postMessage bridge introduced in M4. */
export class BridgeToolClient implements ToolClient {
  private unavailable(): never {
    throw new Error("M4: the ext-apps bridge is not implemented yet");
  }

  wikiStatus(_topic: string): Promise<WikiStatus> {
    return this.unavailable();
  }

  metricsRead(_topic: string): Promise<MetricsWindow> {
    return this.unavailable();
  }

  async close(): Promise<void> {}
}

function readResult(result: unknown): unknown {
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
