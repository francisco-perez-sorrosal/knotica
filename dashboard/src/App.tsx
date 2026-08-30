import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { signal } from "@preact/signals";
import type { App as ExtApp } from "@modelcontextprotocol/ext-apps";
import { applyDocumentTheme } from "@modelcontextprotocol/ext-apps";

import { CreateDrawer } from "./CreateDrawer";
import { ProcessBrief } from "./lanes/ProcessBrief";
import { ProcessOutcome } from "./lanes/ProcessOutcome";
import { publishOpenAnchor } from "./lanes/laneNavigation";
import type { ProcessId } from "./lanes/processMeta";
import { AnswerLane } from "./lanes/answer/AnswerLane";
import { FillLane } from "./lanes/fill/FillLane";
import { HomeLane } from "./lanes/home/HomeLane";
import { ImproveLane } from "./lanes/improve/ImproveLane";
import { LearnLane } from "./lanes/learn/LearnLane";
import { TendLane } from "./lanes/tend/TendLane";
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
import { Icon } from "./icons";
import { InfoPopover } from "./InfoPopover";
import { DEFAULT_PANE, resolveAnchor, resolvePane } from "./paneRouting";
import type { LaneAnchor } from "./paneRouting";
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
/* `?focus=` used to be accepted and then thrown away. It now seeds the same
   one-shot arrival a Home queue row or a registry `NEXT STEP` produces, so the
   three entry points into a stage — attention row, deep link, `open_dashboard`
   argument — land identically. */
const initialAnchor: LaneAnchor = initialLane
  ? resolveAnchor(initialLane, query.get("focus") || "")
  : { lane: resolvePane(query.get("pane")), stage: null };
const initialPane = initialAnchor.lane;
/**
 * The HTTP mount is normally served by the same process that answers `/mcp`,
 * so same-origin is the honest default — a hardcoded port polls a *different*
 * server whenever `--port` isn't 8765, and the stale answers it gets are
 * indistinguishable from live ones. The fixed fallback remains only for
 * non-http contexts (e.g. a file:// open of the built artifact).
 */
const mcpUrl =
  query.get("mcp") ||
  (window.location.protocol === "http:" || window.location.protocol === "https:"
    ? new URL("/mcp", window.location.origin).toString()
    : "http://127.0.0.1:8765/mcp");

const catalog = signal<WikiStatus | null>(null);
const status = signal<WikiStatus | null>(null);
const metrics = signal<MetricsWindow | null>(null);
const error = signal<string | null>(null);
const updated = signal<Date | null>(null);
/** Dismissed once per session — reset on full page reload. */
const llmBannerDismissed = signal(false);

const TRANSPORT_ERROR_HINT = /fetch|mcp|connect/i;

/** How long an arrived-at row keeps its border tint. Long enough to find, short
 *  enough not to become a second, competing "current" marker. */
const ARRIVAL_TINT_MS = 1_600;

