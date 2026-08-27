import type { JSX } from "preact";
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import { DeletePreviewBanner } from "../../DeletePreview";
import { formatDeleteApplied } from "../../deleteHelpers";
import { PromotePreviewBanner } from "../../PromotePreview";
import { formatPromoteApplied } from "../../promoteHelpers";
import { PromptDiff } from "../../PromptDiff";
import type { ToolClient } from "../../toolClient";
import type {
  BranchDeleteResult,
  BranchScoreboard,
  CompilePromoteResult,
  ScoreboardEntry,
  WikiStatus,
} from "../../types";

/**
 * The `promote` stage body (`INTERFACE_DESIGN.md §2.4`) — absorbs
 * `ScoreboardPanel`'s branches scoreboard/promote/delete surface, scoped to
 * the one branch that matters here: the reviewed (open) compile branch.
 * `compile action=promote` and `branches action=promote kind="compile"`
 * resolve to the same core call (`§2.4` point 6); this stage collapses onto
 * the latter (`client.branchPromote`) so there is exactly one promote
 * control and one scoreboard mount, never both `ScoreboardPanel` and
 * `CompilePanel` reaching for the same branch.
 *
 * `deltaTone`/`formatDeltaOrDash` intentionally duplicate two small pure
 * helpers already private to `ScoreboardPanel.tsx` rather than extracting a
 * shared module: `ScoreboardPanel.tsx` is out of this step's declared file
 * scope and is itself slated for dissolution once every stage that absorbs
 * a slice of it has landed (`IMPLEMENTATION_PLAN.md`'s M3 scope
 * clarification) — extracting a shared home now would edit a file about to
 * be deleted, for two five-line formatters.
 */

function deltaTone(delta: number | null | undefined): string {
  if (delta == null) return "";
  return delta >= 0 ? "delta-up" : "delta-down";
}

function formatDeltaOrDash(delta: number | null | undefined): string {
  if (delta == null) return "—";
  return `${delta >= 0 ? "+" : ""}${delta.toFixed(4)}`;
}

export function PromoteStage({
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
}): JSX.Element {
  const [board, setBoard] = useState<BranchScoreboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [preview, setPreview] = useState<CompilePromoteResult | null>(null);
  const [deletePreviewBusy, setDeletePreviewBusy] = useState(false);
  const [deleteApplyBusy, setDeleteApplyBusy] = useState(false);
  const [deletePreview, setDeletePreview] = useState<BranchDeleteResult | null>(
    null,
  );

  const loadBoard = useCallback(async () => {
    if (!client || !topic) return;
    try {
      const next = await client.branchScoreboard(topic, vault);
      setBoard(next);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [client, topic, vault]);

  useEffect(() => {
    void loadBoard();
  }, [loadBoard]);

  const openCompile = useMemo<ScoreboardEntry | null>(
    () =>
      board?.entries.find(
        (row) => row.kind === "compile" && row.slot === "open",
      ) ?? null,
    [board],
  );
  const baseline = board?.baseline ?? status?.gate.baseline ?? null;
  const promoteBusy = previewBusy || applyBusy;
  const deleteBusy = deletePreviewBusy || deleteApplyBusy;

  async function previewPromote() {
    if (!client || !openCompile || promoteBusy) return;
    setPreviewBusy(true);
    setError(null);
    setDeletePreview(null);
    try {
      const next = await client.branchPromote(
        "compile",
        topic,
        openCompile.name,
        "dry-run",
        vault,
      );
      setPreview(next);
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function applyPromote() {
    if (!client || !openCompile || !preview || applyBusy) return;
    setApplyBusy(true);
    setError(null);
    try {
      const result = await client.branchPromote(
        "compile",
        topic,
        openCompile.name,
        "apply",
        vault,
      );
      setNote(formatPromoteApplied(result));
      setPreview(null);
      await Promise.all([onStatusRefresh?.(), loadBoard()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setApplyBusy(false);
    }
  }

  async function previewDelete() {
    if (!client || !openCompile || deleteBusy) return;
    setDeletePreviewBusy(true);
    setError(null);
    setPreview(null);
    try {
      const next = await client.branchDelete(
        topic,
        openCompile.name,
        "dry-run",
        vault,
      );
      setDeletePreview(next);
    } catch (cause) {
      setDeletePreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeletePreviewBusy(false);
    }
  }

  async function applyDelete() {
    if (!client || !openCompile || !deletePreview || deleteApplyBusy) return;
    setDeleteApplyBusy(true);
    setError(null);
    try {
      const result = await client.branchDelete(
        topic,
        openCompile.name,
        "apply",
        vault,
      );
      setNote(formatDeleteApplied(result));
      setDeletePreview(null);
      await Promise.all([onStatusRefresh?.(), loadBoard()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeleteApplyBusy(false);
    }
  }

  return (
    <div class="promote-stage">
      {error ? (
        <p role="alert" class="ask-error">
          {error}
        </p>
      ) : null}
      {note ? <p class="scoreboard-note">{note}</p> : null}

      {openCompile ? (
        <article class="scoreboard-open-card">
          <header class="scoreboard-open-head">
            <code class="scoreboard-name">{openCompile.name}</code>
            {openCompile.sha ? (
              <span class="scoreboard-sha">{openCompile.sha}</span>
            ) : null}
            <span
              class={`status-chip status-${openCompile.status.replace(/[^a-z]+/g, "-")}`}
            >
              {openCompile.status}
            </span>
          </header>
          <div class="scoreboard-open-metrics">
            <div>
              <span class="stat-label">Score</span>
              <strong>
                {openCompile.scalar != null
                  ? openCompile.scalar.toFixed(4)
                  : "—"}
              </strong>
            </div>
            <div>
              <span class="stat-label">Δ baseline</span>
              <strong class={deltaTone(openCompile.delta)}>
                {formatDeltaOrDash(openCompile.delta)}
              </strong>
            </div>
          </div>
          {baseline != null ? (
            <p class="scoreboard-open-verdict">
              {openCompile.beats_baseline ? (
                <span class="delta-up">
                  Beats per-topic baseline ({baseline.toFixed(4)})
                </span>
              ) : (
                <span class="delta-down">
                  Does not beat per-topic baseline ({baseline.toFixed(4)})
                </span>
              )}
            </p>
          ) : null}

          <PromptDiff
            client={client}
            topic={topic}
            vault={vault}
            mode="compiled"
            branch={openCompile.name}
          />

          <div class="scoreboard-open-actions">
            {openCompile.promotable ? (
              <button
                type="button"
                class="primary"
                data-testid="promote-preview-trigger"
                disabled={!client || promoteBusy}
                onClick={() => void previewPromote()}
              >
                {previewBusy ? "Previewing…" : "Preview promote"}
              </button>
            ) : null}
            {openCompile.deletable ? (
              <button
                type="button"
                class="danger"
                disabled={!client || deleteBusy}
                onClick={() => void previewDelete()}
              >
                {deletePreviewBusy ? "Previewing…" : "Preview delete"}
              </button>
            ) : null}
          </div>
        </article>
      ) : (
        <p class="muted">No open compile branch to promote yet.</p>
      )}

      <PromotePreviewBanner
        preview={preview}
        busy={applyBusy}
        onApply={() => void applyPromote()}
        onDismiss={() => setPreview(null)}
      />
      <DeletePreviewBanner
        preview={deletePreview}
        busy={deleteApplyBusy}
        onApply={() => void applyDelete()}
        onDismiss={() => setDeletePreview(null)}
      />
    </div>
  );
}
