import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { signal } from "@preact/signals";
import type { App as ExtApp } from "@modelcontextprotocol/ext-apps";
import { applyDocumentTheme } from "@modelcontextprotocol/ext-apps";

import { ArenaPane } from "./ArenaPane";
import { AskPane } from "./AskPane";
import { DatasetsPane } from "./DatasetsPane";
import { IngestPane } from "./IngestPane";
import { ImproveLane } from "./lanes/improve/ImproveLane";
import { TendLane } from "./lanes/tend/TendLane";
import { LoopPane } from "./LoopPane";
import { NotesPane } from "./NotesPane";
import { SourcesPane } from "./SourcesPane";
import { VaultPane } from "./VaultPane";
import {
  BridgeToolClient,
  HttpToolClient,
  preferBridgeMount,
  paneFromToolInput,
  topicFromToolInput,
  vaultFromToolInput,
  type ToolClient,
} from "./toolClient";
import { flywheelLabel, flywheelTone } from "./compileStages";
import { resolveLaneFocus, resolvePane } from "./paneRouting";
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
/**
 * `?lane=`/`?focus=` are the deep link `open_dashboard` hands out; `?pane=`
 * stays a bookmark alias for links minted before the lanes existed. A `?lane=`
 * wins when both are present — it is the newer, explicit form.
 */
