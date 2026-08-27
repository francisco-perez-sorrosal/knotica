import type { JSX } from "preact";

import { PromptDiff } from "../../PromptDiff";
import { useTwoPhaseAction } from "../../TwoPhaseAction";
import type { ToolClient } from "../../toolClient";
import type { LoopOnceResult, WikiStatus } from "../../types";

/**
 * The `gate` stage body (`INTERFACE_DESIGN.md §2.4`) — absorbs `LoopPane`'s
 * pending-candidate list and its billed `loop run_once` trigger
 * (`gateNowControls`). The billed cycle stays on the shared two-phase nonce
 * primitive (`TwoPhaseAction.tsx`) rather than the armed→confirm affordance:
 * `run_once` already mints a server-side nonce and quote, so the orchestrator's
 * no-native-dialogs ruling (`LEARNINGS.md`) is satisfied by that flow as-is —
 * the ruling only reaches spend-immediately actions with no free preview leg
 * (see `HealStage.tsx`'s `compile action=run`).
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

  return (
    <div class="gate-stage">
      {baselineUnreachable ? (
        <p class="lane-stage-remedy" role="alert">
          {baselineUnreachable.message} {baselineUnreachable.fix}
        </p>
      ) : null}

      {pendingCandidates.length > 0 ? (
        <ul class="candidate-list">
          {pendingCandidates.map((row) => (
            <li key={row.branch}>
              <code>{row.branch}</code>
              <span class={`candidate-tag ${row.pending ? "pending" : "done"}`}>
                {row.pending ? "pending" : "processed"} · {row.sha}
              </span>
              {row.pending ? (
                <PromptDiff
                  client={client ?? null}
                  topic={topic}
                  vault={vault}
                  branch={row.branch}
                  label="Show query.md diff"
                />
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p class="muted">
          Push new content to a local <code>loop/c/*</code> branch — the watcher
          picks it up, evals it on a clone, and gates the result against the
          baseline. Nothing to do here manually; this card fills in once a
          candidate is pending.
        </p>
      )}

      {outcome ? (
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
            onClick={() => void gateOnce.confirm()}
          >
            {busy === "confirm" ? "Gating…" : "Confirm — run and bill"}
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
        <button
          type="button"
          data-testid="gate-run-once-preview"
          disabled={!client || busy !== null || pendingCount === 0}
          title="Nudges the watcher to gate the next candidate now, instead of waiting for its next tick"
          onClick={() => void gateOnce.preview()}
        >
          {busy === "preview" ? "Estimating…" : "Gate next candidate now"}
        </button>
      )}

      <p class="muted heal-hint">
        The watcher gates each <code>loop/c/*</code> tip automatically on its
        next tick — this button just runs one cycle immediately, and is billed:
        the first click only quotes it. Gate pass merges; gate fail opens Heal.
      </p>
    </div>
  );
}
