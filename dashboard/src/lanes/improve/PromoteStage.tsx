import type { JSX } from "preact";
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import { DeletePreviewBanner } from "../../DeletePreview";
import { formatDeleteApplied } from "../../deleteHelpers";
import { PromotePreviewBanner } from "../../PromotePreview";
import { formatPromoteApplied } from "../../promoteHelpers";
import { PromptDiff } from "../../PromptDiff";
import { SectionCard } from "../../SectionCard";
import type { SectionTone } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { TermHint } from "../../TermHint";
import { Spinner } from "../../icons";
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

function deltaTone(delta: number | null | undefined): SectionTone | undefined {
  if (delta == null) return undefined;
  return delta >= 0 ? "good" : "bad";
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

      <SectionCard
        title="BRANCH UNDER REVIEW"
        icon="stage:promote"
        headerActions={openCompile ? branchIdentity(openCompile) : undefined}
        footer={
          openCompile ? (
            <>
              {/* `Preview delete` sits left of `Preview promote` so the
                  destructive control is never the rightmost, closest-to-thumb
                  target — Fitts, inverted deliberately. */}
              {openCompile.deletable ? (
                <button
                  type="button"
                  class="danger"
                  disabled={!client || deleteBusy}
                  aria-busy={deletePreviewBusy || undefined}
                  onClick={() => void previewDelete()}
                >
                  {deletePreviewBusy ? (
                    <>
                      <Spinner />
                      Previewing…
                    </>
                  ) : (
                    "Preview delete"
                  )}
                </button>
              ) : null}
              {openCompile.promotable ? (
                <button
                  type="button"
                  class="primary"
                  data-testid="promote-preview-trigger"
                  disabled={!client || promoteBusy}
                  aria-busy={previewBusy || undefined}
                  onClick={() => void previewPromote()}
                >
                  {previewBusy ? (
                    <>
                      <Spinner />
                      Previewing…
                    </>
                  ) : (
                    "Preview promote"
                  )}
                </button>
              ) : null}
            </>
          ) : undefined
        }
      >
        {openCompile ? (
          <>
            <StatGrid>
              <Stat
                label={hint("score")}
                value={
                  openCompile.scalar != null
                    ? openCompile.scalar.toFixed(4)
                    : null
                }
              />
              <Stat
                label={hint("delta")}
                value={
                  openCompile.delta == null
                    ? null
                    : formatDeltaOrDash(openCompile.delta)
                }
                tone={deltaTone(openCompile.delta)}
              />
              <Stat
                label={hint("baseline")}
                value={baseline != null ? baseline.toFixed(4) : null}
              />
            </StatGrid>
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
          </>
        ) : (
          <p class="muted">
            No open compile branch to promote yet. Heal writes one after a gate
            refusal.
          </p>
        )}
      </SectionCard>

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

/**
 * The reviewed branch's identity in the card header: its name, its sha and
 * its status verdict. The three elements keep their existing classes and
 * their existing text — only their container changed.
 */
function branchIdentity(openCompile: ScoreboardEntry): JSX.Element {
  return (
    <>
      <code class="scoreboard-name">{openCompile.name}</code>
      {openCompile.sha ? (
        <span class="scoreboard-sha">{openCompile.sha}</span>
      ) : null}
      <span
        class={`status-chip status-${openCompile.status.replace(/[^a-z]+/g, "-")}`}
      >
        <TermHint
          id="promote-status"
          term={openCompile.status}
          title="Promotable"
          body="A branch is promotable once it has been gated and beats the baseline. Promote merges it into the vault; delete drops the branch and keeps the vault as it is."
          align="end"
        />
      </span>
    </>
  );
}

/** The explanatory copy behind each stat label's `TermHint`. */
const PROMOTE_HINTS = {
  score: {
    term: "SCORE",
    title: "Score",
    body: "What this branch scored on the held-out set, using the same harness as the baseline. Scores from different harness versions are not comparable — the loop reports unknown rather than pretending otherwise.",
  },
  delta: {
    term: "Δ BASELINE",
    title: "Δ baseline",
    body: "Score minus baseline. Positive means this branch is better on the held-out set; it does not mean better everywhere.",
  },
  baseline: {
    term: "BASELINE",
    title: "Baseline",
    body: "The frozen stick this topic is measured against. Promoting a branch that beats it moves the stick.",
  },
} as const;

function hint(key: keyof typeof PROMOTE_HINTS): JSX.Element {
  return <TermHint id={`promote-${key}`} {...PROMOTE_HINTS[key]} />;
}
