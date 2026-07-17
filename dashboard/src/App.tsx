import { useEffect } from "preact/hooks";
import { signal } from "@preact/signals";

import { LoopPane } from "./LoopPane";
import { HttpToolClient } from "./toolClient";
import type { MetricsWindow, WikiStatus } from "./types";
import "./app.css";

const query = new URLSearchParams(window.location.search);
const topic = query.get("topic") || "agentic-systems";
const mcpUrl = query.get("mcp") || "http://127.0.0.1:8765/mcp";

const status = signal<WikiStatus | null>(null);
const metrics = signal<MetricsWindow | null>(null);
const error = signal<string | null>(null);
const updated = signal<Date | null>(null);

export function App() {
  useEffect(() => {
    const client = new HttpToolClient(mcpUrl);
    let stopped = false;

    async function refresh() {
      try {
        const [nextStatus, nextMetrics] = await Promise.all([
          client.wikiStatus(topic),
          client.metricsRead(topic),
        ]);
        if (!stopped) {
          status.value = nextStatus;
          metrics.value = nextMetrics;
          error.value = null;
          updated.value = new Date();
        }
      } catch (cause) {
        if (!stopped) error.value = cause instanceof Error ? cause.message : String(cause);
      }
    }

    void refresh();
    const interval = window.setInterval(() => void refresh(), 2_000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
      void client.close();
    };
  }, []);

  return (
    <>
      <nav>
        <span>topic / <strong>{topic}</strong></span>
        <span>{updated.value ? `updated ${updated.value.toLocaleTimeString()}` : "connecting…"}</span>
      </nav>
      {error.value ? <aside role="alert">MCP read failed: {error.value}</aside> : null}
      <LoopPane status={status.value} metrics={metrics.value} />
    </>
  );
}
