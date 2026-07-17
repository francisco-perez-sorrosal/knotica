import { useEffect, useRef, useState } from "preact/hooks";
import { signal } from "@preact/signals";
import type { App as ExtApp } from "@modelcontextprotocol/ext-apps";
import { applyDocumentTheme } from "@modelcontextprotocol/ext-apps";

import { GoldenPane } from "./GoldenPane";
import { IngestPane } from "./IngestPane";
import { LoopPane } from "./LoopPane";
import { VaultPane } from "./VaultPane";
import {
  BridgeToolClient,
  HttpToolClient,
  preferBridgeMount,
  topicFromToolInput,
  vaultFromToolInput,
  type ToolClient,
} from "./toolClient";
import type { MetricsWindow, PaneId, WikiStatus } from "./types";
import "./app.css";

const query = new URLSearchParams(window.location.search);
const initialTopic = query.get("topic") || "agentic-systems";
const initialVault = query.get("vault") || "";
const paneParam = query.get("pane");
const initialPane = (
  paneParam === "golden" || paneParam === "ingest" || paneParam === "loop"
    ? paneParam
    : "vault"
) as PaneId;
const mcpUrl = query.get("mcp") || "http://127.0.0.1:8765/mcp";

const catalog = signal<WikiStatus | null>(null);
const status = signal<WikiStatus | null>(null);
const metrics = signal<MetricsWindow | null>(null);
const error = signal<string | null>(null);
const updated = signal<Date | null>(null);

function shortenPath(path: string): string {
  if (!path) return "";
  const home = path.startsWith("/Users/")
    ? path.replace(/^\/Users\/[^/]+/, "~")
    : path.replace(/^\/home\/[^/]+/, "~");
  if (home.length <= 48) return home;
  const parts = home.split("/");
  if (parts.length < 4) return `…${home.slice(-44)}`;
  return `${parts[0]}/…/${parts.slice(-2).join("/")}`;
}