function prefersReducedMotion(): boolean {
  return Boolean(
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches,
  );
}

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
  /* The one-shot arrival. Non-null for exactly one render after an anchor is
     followed: the target lane reads it (Improve seeds its stage focus from it,
     every railed lane gets its row scrolled into view and tinted), then the
     effect below clears it. A request that survived would re-seed focus on the
     next topic change, which is focus theft with a delay. */
  const [arrival, setArrival] = useState<LaneAnchor | null>(
    initialAnchor.stage ? initialAnchor : null,
  );
  const [showCreateDrawer, setShowCreateDrawer] = useState(false);
  /* What the last chrome process did. It is held here rather than in
     `CreateDrawer` because both of that drawer's forms close on success --
     an outcome parked inside it would be unmounted by the very click that
     produced it. Superseded by the next chrome action, never cleared on a
     timer. */
  const [chromeOutcome, setChromeOutcome] = useState<ProcessId | null>(null);
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
  const fillPendingCount = topicRow?.suggestions?.pending ?? 0;
  // Open gaps count toward the Fill badge too. A gap you just filed has no
  // suggestion yet — discovery has not run — so counting suggestions alone left
  // the tab bare and the gap unfindable without opening the lane and knowing to
  // look. Both are "something here wants a decision", which is what the badge means.
  const fillOpenGapCount = topicRow?.gaps?.open_total ?? 0;
  const fillAttentionCount = fillPendingCount + fillOpenGapCount;
  // Drifted, not total: the badge is an attention signal, matching Fill's pending count.
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

  async function selectVault(name: string, process: ProcessId = "vault.use") {
    setVault(name);
    const url = new URL(window.location.href);
    url.searchParams.set("vault", name);
    window.history.replaceState({}, "", url);
    try {
      await clientRef.current?.vaultUse(name);
      await refreshStatus(true);
      setChromeOutcome(process);
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause);
    }
  }

  function selectTopic(name: string, process?: ProcessId) {
    setTopic(name);
    if (process) setChromeOutcome(process);
    const url = new URL(window.location.href);
    url.searchParams.set("topic", name);
    window.history.replaceState({}, "", url);
  }

  function selectPane(next: PaneId) {
    setPane(next);
    const url = new URL(window.location.href);
    // The default pane is the bare URL — no `?pane=` to strip off later.
    if (next === DEFAULT_PANE) url.searchParams.delete("pane");
    else url.searchParams.set("pane", next);
    // A tab click is a fresh destination, so any anchor coordinate left over
    // from a followed `NEXT STEP` is stale: keeping it would make a reload land
    // somewhere the user did not last choose.
    url.searchParams.delete("lane");
    url.searchParams.delete("focus");
    window.history.replaceState({}, "", url);
  }

  /**
   * The single cross-lane navigation callback (`dec-092`/M4 sharpened). It does
   * three things and nothing else: sets the pane, records the destination in the
   * URL so the landing is shareable and survives a reload, and publishes the
   * one-shot arrival the target lane consumes.
   *
   * Every caller passes a `(lane, stage)` pair that came out of a registry —
   * `PROCESS_META`'s `next` anchors or `ATTENTION_KIND_META`'s row anchors —
   * both census-validated against `LANE_STAGES`, so this cannot be handed a
   * destination the process model does not declare.
   */
  const openAnchor = useCallback((lane: PaneId, stage?: string | null) => {
    setPane(lane);
    const url = new URL(window.location.href);
    url.searchParams.delete("pane");
    url.searchParams.set("lane", lane);
    if (stage) url.searchParams.set("focus", stage);
    else url.searchParams.delete("focus");
    window.history.replaceState({}, "", url);
    setArrival({ lane, stage: stage ?? null });
  }, []);

  useEffect(() => {
    publishOpenAnchor(openAnchor);
    return () => publishOpenAnchor(null);
  }, [openAnchor]);

  /**
   * Arrival, for every lane that is not Improve: scroll the row into view and
   * tint its border for a moment. **Focus is not moved** — a scroll-and-tint
   * orients without hijacking the keyboard, and moving focus on arrival is the
   * same theft the rail contract forbids. The tint is decoration; the position
   * is the carrier, so a reduced-motion user loses nothing.
   *
   * Runs after the target lane's own render, which is what guarantees the row
   * exists — including the case where Improve's focus seeding is what mounted
   * the stage body in the first place.
   */
  useEffect(() => {
    if (!arrival) return;
    const target = arrival.stage
      ? document.querySelector<HTMLElement>(
          `[data-anchor="${arrival.lane}:${arrival.stage}"]`,
        )
      : null;
    setArrival(null);
    if (!target) return;
    target.scrollIntoView?.({
      block: "nearest",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
    target.dataset.anchorArrived = "true";
    const timer = window.setTimeout(() => {
      delete target.dataset.anchorArrived;
    }, ARRIVAL_TINT_MS);
    return () => window.clearTimeout(timer);
  }, [arrival]);

  return (
    <>
      <header class="app-chrome">
        <div class="app-chrome-top">
          <div class="brand-block">
            <div class="brand-row">
              <span class="brand-mark" aria-hidden="true">
                ◈
              </span>
              <span class="eyebrow">knotica</span>
              <span class="brand-sep" aria-hidden="true">
                ·
              </span>
              {available.length >= 1 ? (
                <>
                  {/* Sibling of the picker, never inside its `<label>`: a
                      button inside a label activates the labelled control. */}
                  <ProcessBrief process="vault.use" term="why switch" />
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
                </>
              ) : (
                <h1 class="vault-title">
                  <ObsidianLink href={vaultOpenUri} className="vault-title-link">
                    {vaultName}
                  </ObsidianLink>
                </h1>
              )}
              <span class="brand-sep" aria-hidden="true">
                ›
              </span>
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
                class="chrome-create-trigger"
                aria-expanded={showCreateDrawer}
                aria-controls="chrome-create-drawer"
                aria-label="Create a knowledge base or topic"
                onClick={() => setShowCreateDrawer((prev) => !prev)}
              >
                <Icon name="plus" size={16} />
              </button>
            </div>
          </div>

          <div class="chrome-status">
            {llmChip ? (
              <span class="chrome-chip">
                <span class={`llm-chip health-chip ${llmChip.tone}`}>
                  {llmChip.label}
                </span>
                <InfoPopover
                  id="chrome:llm"
                  title="Server LLM"
                  ariaLabel="About the server LLM status"
                  align="end"
                  whatThisIs="Server-side LLM powers Ask/query, Compile, Loop/Arena, and live eval."
                  whatToDoNext="Set CLAUDE_CODE_OAUTH_TOKEN (preferred -- subscription, no metered spend) or ANTHROPIC_API_KEY (metered) in the server environment."
                />
              </span>
            ) : null}

            <span class="chrome-chip">
              <span class={`baseline-chip health-chip ${baselineTone}`}>
                {baselinePrefix} · {baselineLabel}
                <span class="baseline-chip-topic"> · {topic}</span>
              </span>
              <InfoPopover
                id="chrome:baseline"
                title="Gate baseline"
                ariaLabel="About the gate baseline"
                align="end"
                whatThisIs={baselineChipTitle(topic, baselineSource)}
                whatToDoNext="Freeze a baseline from Improve once a scalar you trust is in hand."
              />
            </span>

            <span class="chrome-chip">
              <span class={`flywheel-chip health-chip ${chipTone}`}>
                {chipLabel}
              </span>
              <InfoPopover
                id="chrome:flywheel"
                title="Compile flywheel"
                ariaLabel="About compile flywheel status"
                align="end"
                whatThisIs="Tracks whether the selected topic has curated enough training data to compile a DSPy program, and whether that program is compiled."
                whatTheStatesMean={
                  <ul>
                    <li>
                      <strong>Curating</strong> -- still gathering training
                      examples.
                    </li>
                    <li>
                      <strong>Ready</strong> -- enough examples to compile.
                    </li>
                    <li>
                      <strong>Compiling</strong> -- a compile run is in
                      progress.
                    </li>
                    <li>
                      <strong>Compiled</strong> -- a program exists for this
                      topic.
                    </li>
                  </ul>
                }
                whatToDoNext="Curate more pages in Learn, or open Improve to compile once ready."
              />
            </span>

            <span class="mount-meta">
              {mount === "connecting"
                ? "connecting…"
                : `${mount} · ${updated.value ? updated.value.toLocaleTimeString() : "waiting…"}`}
            </span>
          </div>
        </div>

        <CreateDrawer
          open={showCreateDrawer}
          client={client}
          onClose={() => setShowCreateDrawer(false)}
          onCreatedKb={(name) => selectVault(name, "vault.create")}
          onCreatedTopic={(name) => selectTopic(name, "learn.create_topic")}
          onRefreshStatus={refreshStatus}
        />

        {/* The chrome's own outcome line. All three of its processes replace
            what the rest of the screen means -- a switched vault invalidates
            every number, a created topic or KB is a surface with nothing in
            it yet -- so each one names where to go and why. */}
        {chromeOutcome ? <ProcessOutcome process={chromeOutcome} /> : null}

        <div class="app-chrome-band">
          <div class="chrome-controls">
            <nav class="pane-tabs" aria-label="Dashboard panes">
              <button
                type="button"
                class={pane === "home" ? "active" : ""}
                onClick={() => selectPane("home")}
              >
                <Icon name="lane:home" size={16} />
                <span class="pane-tab-label">Home</span>
              </button>
              <button
                type="button"
                class={pane === "learn" ? "active" : ""}
                onClick={() => selectPane("learn")}
              >
                <Icon name="lane:learn" size={16} />
                <span class="pane-tab-label">Learn</span>
              </button>
              <button
                type="button"
                class={pane === "answer" ? "active" : ""}
                onClick={() => selectPane("answer")}
              >
                <Icon name="lane:answer" size={16} />
                <span class="pane-tab-label">Answer</span>
              </button>
              <button
                type="button"
                class={pane === "improve" ? "active" : ""}
                onClick={() => selectPane("improve")}
              >
                <Icon name="lane:improve" size={16} />
                <span class="pane-tab-label">Improve</span>
              </button>
              <button
                type="button"
                class={pane === "fill" ? "active" : ""}
                onClick={() => selectPane("fill")}
              >
                <Icon name="lane:fill" size={16} />
                <span class="pane-tab-label">Fill</span>
                {fillAttentionCount > 0 ? (
                  <span
                    class="pane-tab-badge"
                    title={`${fillPendingCount} suggestion(s) awaiting review · ${fillOpenGapCount} open gap(s) awaiting discovery`}
                  >
                    {fillAttentionCount}
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                class={pane === "tend" ? "active" : ""}
                onClick={() => selectPane("tend")}
              >
                <Icon name="lane:tend" size={16} />
                <span class="pane-tab-label">Tend</span>
                {notesDriftedCount > 0 ? (
                  <span
                    class="pane-tab-badge"
                    title="Notes whose anchors drifted"
                  >
                    {notesDriftedCount}
                  </span>
                ) : null}
              </button>
            </nav>

            <p class="vault-path" title={vaultPath}>
              <ObsidianLink href={vaultOpenUri} className="vault-path-link">
                {shortenPath(vaultPath) || "resolving vault path…"}
              </ObsidianLink>
              <Icon name="external-link" size={16} />
            </p>
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

      {pane === "home" ? (
        // Home owns its own cross-topic `view="attention"` read, so it takes
        // no `status`/`topic` from the app poll — only the client, the vault
        // it reads across, and the one navigation callback. That callback is
        // no longer Home's alone: it is the single `openAnchor` every lane may
        // hold, and Home's privilege is now that it *routes on nothing else*.
        <HomeLane
          client={client}
          vault={resolvedVaultName}
          onOpenAnchor={openAnchor}
        />
      ) : null}
      {pane === "improve" ? (
        <ImproveLane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          arrivalStage={arrival?.lane === "improve" ? arrival.stage : null}
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
      {pane === "learn" ? (
        <LearnLane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          obsidianCtx={obsidianCtx}
        />
      ) : null}
      {pane === "answer" ? (
        <AnswerLane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          obsidianCtx={obsidianCtx}
          status={status.value}
        />
      ) : null}
      {pane === "fill" ? (
        <FillLane
          client={client}
          topic={topic}
          vault={resolvedVaultName}
          status={status.value}
          onStatusRefresh={() => refreshStatus(false)}
        />
      ) : null}
    </>
  );
}
