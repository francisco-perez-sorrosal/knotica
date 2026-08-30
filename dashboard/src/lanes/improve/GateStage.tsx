import type { JSX } from "preact";

import { PromptDiff } from "../../PromptDiff";
import { SectionCard } from "../../SectionCard";
import { StateList } from "../../StateList";
import type { StateListRow } from "../../StateList";
import { TermHint } from "../../TermHint";
import { useTwoPhaseAction } from "../../TwoPhaseAction";
import { Spinner } from "../../icons";
import type { ToolClient } from "../../toolClient";
import type {
  LoopOnceResult,
  LoopPendingCandidate,
  WikiStatus,
} from "../../types";

/**
 * The `gate` stage body — absorbs `LoopPane`'s pending-candidate list and its
 * billed `loop run_once` trigger (`gateNowControls`). The billed cycle stays
 * on the shared two-phase nonce primitive (`TwoPhaseAction.tsx`) rather than
 * the armed→confirm affordance: `run_once` already mints a server-side nonce
 * and quote, so the orchestrator's no-native-dialogs ruling (`LEARNINGS.md`)
 * is satisfied by that flow as-is — the ruling only reaches spend-immediately
 * actions with no free preview leg (see `HealStage.tsx`'s `compile action=run`).
 *
 * The body is one `SectionCard` holding the candidate rows, with the billed
 * trigger in the footer of the card whose data it acts on. The
 * `baseline_unreachable` alert stays *above* the card: it explains why every
 * candidate must fail, so it outranks any single card. The candidate list is
 * a `StateList` — the state word ("pending"/"processed") is visible text next
 * to its icon and the sha is a right-aligned tabular value column, instead of
 * the run-on "pending · sha" tag the old `.candidate-tag` rendered.
 *
 * The trigger carries no `title=` any more. A tooltip is invisible on touch,
 * needs a hover dwell and is unreachable by keyboard; what it said now reads
 * as a visible `.section-card-note` under the footer, which also carries the
 * reason the control is disabled when there is nothing pending.
 */