export function App() {
  const [topic, setTopic] = useState(initialTopic);
  const [vault, setVault] = useState(initialVault);
  const [pane, setPane] = useState<PaneId>(initialPane);
  const [mount, setMount] = useState<"http" | "bridge" | "connecting">(
    preferBridgeMount() ? "connecting" : "http",
  );
  const [client, setClient] = useState<ToolClient | null>(null);
  const topicRef = useRef(topic);
  const vaultRef = useRef(vault);
  topicRef.current = topic;
  vaultRef.current = vault;

  useEffect(() => {
    let stopped = false;
    let active: ToolClient | undefined;
    let interval: number | undefined;
    let bridgeApp: ExtApp | undefined;

    async function refresh(toolClient: ToolClient) {
      try {
        const [vaultWide, topicScoped, nextMetrics] = await Promise.all([
          toolClient.wikiStatus("", vaultRef.current),
          toolClient.wikiStatus(topicRef.current, vaultRef.current),
          toolClient.metricsRead(topicRef.current, vaultRef.current),
        ]);
        if (!stopped) {
          catalog.value = vaultWide;
          status.value = topicScoped;
          metrics.value = nextMetrics;
          error.value = null;
          updated.value = new Date();
          if (!vaultRef.current && vaultWide.vault_name) {
            setVault(vaultWide.vault_name);
            vaultRef.current = vaultWide.vault_name;
          }
        }
      } catch (cause) {
        if (!stopped) error.value = cause instanceof Error ? cause.message : String(cause);
      }
    }

    function startPolling(toolClient: ToolClient) {
      void refresh(toolClient);
      interval = window.setInterval(() => void refresh(toolClient), 2_000);
    }

    function applyHostTheme(theme: string | undefined) {
      if (theme === "light" || theme === "dark") {
        applyDocumentTheme(theme);
        document.documentElement.dataset.theme = theme;
      }
    }

    async function boot() {
      if (preferBridgeMount()) {
        try {
          const { client: bridge, app } = await BridgeToolClient.connect();
          if (stopped) {
            await bridge.close();
            return;
          }
          active = bridge;
          bridgeApp = app;
          setClient(bridge);
          setMount("bridge");

          app.ontoolinput = (input) => {
            const nextTopic = topicFromToolInput(input, topicRef.current);
            const nextVault = vaultFromToolInput(input, vaultRef.current);
            let changed = false;
            if (nextTopic !== topicRef.current) {
              topicRef.current = nextTopic;
              setTopic(nextTopic);
              changed = true;
            }
            if (nextVault !== vaultRef.current) {
              vaultRef.current = nextVault;
              setVault(nextVault);
              changed = true;
            }
            if (changed) void refresh(bridge);
          };
          applyHostTheme(app.getHostContext()?.theme);
          app.onhostcontextchanged = (ctx) => applyHostTheme(ctx.theme);

          startPolling(bridge);
          return;
        } catch (cause) {
          if (!stopped) {
            const detail = cause instanceof Error ? cause.message : String(cause);
            error.value = `MCP App bridge unavailable (${detail}); trying HTTP…`;
          }
        }
      }

      const http = new HttpToolClient(mcpUrl);
      active = http;
      setClient(http);
      if (!stopped) setMount("http");
      startPolling(http);
    }

    void boot();
    return () => {
      stopped = true;
      if (interval !== undefined) window.clearInterval(interval);
      if (bridgeApp) {
        bridgeApp.ontoolinput = undefined;
        bridgeApp.onhostcontextchanged = undefined;
      }
      void active?.close();
    };
  }, []);

  useEffect(() => {
    if (!client) return;
    void Promise.all([
      client.wikiStatus("", vault),
      client.wikiStatus(topic, vault),
      client.metricsRead(topic, vault),
    ])
      .then(([vaultWide, topicScoped, nextMetrics]) => {
        catalog.value = vaultWide;
        status.value = topicScoped;
        metrics.value = nextMetrics;
        error.value = null;
        updated.value = new Date();
      })
      .catch((cause: unknown) => {
        error.value = cause instanceof Error ? cause.message : String(cause);
      });
  }, [client, topic, vault]);

  const resolvedVaultName =
    vault || catalog.value?.vault_name || status.value?.vault_name || "";
  const vaultName = resolvedVaultName || "…";
  const vaultPath =
    catalog.value?.vault_path ||
    catalog.value?.vault ||
    status.value?.vault_path ||
    status.value?.vault ||
    "";
  const available = catalog.value?.available_vaults ?? [];
  const readyVaults = available.filter((entry) => entry.ready);
  const topics = catalog.value?.topics.map((row) => row.topic) ?? [topic];

  function selectVault(name: string) {
    setVault(name);
    const url = new URL(window.location.href);
    url.searchParams.set("vault", name);
    window.history.replaceState({}, "", url);
  }

  function selectTopic(name: string) {
    setTopic(name);
    const url = new URL(window.location.href);
    url.searchParams.set("topic", name);
    window.history.replaceState({}, "", url);
  }

  function selectPane(next: PaneId) {
    setPane(next);
    const url = new URL(window.location.href);
    if (next === "loop") url.searchParams.delete("pane");
    else url.searchParams.set("pane", next);
    window.history.replaceState({}, "", url);
  }

  return (
    <>
      <header class="app-chrome">
        <div class="brand-block">
          <p class="eyebrow">knotica</p>
          <div class="vault-identity">
            <h1 class="vault-title">{vaultName}</h1>
            {readyVaults.length > 1 ? (
              <label class="vault-picker">
                <span>Switch vault</span>
                <select
                  value={vault || vaultName}
                  onChange={(event) => selectVault((event.target as HTMLSelectElement).value)}
                >
                  {readyVaults.map((entry) => (
                    <option value={entry.name} key={entry.name}>
                      {entry.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <p class="vault-path" title={vaultPath}>
              {shortenPath(vaultPath) || "resolving vault path…"}
            </p>
          </div>
        </div>

        <div class="chrome-controls">
          <label class="topic-picker">
            <span>Topic</span>
            <select
              value={topic}
              onChange={(event) => selectTopic((event.target as HTMLSelectElement).value)}
            >
              {(topics.includes(topic) ? topics : [topic, ...topics]).map((name) => (
                <option value={name} key={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <nav class="pane-tabs" aria-label="Dashboard panes">
            <button
              type="button"
              class={pane === "vault" ? "active" : ""}
              onClick={() => selectPane("vault")}
            >
              Vault
            </button>
            <button
              type="button"
              class={pane === "loop" ? "active" : ""}
              onClick={() => selectPane("loop")}
            >
              Loop
            </button>
            <button
              type="button"
              class={pane === "ingest" ? "active" : ""}
              onClick={() => selectPane("ingest")}
            >
              Ingest
            </button>
            <button
              type="button"
              class={pane === "golden" ? "active" : ""}
              onClick={() => selectPane("golden")}
            >
              Golden
            </button>
          </nav>

          <span class="mount-meta">
            {mount === "connecting"
              ? "connecting…"
              : `${mount} · ${updated.value ? updated.value.toLocaleTimeString() : "waiting…"}`}
          </span>
        </div>
      </header>

      {error.value ? <aside role="alert">MCP read failed: {error.value}</aside> : null}

      {pane === "loop" ? (
        <LoopPane status={status.value} metrics={metrics.value} />
      ) : null}
      {pane === "vault" ? (
        <VaultPane
          client={client}
          catalog={catalog.value}
          status={status.value}
          topic={topic}
          vault={resolvedVaultName}
          onSelectTopic={selectTopic}
        />
      ) : null}
      {pane === "ingest" ? (
        <IngestPane client={client} topic={topic} vault={resolvedVaultName} />
      ) : null}
      {pane === "golden" ? (
        <GoldenPane client={client} topic={topic} vault={resolvedVaultName} />
      ) : null}
    </>
  );
}
