import { useEffect, useRef, useState } from "preact/hooks";

import { isCompileActive } from "./compileStages";
import { PromotePreviewBanner } from "./PromotePreview";
import { PromptDiff } from "./PromptDiff";
import { formatPromoteApplied } from "./promoteHelpers";
import type { ToolClient } from "./toolClient";
import { findTopicRow, queryTrainCount } from "./topicHelpers";
import type { CompilePromoteResult, CompileRunResult, CompileStatus, WikiStatus } from "./types";

export function CompilePanel({
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
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [promoteMessage, setPromoteMessage] = useState<string | null>(null);
  const [promotePreview, setPromotePreview] = useState<CompilePromoteResult | null>(null);
  const [poll, setPoll] = useState<CompileStatus | null>(null);
  const [lastRun, setLastRun] = useState<CompileRunResult | null>(null);
  const inFlight = useRef(false);

  const row = findTopicRow(status, topic);
  const threshold = status?.compile_ready_threshold ?? 30;
  const goldenFloor = status?.eval_min_golden ?? 20;
  const trainN = queryTrainCount(row);
  const ready = Boolean(row?.compile_ready);
  const compiled = row?.compiled;
  const compile = poll ?? status?.compile ?? null;
  const stage = compile?.stage ?? "idle";
  const active = isCompileActive(stage);
  const promoteBranchName = compile?.branch ?? lastRun?.branch ?? null;
  const canPromote = stage === "completed" && Boolean(promoteBranchName);
  const promoteBusy = previewBusy || applyBusy;
  const trialPct =
    compile && compile.trial_total > 0
      ? Math.min(100, Math.round((compile.trial / compile.trial_total) * 100))
      : active
        ? 15
        : stage === "completed"
          ? 100
          : 0;

  useEffect(() => {
    if (!client || !topic || !active) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await client.compileStatus(topic, vault);
        if (!cancelled) setPoll(next);
      } catch {
        /* keep last snapshot */
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [client, topic, vault, active]);

  useEffect(() => {
    setPromotePreview(null);
    setPromoteMessage(null);
  }, [promoteBranchName, topic, vault]);

  async function previewMerge() {
    const branch = promoteBranchName;
    if (!client || !topic || !branch || promoteBusy) return;
    setPreviewBusy(true);
    setError(null);
    setPromoteMessage(null);
    try {
      const preview = await client.compilePromote(topic, branch, "dry-run", vault);
      setPromotePreview(preview);
    } catch (cause) {
      setPromotePreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function applyMerge() {
    const branch = promoteBranchName;
    if (!client || !topic || !branch || !promotePreview || applyBusy) return;
    setApplyBusy(true);
    setError(null);
    try {
      const result = await client.compilePromote(topic, branch, "apply", vault);
      setPromoteMessage(formatPromoteApplied(result));
      setPromotePreview(null);
      setPoll(null);
      setLastRun(null);
      await onStatusRefresh?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApplyBusy(false);
    }
  }

  async function startCompile() {
    if (!client || !topic || busy || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    setPromotePreview(null);
    setPromoteMessage(null);
    try {
      const result = await client.compileRun(topic, vault);
      setLastRun(result);
      setPoll({
        schema_version: 1,
        topic: result.topic,
        stage: result.stage,
        branch: result.branch,
        message: result.message,
        trial: 1,
        trial_total: 1,
        scalar_before: result.scalar_before,
        scalar_after: result.scalar_after,
        error: null,
        updated_at: "",
      });
      onStatusRefresh?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const delta =
    compile?.scalar_before != null && compile?.scalar_after != null
      ? compile.scalar_after - compile.scalar_before
      : lastRun?.scalar_before != null && lastRun?.scalar_after != null
        ? lastRun.scalar_after - lastRun.scalar_before
        : null;

  return (
    <section class="compile-panel" aria-label="Compile">
      <header class="compile-header">
        <div>
          <h3>Compile</h3>
          <p>Proactive flywheel — optimize query from curated trainset</p>
        </div>
        {compiled?.present ? (
          <span class="compile-header-chips">
            <span class="health-chip ok">Compiled</span>
            {compiled.optimizer ? (
              <span
                class="health-chip warn"
                title={
                  compiled.optimizer === "bootstrap" && compiled.fallback_reason
                    ? `MIPRO unavailable: ${compiled.fallback_reason}`
                    : undefined
                }
              >
                {compiled.optimizer === "mipro" ? "MIPRO" : "bootstrap"}
              </span>
            ) : null}
          </span>
        ) : ready ? (
          <span class="health-chip warn">Ready</span>
        ) : (
          <span class="health-chip bad">
            {trainN}/{threshold}
          </span>
        )}
      </header>

      <div class="compile-meter" aria-label="Compile-ready progress">
        <div class="curate-track health-ok" aria-hidden="true">
          <div
            class="curate-fill"
            style={{ width: `${Math.min(100, Math.round((trainN / threshold) * 100))}%` }}
          />
        </div>
        <p
          class="muted"
          title={
            `Trainset query-style rows in qa.jsonl vs compile floor (≥${threshold}). ` +
            `Held-out rows in golden.jsonl vs eval floor (≥${goldenFloor}).`
          }
        >
          Trainset {trainN}
          {trainN >= threshold ? ` (≥${threshold} ✓)` : ` of ≥${threshold} needed for compile`}
          {row
            ? ` · Held-out ${row.golden_n ?? 0}` +
              ((row.golden_n ?? 0) >= goldenFloor
                ? ` (≥${goldenFloor} ✓)`
                : ` of ≥${goldenFloor}`)
            : ""}
          {compiled?.present ? " · artifact active" : ""}
        </p>
      </div>

      {compiled?.present || canPromote ? (
        <div class="compile-prompt-diff">
          <p class="muted">
            {compiled?.present
              ? "Active compile artifact — compare vault query.md vs full runtime program:"
              : "Preview compile branch — vault query.md vs branch runtime program:"}
          </p>
          <PromptDiff
            client={client}
            topic={topic}
            vault={vault}
            mode="compiled"
            branch={canPromote && promoteBranchName ? promoteBranchName : undefined}
          />
        </div>
      ) : null}

      {active || stage === "completed" || stage === "failed" ? (
        <div class="compile-progress">
          <div class="curate-track health-warn" aria-hidden="true">
            <div class="curate-fill" style={{ width: `${trialPct}%` }} />
          </div>
          <p>
            <strong>{stage}</strong>
            {compile?.trial_total
              ? ` · trial ${compile.trial}/${compile.trial_total}`
              : null}
          </p>
          {compile?.message ? <p class="muted">{compile.message}</p> : null}
          {compile?.branch ? (
            <p class="compile-branch">
              Branch <code>{compile.branch}</code>
            </p>
          ) : null}
          {delta != null ? (
            <p class="compile-delta">
              Scalar {compile?.scalar_before?.toFixed(3)} → {compile?.scalar_after?.toFixed(3)}{" "}
              ({delta >= 0 ? "+" : ""}
              {delta.toFixed(3)})
            </p>
          ) : null}
          {stage === "completed" ? (
            <p class="compile-cta">
              Preview the compile branch merge, then apply to activate the artifact and prove in Ask.
            </p>
          ) : null}
        </div>
      ) : null}

      {promoteMessage ? <p class="scoreboard-note">{promoteMessage}</p> : null}
      <PromotePreviewBanner
        preview={promotePreview}
        busy={applyBusy}
        applyLabel="Apply merge to main"
        onApply={() => void applyMerge()}
        onDismiss={() => setPromotePreview(null)}
      />
      {error ? <aside role="alert">Compile failed: {error}</aside> : null}

      <div class="compile-actions">
        <button
          type="button"
          disabled={!client || !topic || !ready || busy || active || Boolean(compiled?.present)}
          onClick={() => void startCompile()}
        >
          {busy || active ? "Compiling…" : "Compile"}
        </button>
        <button
          type="button"
          class="ghost"
          disabled={!client || !topic || promoteBusy || !canPromote}
          onClick={() => void previewMerge()}
        >
          {previewBusy ? "Previewing…" : promotePreview ? "Preview ready" : "Preview merge"}
        </button>
        <button
          type="button"
          class="ghost"
          disabled={!client || !topic || promoteBusy || !promotePreview}
          onClick={() => void applyMerge()}
        >
          {applyBusy ? "Applying…" : "Apply merge to main"}
        </button>
      </div>
    </section>
  );
}
