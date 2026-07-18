import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { signal } from "@preact/signals";
import type { App as ExtApp } from "@modelcontextprotocol/ext-apps";
import { applyDocumentTheme } from "@modelcontextprotocol/ext-apps";

import { ArenaPane } from "./ArenaPane";
import { AskPane } from "./AskPane";
import { DatasetsPane } from "./DatasetsPane";
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
import { flywheelLabel, flywheelTone } from "./compileStages";
import {
  ObsidianLink,
  obsidianOpenVaultFromContext,
  type ObsidianContext,
} from "./obsidianLinks";
import {
  baselineChipPrefix,
  baselineChipTitle,
  baselineChipTone,
  findTopicRow,
  resolveTopicBaseline,
} from "./topicHelpers";
import type { MetricsWindow, PaneId, WikiStatus } from "./types";
import "./app.css";

const query = new URLSearchParams(window.location.search);
const initialTopic = query.get("topic") || "agentic-systems";
const initialVault = query.get("vault") || "";
const paneParam = query.get("pane");
const initialPane = (
  paneParam === "datasets" ||
  paneParam === "golden" ||
  paneParam === "ingest" ||
  paneParam === "loop" ||
  paneParam === "ask" ||
  paneParam === "arena"
    ? paneParam === "golden"
      ? "datasets"
      : paneParam
    : "vault"
) as PaneId;
const mcpUrl = query.get("mcp") || "http://127.0.0.1:8765/mcp";

const catalog = signal<WikiStatus | null>(null);
const status = signal<WikiStatus | null>(null);
const metrics = signal<MetricsWindow | null>(null);
const error = signal<string | null>(null);
const updated = signal<Date | null>(null);
/** Dismissed once per session — reset on full page reload. */
const llmBannerDismissed = signal(false);

const TRANSPORT_ERROR_HINT = /fetch|mcp|connect/i;