const initialLane = query.get("lane") || "";
const initialPane = initialLane
  ? resolveLaneFocus(initialLane, query.get("focus") || "")
  : resolvePane(query.get("pane"));
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
  const [showNewKb, setShowNewKb] = useState(false);
  const [newKbPath, setNewKbPath] = useState("");
  const [newKbName, setNewKbName] = useState("");
  const [newKbTopic, setNewKbTopic] = useState("");
  const [showNewTopic, setShowNewTopic] = useState(false);
  const [newTopicName, setNewTopicName] = useState("");
  const [newTopicBusy, setNewTopicBusy] = useState(false);
  const [newTopicError, setNewTopicError] = useState<string | null>(null);
  const [newKbBusy, setNewKbBusy] = useState(false);
  const [newKbError, setNewKbError] = useState<string | null>(null);
  const [mount, setMount] = useState<"http" | "bridge" | "connecting">(
    preferBridgeMount() ? "connecting" : "http",
  );
  const [client, setClient] = useState<ToolClient | null>(null);
  const topicRef = useRef(topic);
  const vaultRef = useRef(vault);
  const paneRef = useRef(pane);
  const clientRef = useRef<ToolClient | null>(null);
  /** Last-observed ``default_vault``; undefined until the first ``wiki_status`` lands. */
  const lastDefaultRef = useRef<string | undefined>(undefined);
  topicRef.current = topic;
  vaultRef.current = vault;
  paneRef.current = pane;
  clientRef.current = client;

  const resolvedVaultArg = useCallback(
    () =>
      vaultRef.current ||
      catalog.value?.vault_name ||
      status.value?.vault_name ||
      "",
    [],
  );

  const refreshStatus = useCallback(
    async (includeMetrics = true) => {
      const toolClient = clientRef.current;
      if (!toolClient) return;
      const vaultArg = resolvedVaultArg();
      try {
        // Vault-wide status always resolves (no topic) and lists the vault's valid
        // topics — fetch it FIRST so we can reconcile the topic before any
        // topic-scoped read. Switching vaults otherwise leaves a topic from the
        // previous vault, whose topic-scoped reads 404 and break the whole view.
        const vaultWide = await toolClient.wikiStatus("", vaultArg);
        catalog.value = vaultWide;
        if (!vaultRef.current && vaultWide.vault_name) {
          setVault(vaultWide.vault_name);
          vaultRef.current = vaultWide.vault_name;
        }
        const topics = vaultWide.topics.map((row) => row.topic);
        let topicArg = topicRef.current;
        if (topics.length > 0 && !topics.includes(topicArg)) {
          topicArg = topics[0];
          setTopic(topicArg);
          topicRef.current = topicArg;
          const url = new URL(window.location.href);
          url.searchParams.set("topic", topicArg);
          window.history.replaceState({}, "", url);
        }
        if (topics.includes(topicArg)) {
          const [topicScoped, nextMetrics] = await Promise.all([
            toolClient.wikiStatus(topicArg, vaultArg),
            includeMetrics
              ? toolClient.metricsRead(topicArg, vaultArg)
              : Promise.resolve(null),
          ]);
          status.value = topicScoped;
          if (nextMetrics) metrics.value = nextMetrics;
        } else {
          // A vault with no topics yet — nothing topic-scoped to show.
          status.value = null;
          metrics.value = null;
        }
        error.value = null;
        updated.value = new Date();
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : String(cause);
      }
    },
    [resolvedVaultArg],
  );

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
            // The bridge mount has no URL, so `lane`/`focus` arrive here —
            // this is the only channel a host has to deep-link a pane.
            const nextPane = paneFromToolInput(input, paneRef.current);
            let changed = false;
            if (nextPane !== paneRef.current) {
              paneRef.current = nextPane;
              setPane(nextPane);
            }
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
            const detail =
              cause instanceof Error ? cause.message : String(cause);
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

  // Follow the server's ``default_vault`` when it changes externally (e.g. a
  // `/knotica:use` from Claude Desktop/Code) — but never override an explicit
  // initial ``?vault=`` selection on first observation.
  useEffect(() => {
    const nextDefault = catalog.value?.default_vault;
    if (!nextDefault) return;
    if (lastDefaultRef.current === undefined) {
      lastDefaultRef.current = nextDefault;
      return;
    }
    if (nextDefault === lastDefaultRef.current) return;
    lastDefaultRef.current = nextDefault;
    if (nextDefault === vaultRef.current) return;
    setVault(nextDefault);
    const url = new URL(window.location.href);
    url.searchParams.set("vault", nextDefault);
    window.history.replaceState({}, "", url);
  }, [catalog.value?.default_vault]);

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
  const topics = catalog.value?.topics.map((row) => row.topic) ?? [topic];
  const topicRow =
    findTopicRow(status.value, topic) ?? findTopicRow(catalog.value, topic);
  const chipLabel = flywheelLabel({
    compiledPresent: Boolean(topicRow?.compiled?.present),
    compileReady: topicRow?.compile_ready,
    stage: status.value?.compile?.stage,
  });
  const chipTone = flywheelTone(chipLabel);
  const { baseline: baselineScalar, source: baselineSource } =
    resolveTopicBaseline(status.value, metrics.value, topicRow);
  const baselineTone = baselineChipTone(baselineSource);
  const baselinePrefix = baselineChipPrefix(baselineSource);
  const baselineLabel =
    baselineScalar != null ? baselineScalar.toFixed(4) : "—";
  const sourcesPendingCount = topicRow?.suggestions?.pending ?? 0;
  // Open gaps count toward the Sources badge too. A gap you just filed has no
  // suggestion yet — discovery has not run — so counting suggestions alone left
  // the tab bare and the gap unfindable without opening the pane and knowing to
  // look. Both are "something here wants a decision", which is what the badge means.
  const sourcesOpenGapCount = topicRow?.gaps?.open_total ?? 0;
  const sourcesAttentionCount = sourcesPendingCount + sourcesOpenGapCount;
  // Drifted, not total: the badge is an attention signal, matching Sources' pending count.
  // Absent on a server whose wiki_status predates the notes summary — then no badge.
  const notesDriftedCount = topicRow?.notes?.drifted ?? 0;
  const llm = catalog.value?.llm;
  const llmChip: { label: string; tone: "ok" | "warn" | "bad" } | null =
    llm == null
      ? null
      : llm.available && llm.mode === "oauth"
        ? { label: "LLM · OAuth", tone: "ok" }
        : llm.available && llm.mode === "api_key"
          ? { label: "LLM · API key", tone: "warn" }
          : !llm.available && llm.reason === "credentials"
            ? { label: "LLM · no key", tone: "bad" }
            : !llm.available && llm.reason === "deps"
              ? { label: "LLM · deps", tone: "bad" }
              : null;

  async function selectVault(name: string) {
    setVault(name);
    const url = new URL(window.location.href);
    url.searchParams.set("vault", name);
    window.history.replaceState({}, "", url);
    try {
      await clientRef.current?.vaultUse(name);
      await refreshStatus(true);
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    }
  }

  function newKbBasename(path: string): string {
    const trimmed = path.trim().replace(/\/+$/, "");
    const parts = trimmed.split("/");
    return parts[parts.length - 1] || trimmed;
  }

  async function submitNewKb(event: Event) {
    event.preventDefault();
    const path = newKbPath.trim();
    if (!clientRef.current || !path) return;
    setNewKbBusy(true);
    setNewKbError(null);
    try {
      const name = newKbName.trim() || newKbBasename(path);
      await clientRef.current.vaultCreate(name, path, newKbTopic.trim(), true);
      setShowNewKb(false);
      setNewKbPath("");
      setNewKbName("");
      setNewKbTopic("");
      await selectVault(name);
    } catch (cause) {
      setNewKbError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setNewKbBusy(false);
    }
  }

  function selectTopic(name: string) {
    setTopic(name);
    const url = new URL(window.location.href);
    url.searchParams.set("topic", name);
    window.history.replaceState({}, "", url);
  }

  // A knowledge base is normally several topics, but `vault action=create`
  // seeds only the first — so without this the dashboard could start a KB and
  // then not grow it. Refresh before selecting: the picker renders from the
  // status payload, and selecting a topic it has not yet seen shows an entry
  // that vanishes on the next poll.
  async function submitNewTopic(event: Event) {
    event.preventDefault();
    const name = newTopicName.trim();
    if (!clientRef.current || !name) return;
    setNewTopicBusy(true);
    setNewTopicError(null);
    try {
      await clientRef.current.createTopic(name);
      setShowNewTopic(false);
      setNewTopicName("");
      await refreshStatus(true);
      selectTopic(name);
    } catch (cause) {
      setNewTopicError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setNewTopicBusy(false);
    }
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
              {available.length >= 1 ? (
                <label class="vault-picker vault-picker-inline">
                  <span class="sr-only">Switch vault</span>
                  <select
                    value={vault || vaultName}
                    onChange={(event) =>
                      void selectVault(
                        (event.target as HTMLSelectElement).value,
                      )
                    }
                    aria-label="Switch vault"
                  >
                    {available.map((entry) => (
                      <option value={entry.name} key={entry.name}>
                        {entry.name}
                        {entry.name === catalog.value?.default_vault
                          ? " (active)"
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <button
                type="button"
                class="toggle"
                onClick={() => {
                  setShowNewKb((prev) => !prev);
                  setNewKbError(null);
                }}
              >
                ＋ New KB
              </button>
            </div>
            <p class="vault-path" title={vaultPath}>
              <ObsidianLink href={vaultOpenUri} className="vault-path-link">
                {shortenPath(vaultPath) || "resolving vault path…"}
              </ObsidianLink>
            </p>
            {showNewKb ? (
              <form
                class="doctor-repair-toolbar"
                onSubmit={(event) => void submitNewKb(event)}
              >
                <label class="heal-inline-field">
                  <span>path</span>
                  <input
                    type="text"
                    required
                    value={newKbPath}
                    placeholder="/path/to/vault"
                    onInput={(event) =>
                      setNewKbPath((event.target as HTMLInputElement).value)
                    }
                  />
                </label>
                <label class="heal-inline-field">
                  <span>name</span>
                  <input
                    type="text"
                    value={newKbName}
                    placeholder={newKbBasename(newKbPath) || "vault name"}
                    onInput={(event) =>
                      setNewKbName((event.target as HTMLInputElement).value)
                    }
                  />
                </label>
                <label class="heal-inline-field">
                  <span>topic</span>
                  <input
                    type="text"
                    value={newKbTopic}
                    placeholder="optional"
                    onInput={(event) =>
                      setNewKbTopic((event.target as HTMLInputElement).value)
                    }
                  />
                </label>
                <button
                  type="submit"
                  class="primary"
                  disabled={newKbBusy || !newKbPath.trim()}
                >
                  {newKbBusy ? "Creating…" : "Create"}
                </button>
                <button
                  type="button"
                  class="toggle"
                  onClick={() => {
                    setShowNewKb(false);
                    setNewKbError(null);
                  }}
                >
                  Cancel
                </button>
                {newKbError ? <p class="tone-bad">{newKbError}</p> : null}
              </form>
            ) : null}
          </div>
        </div>

        <div class="app-chrome-band">
          <div class="chrome-controls">
            <label class="topic-picker topic-picker-inline">
              <span class="sr-only">Topic</span>
              <select
                value={topic}
                onChange={(event) =>
                  selectTopic((event.target as HTMLSelectElement).value)
                }
                aria-label="Topic"
              >
                {(topics.includes(topic) ? topics : [topic, ...topics]).map(
                  (name) => (
                    <option value={name} key={name}>
                      {name}
                    </option>
                  ),
                )}
              </select>
            </label>

            <button
              type="button"
              class="toggle"
              onClick={() => {
                setShowNewTopic((prev) => !prev);
                setNewTopicError(null);
              }}
            >
              ＋ New topic
            </button>

            {showNewTopic ? (
              <form
                class="doctor-repair-toolbar"
                onSubmit={(event) => void submitNewTopic(event)}
              >
                <label class="heal-inline-field">
                  <span>topic</span>
                  <input
                    type="text"
                    required
                    value={newTopicName}
                    placeholder="pretraining"
                    onInput={(event) =>
                      setNewTopicName((event.target as HTMLInputElement).value)
                    }
                  />
                </label>
                <button
                  type="submit"
                  class="primary"
                  disabled={newTopicBusy || !newTopicName.trim()}
                >
                  {newTopicBusy ? "Creating…" : "Create"}
                </button>
                <button
                  type="button"
                  class="toggle"
                  onClick={() => {
                    setShowNewTopic(false);
                    setNewTopicError(null);
                  }}
                >
                  Cancel
                </button>
                {newTopicError ? <p class="tone-bad">{newTopicError}</p> : null}
              </form>
            ) : null}

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
                class={pane === "sources" ? "active" : ""}
                onClick={() => selectPane("sources")}
              >
                Sources
                {sourcesAttentionCount > 0 ? (
                  <span
                    class="pane-tab-badge"
                    title={`${sourcesPendingCount} suggestion(s) awaiting review · ${sourcesOpenGapCount} open gap(s) awaiting discovery`}
                  >
                    {sourcesAttentionCount}
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                class={pane === "notes" ? "active" : ""}
                onClick={() => selectPane("notes")}
              >
                Notes
                {notesDriftedCount > 0 ? (
                  <span
                    class="pane-tab-badge"
                    title="Notes whose anchors drifted"
                  >
                    {notesDriftedCount}
                  </span>
                ) : null}
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
              <button
                type="button"
                class={pane === "improve" ? "active" : ""}
                onClick={() => selectPane("improve")}
              >
                Improve
              </button>
              <button
                type="button"
                class={pane === "tend" ? "active" : ""}
                onClick={() => selectPane("tend")}
              >
                Tend
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

              {llmChip ? (
                <span
                  class={`llm-chip health-chip ${llmChip.tone}`}
                  title="Server-side LLM powers Ask/query, Compile, Loop/Arena, and live eval. OAuth = CLAUDE_CODE_OAUTH_TOKEN (subscription, no metered spend); API key = ANTHROPIC_API_KEY (metered)."
                >
                  {llmChip.label}
                </span>
              ) : null}

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
                Credentials found but eval dependencies are missing. Restart the
                server with: <code>uv run --extra evals knotica mcp …</code>
              </>
            ) : (
              <>
                Ask, Arena, Compile and live evals need credentials. Set{" "}
                <code>CLAUDE_CODE_OAUTH_TOKEN</code> (preferred) or{" "}
                <code>ANTHROPIC_API_KEY</code> in the server environment.
              </>
            )}
          </span>
          <button
            type="button"
            onClick={() => (llmBannerDismissed.value = true)}
          >
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
      {pane === "sources" ? (
        <SourcesPane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          status={status.value}
          onStatusRefresh={() => refreshStatus(false)}
        />
      ) : null}
      {pane === "notes" ? (
        <NotesPane client={client} topic={topic} vault={resolvedVaultName} />
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
        <IngestPane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          obsidianCtx={obsidianCtx}
        />
      ) : null}
      {pane === "datasets" || pane === "golden" ? (
        <DatasetsPane client={client} topic={topic} vault={resolvedVaultName} />
      ) : null}
      {pane === "improve" ? (
        <ImproveLane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          status={status.value}
          metrics={metrics.value}
          obsidianCtx={obsidianCtx}
          onStatusRefresh={() => refreshStatus(true)}
        />
      ) : null}
      {pane === "tend" ? (
        <TendLane
          client={client}
          vault={resolvedVaultName}
          topic={topic}
          obsidianCtx={obsidianCtx}
        />
      ) : null}
    </>
  );
}