export function GateStage({
  client,
  topic,
  vault,
  status,
  onStatusRefresh,
}: {
  client?: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const pendingCandidates = status?.loop.pending_candidates ?? [];
  const pendingCount = pendingCandidates.filter((row) => row.pending).length;
  const baselineUnreachable = status?.loop.baseline_unreachable ?? null;

  // Both legs go through one call expression, so the confirm leg is provably
  // the preview leg plus the nonce — mirrors `LoopPane.tsx`'s `gateCandidate`.
  const gateCandidate = (confirm: string) =>
    client!.loopRunOnce(topic, confirm, vault);
  const gateOnce = useTwoPhaseAction<LoopOnceResult>({
    preview: () => gateCandidate(""),
    confirm: async (nonce) => {
      const result = await gateCandidate(nonce);
      await onStatusRefresh?.();
      return result;
    },
  });
  const { preview, outcome, busy } = gateOnce.state;
  const triggerDisabled = !client || busy !== null || pendingCount === 0;

  return (
    <div class="gate-stage">
      {baselineUnreachable ? (
        <p class="lane-stage-remedy" role="alert">
          {baselineUnreachable.message} {baselineUnreachable.fix}
        </p>
      ) : null}

      <SectionCard
        title="PENDING CANDIDATES"
        icon="stage:gate"
        headerActions={
          <span class="chip">
            <TermHint
              id="gate-pending-count"
              term={pendingCount > 0 ? `${pendingCount} pending` : "none pending"}
              title="Gate baseline"
              body="A candidate must beat the frozen baseline to merge. If the baseline is unreachable, every candidate fails for a reason that has nothing to do with the candidate — which is what the alert above says."
              align="end"
            />
          </span>
        }
        footer={
          outcome ? (
            <div class="heal-policy-controls heal-run-eval-outcome" role="status">
              <p class="heal-step-body">
                {outcome.billed ? (
                  <>
                    <strong>Gate cycle ran — this billed.</strong>{" "}
                    {outcome.message || "No further detail was reported."}
                  </>
                ) : (
                  <>
                    <strong>Nothing ran, nothing was billed.</strong>{" "}
                    {outcome.message || "The loop declined this tick."}
                  </>
                )}
              </p>
              <button type="button" class="ghost" onClick={gateOnce.reset}>
                Dismiss
              </button>
            </div>
          ) : preview ? (
            <div class="heal-policy-controls heal-run-eval-confirm">
              <p class="heal-step-body">
                Preview: {preview.estimated_cost ?? "one gate cycle"}.{" "}
                {preview.holds?.held ? (
                  <>
                    The loop would decline this tick right now
                    {preview.holds.reasons.length > 0
                      ? ` (${preview.holds.reasons.join("; ")})`
                      : ""}
                    .{" "}
                  </>
                ) : null}
                This has NOT billed yet — confirm to run and bill.
              </p>
              <button
                type="button"
                data-testid="gate-run-once-confirm"
                class="heal-freeze-primary"
                disabled={busy !== null}
                aria-busy={busy === "confirm" || undefined}
                onClick={() => void gateOnce.confirm()}
              >
                {busy === "confirm" ? (
                  <>
                    <Spinner />
                    Gating…
                  </>
                ) : (
                  "Confirm — run and bill"
                )}
              </button>
              <button
                type="button"
                class="ghost"
                disabled={busy !== null}
                onClick={gateOnce.reset}
              >
                Cancel
              </button>
            </div>
          ) : (
            <>
              {/* Sibling of the button, never a child: the accessible name
                  stays `Gate next candidate now` and the two-phase contract
                  is untouched. */}
              <span class="chip cost">billed</span>
              <button
                type="button"
                data-testid="gate-run-once-preview"
                class="primary"
                disabled={triggerDisabled}
                aria-busy={busy === "preview" || undefined}
                onClick={() => void gateOnce.preview()}
              >
                {busy === "preview" ? (
                  <>
                    <Spinner />
                    Estimating…
                  </>
                ) : (
                  "Gate next candidate now"
                )}
              </button>
              <p class="section-card-note">
                Nudges the watcher to gate the next candidate now instead of
                waiting for its next tick. The first click only quotes it.
                {pendingCount === 0
                  ? " Nothing is pending, so there is nothing to gate."
                  : ""}
              </p>
            </>
          )
        }
      >
        <>
          <p class="muted">
            The watcher gates each <code>loop/c/*</code> tip automatically on
            its next tick. Gate pass merges; gate fail opens Heal.
          </p>
          {pendingCandidates.length > 0 ? (
            <StateList
              label="Pending candidates"
              rows={pendingCandidates.map((row) =>
                candidateRow(row, client ?? null, topic, vault),
              )}
            />
          ) : (
            <p class="muted">
              Push new content to a local <code>loop/c/*</code> branch — the
              watcher picks it up, evals it on a clone, and gates the result
              against the baseline. Nothing to do here manually; this card
              fills in once a candidate is pending.
            </p>
          )}
        </>
      </SectionCard>
    </div>
  );
}

/**
 * One candidate as a `StateList` row. The old `.candidate-tag`'s
 * "pending · sha" run-on splits: the word becomes the toned state chip, the
 * sha becomes the right-aligned tabular value column.
 */
function candidateRow(
  row: LoopPendingCandidate,
  client: ToolClient | null,
  topic: string,
  vault: string,
): StateListRow {
  return {
    id: row.branch,
    state: row.pending ? "pending" : "processed",
    icon: row.pending ? "state:pending" : "state:complete",
    name: (
      <TermHint
        id={`gate-candidate-${row.branch}`}
        term={row.branch}
        title="Candidate branch"
        body="A local loop/c/* branch. The watcher picks up each tip, evaluates it on a clone of the vault, and scores it against the gate baseline. The live vault is never touched."
      />
    ),
    stateLabel: row.pending ? "pending" : "processed",
    tone: row.pending ? "warn" : "good",
    value: row.sha,
    action: row.pending ? (
      <PromptDiff
        client={client}
        topic={topic}
        vault={vault}
        branch={row.branch}
        label="Show query.md diff"
      />
    ) : undefined,
  };
}
