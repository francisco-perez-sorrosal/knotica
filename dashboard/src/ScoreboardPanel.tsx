import { useEffect, useMemo, useState } from "preact/hooks";

import { DeletePreviewBanner } from "./DeletePreview";
import { formatDeleteApplied } from "./deleteHelpers";
import { PromotePreviewBanner } from "./PromotePreview";
import { formatPromoteApplied } from "./promoteHelpers";
import { PromptDiff } from "./PromptDiff";
import type { ToolClient } from "./toolClient";
import { findTopicRow } from "./topicHelpers";
import type {
  BranchDeleteResult,
  BranchScoreboard,
  CompilePromoteResult,
  ScoreboardEntry,
  WikiStatus,
} from "./types";

const LOOP_KIND_LABEL: Record<"loop_candidate" | "loop_result", string> = {
  loop_candidate: "loop/c",
  loop_result: "loop/r",
};

export function ScoreboardPanel({
  client,
  topic,
  vault,
  status,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}) {
  const [board, setBoard] = useState<BranchScoreboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [deletePreviewBusy, setDeletePreviewBusy] = useState(false);
  const [deleteApplyBusy, setDeleteApplyBusy] = useState(false);
  const [promotePreview, setPromotePreview] = useState<{
    branch: string;
    kind: "compile" | "loop";
    result: CompilePromoteResult;
  } | null>(null);
  const [deletePreview, setDeletePreview] = useState<{
    branch: string;
    result: BranchDeleteResult;
  } | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState<string | null>(null);

  async function loadBoard() {
    if (!client || !topic) return null;
    const next = await client.branchScoreboard(topic, vault);
    setBoard(next);
    setError(null);
    return next;
  }

  useEffect(() => {
    if (!client || !topic) return;
    let cancelled = false;
    const load = async () => {
      try {
        const next = await client.branchScoreboard(topic, vault);
        if (!cancelled) {
          setBoard(next);
          setError(null);
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      }
    };
    void load();
    const id = window.setInterval(() => void load(), 4_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [client, topic, vault]);

  const baselineMeta = board?.baseline_meta;
  const baseline = board?.baseline ?? status?.gate.baseline ?? null;
  const lastMetricsScalar =
    baselineMeta?.last_metrics_scalar ?? status?.gate.last_scalar ?? null;
  const gateState = status?.gate.state ?? "unknown";
  const gateFrozen = baselineMeta?.frozen ?? status?.loop.baseline_frozen ?? false;

  const openCompile = useMemo(
    () => board?.entries.find((row) => row.kind === "compile" && row.slot === "open") ?? null,
    [board],
  );
  const compileHistory = useMemo(
    () =>
      board?.entries.filter(
        (row) =>
          row.kind === "compile" && (row.slot === "history" || row.slot === "archived"),
      ) ?? [],
    [board],
  );
  // Live candidates awaiting a gate decision — the loop/c/* branches proper.
  const liveLoopRows = useMemo(
    () => board?.entries.filter((row) => row.kind === "loop_candidate") ?? [],
    [board],
  );
  // loop/r/* result branches are merge_branch()'d onto default the instant they're
  // created (see LoopRunner.observe_default/_keep) — every tip the scoreboard can
  // still see is already-merged observation history, auto-pruned to the newest 5.
  const observationHistoryRows = useMemo(
    () => board?.entries.filter((row) => row.kind === "loop_result") ?? [],
    [board],
  );
  const arenaRows = useMemo(
    () => board?.entries.filter((row) => row.kind === "arena_variant") ?? [],
    [board],
  );
  const topicRow = findTopicRow(status, topic);
  const compiledActive = Boolean(topicRow?.compiled?.present);

  const promoteBusy = previewBusy || applyBusy;
  const deleteBusy = deletePreviewBusy || deleteApplyBusy;
  const hasAnyRows =
    Boolean(openCompile) ||
    compileHistory.length > 0 ||
    liveLoopRows.length > 0 ||
    observationHistoryRows.length > 0 ||
    arenaRows.length > 0;

  function promoteKind(row: ScoreboardEntry): "compile" | "loop" | null {
    if (row.kind === "compile") return "compile";
    if (row.kind === "loop_result" || row.kind === "loop_candidate") return "loop";
    return null;
  }

  async function previewPromote(row: ScoreboardEntry) {
    if (!client || !topic || promoteBusy) return;
    const kind = promoteKind(row);
    if (!kind) return;
    setPreviewBusy(true);
    setActionMessage(null);
    setError(null);
    setDeletePreview(null);
    try {
      const preview = await client.branchPromote(kind, topic, row.name, "dry-run", vault);
      setPromotePreview({ kind, branch: row.name, result: preview });
    } catch (cause) {
      setPromotePreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function applyPromote() {
    if (!client || !topic || !promotePreview || applyBusy) return;
    const { kind, branch } = promotePreview;
    setApplyBusy(true);
    setError(null);
    try {
      const result = await client.branchPromote(kind, topic, branch, "apply", vault);
      setActionMessage(formatPromoteApplied(result));
      setPromotePreview(null);
      await Promise.all([onStatusRefresh?.(), loadBoard()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApplyBusy(false);
    }
  }

  async function previewDelete(branch: string) {
    if (!client || !topic || deleteBusy) return;
    setDeletePreviewBusy(true);
    setActionMessage(null);
    setError(null);
    setPromotePreview(null);
    try {
      const preview = await client.branchDelete(topic, branch, "dry-run", vault);
      setDeletePreview({ branch, result: preview });
    } catch (cause) {
      setDeletePreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeletePreviewBusy(false);
    }
  }

  async function applyDelete() {
    if (!client || !topic || !deletePreview || deleteApplyBusy) return;
    const { branch } = deletePreview;
    setDeleteApplyBusy(true);
    setError(null);
    try {
      const result = await client.branchDelete(topic, branch, "apply", vault);
      setActionMessage(formatDeleteApplied(result));
      setDeletePreview(null);
      await Promise.all([onStatusRefresh?.(), loadBoard()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeleteApplyBusy(false);
    }
  }

  return (
    <section class="panel scoreboard-panel" aria-label="Branch scoreboard">
      <header class="scoreboard-header">
        <div>
          <h2>Scoreboard</h2>
          <p>
            Gate baseline is <strong>per topic</strong> — stored in{" "}
            <code>{baselineMeta?.path ?? `${topic}/.knotica/loop-state.json`}</code>, not
            vault-wide. Compare compile branches against that scalar; only the latest open
            compile may be promoted.
          </p>
        </div>
      </header>

      <div class="scoreboard-hero">
        <div class="scoreboard-hero-copy">
          <span class="scoreboard-hero-label">Baseline · {topic}</span>
          <span class="health-chip ok">per topic</span>
          {gateFrozen ? (
            <span class="health-chip ok">frozen</span>
          ) : (
            <span class="health-chip warn">not frozen</span>
          )}
          <span class={`health-chip ${gateTone(gateState)}`}>{gateState}</span>
        </div>
        <output
          class={`scoreboard-hero-scalar ${baseline != null ? "scalar-good" : ""}`}
          aria-label="Per-topic gate baseline scalar"
        >
          {baseline != null ? baseline.toFixed(4) : "—"}
        </output>
        {lastMetricsScalar != null &&
        baseline != null &&
        Math.abs(lastMetricsScalar - baseline) > 1e-6 ? (
          <p class="scoreboard-hero-sub">
            Last metrics scalar{" "}
            <strong class={lastMetricsScalar >= baseline ? "delta-up" : "delta-down"}>
              {lastMetricsScalar.toFixed(4)}
            </strong>{" "}
            ({formatDelta(lastMetricsScalar - baseline)} vs baseline)
          </p>
        ) : lastMetricsScalar != null && baseline == null ? (
          <p class="scoreboard-hero-sub">
            Last metrics scalar <strong>{lastMetricsScalar.toFixed(4)}</strong> (baseline not
            frozen yet)
          </p>
        ) : null}
        {baselineMeta ? (
          <p class="muted scoreboard-hero-source">
            Source: <code>{baselineMeta.path}</code> · {baselineMeta.source}
          </p>
        ) : null}
      </div>

      {compiledActive ? (
        <div class="scoreboard-prompt-diff-hero">
          <p class="muted">
            Compile updates the runtime program in{" "}
            <code>{topic}/.knotica/compiled/query_v1.json</code> (instructions + demos), not{" "}
            <code>query.md</code>. Compare vault prompt vs full compiled program:
          </p>
          <PromptDiff client={client} topic={topic} vault={vault} mode="compiled" />
        </div>
      ) : null}

      {error ? (
        <p class="scoreboard-error" role="alert">
          {error}
        </p>
      ) : null}
      {actionMessage ? <p class="scoreboard-note">{actionMessage}</p> : null}

      <PromotePreviewBanner
        preview={promotePreview?.result ?? null}
        busy={applyBusy}
        onApply={() => void applyPromote()}
        onDismiss={() => setPromotePreview(null)}
      />
      <DeletePreviewBanner
        preview={deletePreview?.result ?? null}
        busy={deleteApplyBusy}
        onApply={() => void applyDelete()}
        onDismiss={() => setDeletePreview(null)}
      />

      {!hasAnyRows ? (
        <div class="scoreboard-empty">
          <p>No scored branches yet.</p>
          <ul>
            <li>
              <strong>Heal path:</strong> the watcher observes new content on{" "}
              <code>loop/c/*</code>, evals it on a clone, and gates it against the baseline.
            </li>
            <li>
              <strong>Flywheel:</strong> when compile-ready, run Compile — branches appear as{" "}
              <code>compile/&lt;topic&gt;/…</code>.
            </li>
          </ul>
        </div>
      ) : (
        <div class="scoreboard-sections">
          <section class="scoreboard-section" aria-label="Open compile">
            <header>
              <h3>Open compile</h3>
              <p>At most one active compile branch — promotable only when it beats baseline. After promote, delete the branch to clean up git; compile history stays in compile-state.</p>
            </header>
            {openCompile ? (
              <CompileCard
                row={openCompile}
                baseline={baseline}
                client={client}
                topic={topic}
                vault={vault}
                promoteBusy={promoteBusy}
                deleteBusy={deleteBusy}
                promoteActive={promotePreview?.branch === openCompile.name}
                deleteActive={deletePreview?.branch === openCompile.name}
                onPreviewPromote={() => void previewPromote(openCompile)}
                onPreviewDelete={() => void previewDelete(openCompile.name)}
              />
            ) : (
              <p class="muted scoreboard-section-empty">No open compile branch.</p>
            )}
          </section>

          {compileHistory.length > 0 ? (
            <section class="scoreboard-section" aria-label="Compile history">
              <header>
                <h3>Compile history</h3>
                <p>
                  Older or deleted compile runs — scores and status only. Live compiled-program
                  diff is on the hero and open compile card.
                </p>
              </header>
              <ul class="scoreboard-history">
                {compileHistory.map((row) => (
                  <CompileHistoryRow
                    key={row.name}
                    row={row}
                    deleteBusy={deleteBusy}
                    deleteActive={deletePreview?.branch === row.name}
                    onPreviewDelete={() => void previewDelete(row.name)}
                  />
                ))}
              </ul>
            </section>
          ) : null}

          {liveLoopRows.length > 0 ? (
            <section class="scoreboard-section" aria-label="Loop branches">
              <header>
                <h3>Loop candidates</h3>
                <p>Self-improving loop branches compared to the same per-topic baseline.</p>
              </header>
              <div class="scoreboard-table-wrap">
                <table class="scoreboard-table">
                  <thead>
                    <tr>
                      <th scope="col">Name</th>
                      <th scope="col">Kind</th>
                      <th scope="col">Score</th>
                      <th scope="col">Δ baseline</th>
                      <th scope="col">Status</th>
                      <th scope="col" />
                    </tr>
                  </thead>
                  <tbody>
                    {liveLoopRows.map((row) => (
                      <LoopRow
                        key={`${row.kind}:${row.name}`}
                        row={row}
                        selected={selectedBranch === row.name}
                        promoteBusy={promoteBusy}
                        promoteActive={promotePreview?.branch === row.name}
                        onSelect={() =>
                          setSelectedBranch((current) => (current === row.name ? null : row.name))
                        }
                        onPreviewPromote={() => void previewPromote(row)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              {selectedBranch && liveLoopRows.some((row) => row.name === selectedBranch) ? (
                <PromptDiff
                  client={client}
                  topic={topic}
                  vault={vault}
                  branch={selectedBranch}
                  label="Show query.md diff for selected loop branch"
                />
              ) : null}
            </section>
          ) : null}

          {observationHistoryRows.length > 0 ? (
            <section class="scoreboard-section" aria-label="Observation history">
              <header>
                <h3>Observation history</h3>
                <p>
                  <code>loop/r/*</code> tips — already merged onto the default branch the
                  moment the watcher created them. Auto-pruned to the newest 5; delete older
                  ones early if you want.
                </p>
              </header>
              <ul class="scoreboard-history">
                {observationHistoryRows.map((row) => (
                  <ObservationHistoryRow
                    key={row.name}
                    row={row}
                    deleteBusy={deleteBusy}
                    deleteActive={deletePreview?.branch === row.name}
                    onPreviewDelete={() => void previewDelete(row.name)}
                  />
                ))}
              </ul>
            </section>
          ) : null}

          {arenaRows.length > 0 ? (
            <section class="scoreboard-section" aria-label="Arena variants">
              <header>
                <h3>Arena variants</h3>
              </header>
              <ul class="scoreboard-history">
                {arenaRows.map((row) => (
                  <li key={row.name} class="scoreboard-history-row">
                    <span class="scoreboard-history-name">{row.name}</span>
                    <span>{row.scalar != null ? row.scalar.toFixed(4) : "—"}</span>
                    <span class={deltaTone(row.delta)}>{formatDeltaOrDash(row.delta)}</span>
                    <span class={`status-chip status-${row.status.replace(/[^a-z]+/g, "-")}`}>
                      {row.status}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      )}
    </section>
  );
}

function CompileCard({
  row,
  baseline,
  client,
  topic,
  vault,
  promoteBusy,
  deleteBusy,
  promoteActive,
  deleteActive,
  onPreviewPromote,
  onPreviewDelete,
}: {
  row: ScoreboardEntry;
  baseline: number | null;
  client: ToolClient | null;
  topic: string;
  vault: string;
  promoteBusy: boolean;
  deleteBusy: boolean;
  promoteActive: boolean;
  deleteActive: boolean;
  onPreviewPromote: () => void;
  onPreviewDelete: () => void;
}) {
  const underBaseline = row.beats_baseline === false;
  const showPromote = row.promotable;
  const showDelete = row.deletable === true;
  const showCleanupHint =
    showDelete && row.beats_baseline === true && (row.status === "promoted" || !showPromote);

  return (
    <article class={`scoreboard-open-card ${underBaseline ? "under-baseline" : "beats-baseline"}`}>
      <div class="scoreboard-open-head">
        <code class="scoreboard-name">{row.name}</code>
        {row.sha ? <span class="scoreboard-sha">{row.sha}</span> : null}
        <span class={`status-chip status-${row.status.replace(/[^a-z]+/g, "-")}`}>
          {row.status}
        </span>
      </div>
      <div class="scoreboard-open-metrics">
        <div>
          <span class="stat-label">Score</span>
          <strong>{row.scalar != null ? row.scalar.toFixed(4) : "—"}</strong>
        </div>
        <div>
          <span class="stat-label">Δ baseline</span>
          <strong class={deltaTone(row.delta)}>{formatDeltaOrDash(row.delta)}</strong>
        </div>
        {row.delta_before != null ? (
          <div>
            <span class="stat-label">Δ compile run</span>
            <strong class={deltaTone(row.delta_before)}>{formatDelta(row.delta_before)}</strong>
          </div>
        ) : null}
      </div>
      {row.note ? <p class="muted scoreboard-open-note">{row.note}</p> : null}
      {baseline != null && row.scalar != null ? (
        <p class="scoreboard-open-verdict">
          {row.beats_baseline ? (
            <span class="delta-up">Beats per-topic baseline ({baseline.toFixed(4)})</span>
          ) : (
            <span class="delta-down">Does not beat per-topic baseline ({baseline.toFixed(4)})</span>
          )}
        </p>
      ) : null}
      {showCleanupHint ? (
        <p class="muted scoreboard-open-note">Safe to delete after promote — history kept.</p>
      ) : null}
      <div class="scoreboard-open-actions">
        {showPromote ? (
          <button
            type="button"
            class="primary"
            disabled={promoteBusy}
            onClick={onPreviewPromote}
          >
            {promoteBusy && promoteActive ? "Previewing…" : promoteActive ? "Preview ready" : "Preview promote"}
          </button>
        ) : null}
        {showDelete ? (
          <button
            type="button"
            class="danger"
            disabled={deleteBusy}
            onClick={onPreviewDelete}
          >
            {deleteBusy && deleteActive ? "Previewing…" : deleteActive ? "Preview ready" : "Preview delete"}
          </button>
        ) : null}
      </div>
      <PromptDiff
        client={client}
        topic={topic}
        vault={vault}
        mode="compiled"
        branch={row.name}
      />
      <PromptDiff
        client={client}
        topic={topic}
        vault={vault}
        branch={row.name}
        baseRef={row.base_sha ?? undefined}
        headRef={row.head_sha ?? undefined}
        historyId={row.history_id ?? undefined}
        diffAvailable={row.diff_available !== false}
        label="Show query.md git diff"
      />
    </article>
  );
}

function CompileHistoryRow({
  row,
  deleteBusy,
  deleteActive,
  onPreviewDelete,
}: {
  row: ScoreboardEntry;
  deleteBusy: boolean;
  deleteActive: boolean;
  onPreviewDelete: () => void;
}) {
  const archived = row.branch_deleted || row.slot === "archived";
  return (
    <li class="scoreboard-history-item">
      <div class="scoreboard-history-row">
        <span class="scoreboard-history-name">
          <code>{row.name}</code>
          {row.sha ? <span class="scoreboard-sha">{row.sha}</span> : null}
          {archived ? <span class="health-chip warn">branch deleted</span> : null}
          {row.created ? <time class="scoreboard-history-date">{formatWhen(row.created)}</time> : null}
        </span>
        <span>{row.scalar != null ? row.scalar.toFixed(4) : "—"}</span>
        <span class={deltaTone(row.delta)}>{formatDeltaOrDash(row.delta)}</span>
        <span class={`status-chip status-${row.status.replace(/[^a-z]+/g, "-")}`}>
          {row.status}
        </span>
        {row.deletable ? (
          <button type="button" class="ghost" disabled={deleteBusy} onClick={onPreviewDelete}>
            {deleteBusy && deleteActive ? "…" : "Delete"}
          </button>
        ) : (
          <span />
        )}
      </div>
      {row.base_sha && row.head_sha ? (
        <p class="muted scoreboard-history-shas">
          {shortSha(row.base_sha)} ↔ {shortSha(row.head_sha)}
          {row.merge_sha ? ` · merge ${shortSha(row.merge_sha)}` : null}
          {row.delta_before != null ? (
            <>
              {" · "}
              <span class={deltaTone(row.delta_before)}>Δ run {formatDelta(row.delta_before)}</span>
            </>
          ) : null}
        </p>
      ) : row.delta_before != null ? (
        <p class="muted scoreboard-history-shas">
          <span class={deltaTone(row.delta_before)}>Δ run {formatDelta(row.delta_before)}</span>
        </p>
      ) : null}
    </li>
  );
}

function shortSha(value: string): string {
  return value.length > 12 ? value.slice(0, 12) : value;
}

function ObservationHistoryRow({
  row,
  deleteBusy,
  deleteActive,
  onPreviewDelete,
}: {
  row: ScoreboardEntry;
  deleteBusy: boolean;
  deleteActive: boolean;
  onPreviewDelete: () => void;
}) {
  return (
    <li class="scoreboard-history-item scoreboard-history-item-muted">
      <div class="scoreboard-history-row">
        <span class="scoreboard-history-name">
          <code>{row.name}</code>
          {row.sha ? <span class="scoreboard-sha">{row.sha}</span> : null}
          {row.created ? <time class="scoreboard-history-date">{formatWhen(row.created)}</time> : null}
        </span>
        <span>{row.scalar != null ? row.scalar.toFixed(4) : "—"}</span>
        <span class={deltaTone(row.delta)}>{formatDeltaOrDash(row.delta)}</span>
        <span class="health-chip ok scoreboard-merged-chip">merged · history</span>
        <button type="button" class="ghost" disabled={deleteBusy} onClick={onPreviewDelete}>
          {deleteBusy && deleteActive ? "…" : "Delete"}
        </button>
      </div>
    </li>
  );
}

function LoopRow({
  row,
  selected,
  promoteBusy,
  promoteActive,
  onSelect,
  onPreviewPromote,
}: {
  row: ScoreboardEntry;
  selected: boolean;
  promoteBusy: boolean;
  promoteActive: boolean;
  onSelect: () => void;
  onPreviewPromote: () => void;
}) {
  if (row.kind !== "loop_candidate" && row.kind !== "loop_result") return null;
  return (
    <tr class={selected ? "selected" : ""}>
      <td>
        <button type="button" class="scoreboard-row-select ghost" onClick={onSelect}>
          <code class="scoreboard-name">{row.name}</code>
          {row.sha ? <span class="scoreboard-sha">{row.sha}</span> : null}
        </button>
      </td>
      <td>
        <span class={`kind-chip kind-${row.kind}`}>{LOOP_KIND_LABEL[row.kind]}</span>
      </td>
      <td>{row.scalar != null ? row.scalar.toFixed(4) : "—"}</td>
      <td class={deltaTone(row.delta)}>{formatDeltaOrDash(row.delta)}</td>
      <td>
        <span class={`status-chip status-${row.status.replace(/[^a-z]+/g, "-")}`}>
          {row.status}
        </span>
      </td>
      <td class="scoreboard-actions">
        {row.promotable ? (
          <button
            type="button"
            disabled={promoteBusy}
            class={promoteActive ? "active" : ""}
            onClick={onPreviewPromote}
          >
            {promoteBusy && promoteActive ? "Previewing…" : promoteActive ? "Preview ready" : "Preview"}
          </button>
        ) : null}
      </td>
    </tr>
  );
}

function gateTone(state: string): string {
  if (state === "pass") return "ok";
  if (state === "fail") return "bad";
  if (state === "unknown") return "warn";
  return "warn";
}

function deltaTone(delta: number | null | undefined): string {
  if (delta == null) return "";
  return delta >= 0 ? "delta-up" : "delta-down";
}

function formatDelta(delta: number): string {
  const sign = delta >= 0 ? "+" : "";
  return `${sign}${delta.toFixed(4)}`;
}

function formatDeltaOrDash(delta: number | null | undefined): string {
  return delta != null ? formatDelta(delta) : "—";
}

function formatWhen(iso: string): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return iso;
  return new Date(parsed).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