function errorRemediationHint(message: string): string | null {
  if (!TRANSPORT_ERROR_HINT.test(message)) return null;
  return "Is the knotica server running? Start it with: knotica mcp --http --port 8765";
}

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
  const clientRef = useRef<ToolClient | null>(null);
  topicRef.current = topic;
  vaultRef.current = vault;
  clientRef.current = client;

  const resolvedVaultArg = useCallback(
    () =>
      vaultRef.current ||
      catalog.value?.vault_name ||
      status.value?.vault_name ||
      "",
    [],
  );

  const refreshStatus = useCallback(async (includeMetrics = true) => {
    const toolClient = clientRef.current;
    if (!toolClient) return;
    const vaultArg = resolvedVaultArg();
    const topicArg = topicRef.current;
    try {
      const [vaultWide, topicScoped, nextMetrics] = await Promise.all([
        toolClient.wikiStatus("", vaultArg),
        toolClient.wikiStatus(topicArg, vaultArg),
        includeMetrics ? toolClient.metricsRead(topicArg, vaultArg) : Promise.resolve(null),
      ]);
      catalog.value = vaultWide;
      status.value = topicScoped;
      if (nextMetrics) metrics.value = nextMetrics;
      error.value = null;
      updated.value = new Date();
      if (!vaultRef.current && vaultWide.vault_name) {
        setVault(vaultWide.vault_name);
        vaultRef.current = vaultWide.vault_name;
      }
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    }
  }, [resolvedVaultArg]);

  const refreshStatusRef = useRef(refreshStatus);
  refreshStatusRef.current = refreshStatus;

  useEffect(() => {
    let stopped = false;
    let active: ToolClient | undefined;
    let interval: number | undefined;
    let bridgeApp: ExtApp | undefined;

    async function refresh() {
      if (stopped) return;
      await refreshStatusRef.current(true);
    }

    function startPolling() {
      void refresh();
      interval = window.setInterval(() => void refresh(), 2_000);
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
          clientRef.current = bridge;
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
            if (changed) void refresh();
          };
          applyHostTheme(app.getHostContext()?.theme);
          app.onhostcontextchanged = (ctx) => applyHostTheme(ctx.theme);

          startPolling();
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
      clientRef.current = http;
      setClient(http);
      if (!stopped) setMount("http");
      startPolling();
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
    void refreshStatus(true);
  }, [client, topic, vault, refreshStatus]);

  const resolvedVaultName =
    vault || catalog.value?.vault_name || status.value?.vault_name || "";
  const vaultName = resolvedVaultName || "…";
  const vaultPath =
    catalog.value?.vault_path ||
    catalog.value?.vault ||
    status.value?.vault_path ||
    status.value?.vault ||
    "";
  const obsidianCtx: ObsidianContext = {
    vaultPath: vaultPath || undefined,
    vaultName: resolvedVaultName || undefined,
  };
  const vaultOpenUri = obsidianOpenVaultFromContext(obsidianCtx);
  const available = catalog.value?.available_vaults ?? [];
  const readyVaults = available.filter((entry) => entry.ready);
  const topics = catalog.value?.topics.map((row) => row.topic) ?? [topic];
  const topicRow = findTopicRow(status.value, topic) ?? findTopicRow(catalog.value, topic);
  const chipLabel = flywheelLabel({
    compiledPresent: Boolean(topicRow?.compiled?.present),
    compileReady: topicRow?.compile_ready,
    stage: status.value?.compile?.stage,
  });
  const chipTone = flywheelTone(chipLabel);
  const { baseline: baselineScalar, source: baselineSource } = resolveTopicBaseline(
    status.value,
    metrics.value,
    topicRow,
  );
  const baselineTone = baselineChipTone(baselineSource);
  const baselinePrefix = baselineChipPrefix(baselineSource);
  const baselineLabel =
    baselineScalar != null ? baselineScalar.toFixed(4) : "—";

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
    if (next === "vault") url.searchParams.delete("pane");
    else url.searchParams.set("pane", next);
    window.history.replaceState({}, "", url);
  }

  return (
    <>
      <header class="app-chrome">
        <div class="app-chrome-top">
          <div class="brand-block">
            <div class="brand-row">
              <span class="eyebrow">knotica</span>
              <span class="brand-sep" aria-hidden="true">
                ·
              </span>
              <h1 class="vault-title">
                <ObsidianLink href={vaultOpenUri} className="vault-title-link">
                  {vaultName}
                </ObsidianLink>
              </h1>
              {readyVaults.length > 1 ? (
                <label class="vault-picker vault-picker-inline">
                  <span class="sr-only">Switch vault</span>
                  <select
                    value={vault || vaultName}
                    onChange={(event) => selectVault((event.target as HTMLSelectElement).value)}
                    aria-label="Switch vault"
                  >
                    {readyVaults.map((entry) => (
                      <option value={entry.name} key={entry.name}>
                        {entry.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
            <p class="vault-path" title={vaultPath}>
              <ObsidianLink href={vaultOpenUri} className="vault-path-link">
                {shortenPath(vaultPath) || "resolving vault path…"}
              </ObsidianLink>
            </p>
          </div>
        </div>

        <div class="app-chrome-band">
          <div class="chrome-controls">
            <label class="topic-picker topic-picker-inline">
              <span class="sr-only">Topic</span>
              <select
                value={topic}
                onChange={(event) => selectTopic((event.target as HTMLSelectElement).value)}
                aria-label="Topic"
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
                class={pane === "ask" ? "active" : ""}
                onClick={() => selectPane("ask")}
              >
                Ask
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
                class={pane === "arena" ? "active" : ""}
                onClick={() => selectPane("arena")}
              >
                Arena
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
                class={pane === "datasets" || pane === "golden" ? "active" : ""}
                onClick={() => selectPane("datasets")}
              >
                Datasets
              </button>
            </nav>

            <div class="chrome-status">
              <span
                class={`flywheel-chip health-chip ${chipTone}`}
                title="Compile flywheel status for the selected topic"
              >
                {chipLabel}
              </span>

              <span
                class={`baseline-chip health-chip ${baselineTone}`}
                title={baselineChipTitle(topic, baselineSource)}
              >
                {baselinePrefix} · {baselineLabel}
                <span class="baseline-chip-topic"> · {topic}</span>
              </span>

              <span class="mount-meta">
                {mount === "connecting"
                  ? "connecting…"
                  : `${mount} · ${updated.value ? updated.value.toLocaleTimeString() : "waiting…"}`}
              </span>
            </div>
          </div>
        </div>
      </header>

      {catalog.value?.llm?.available === false && !llmBannerDismissed.value ? (
        <aside class="loop-banner tone-argue llm-banner">
          <strong>Headless LLM off</strong>
          <span>
            {catalog.value.llm.reason === "deps" ? (
              <>
                Credentials found but eval dependencies are missing. Restart the server with:{" "}
                <code>uv run --group evals knotica mcp …</code>
              </>
            ) : (
              <>
                Ask, Arena, Compile and live evals need credentials. Set{" "}
                <code>CLAUDE_CODE_OAUTH_TOKEN</code> (preferred) or <code>ANTHROPIC_API_KEY</code>{" "}
                in the server environment.
              </>
            )}
          </span>
          <button type="button" onClick={() => (llmBannerDismissed.value = true)}>
            Dismiss
          </button>
        </aside>
      ) : null}

      {error.value ? (
        <aside role="alert">
          <p>MCP read failed: {error.value}</p>
          {errorRemediationHint(error.value) ? (
            <p class="muted">{errorRemediationHint(error.value)}</p>
          ) : null}
        </aside>
      ) : null}

      {pane === "vault" ? (
        <VaultPane
          client={client}
          catalog={catalog.value}
          status={status.value}
          topic={topic}
          vault={resolvedVaultName}
          obsidianCtx={obsidianCtx}
          onSelectTopic={selectTopic}
          onStatusRefresh={() => refreshStatus(false)}
        />
      ) : null}
      {pane === "ask" ? (
        <AskPane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          obsidianCtx={obsidianCtx}
          status={status.value}
          onOpenLoop={() => selectPane("loop")}
          onOpenArena={() => selectPane("arena")}
        />
      ) : null}
      {pane === "loop" ? (
        <LoopPane
          status={status.value}
          metrics={metrics.value}
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          obsidianCtx={obsidianCtx}
          onOpenArena={() => selectPane("arena")}
          onOpenAsk={() => selectPane("ask")}
          onOpenVault={() => selectPane("vault")}
          onStatusRefresh={() => refreshStatus(true)}
        />
      ) : null}
      {pane === "arena" ? (
        <ArenaPane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          status={status.value}
          onOpenAsk={() => selectPane("ask")}
          onOpenLoop={() => selectPane("loop")}
        />
      ) : null}
      {pane === "ingest" ? (
        <IngestPane client={client} topic={topic} vault={resolvedVaultName} obsidianCtx={obsidianCtx} />
      ) : null}
      {pane === "datasets" || pane === "golden" ? (
        <DatasetsPane client={client} topic={topic} vault={resolvedVaultName} />
      ) : null}
    </>
  );
}
