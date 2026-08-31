import type { JSX } from "preact";

import { ProcessBrief } from "../ProcessBrief";
import { GapOriginBadge } from "./badges";
import { verbLabel } from "./verbLabel";
import type { GapRecord } from "../../types";

/**
 * `GapCard` -- one open gap in Fill's Gap stage, carrying the one gap-side
 * decision: **Dismiss…** (reason required, mirroring the approve queue's
 * reject form). Confirming routes through `QueueStage.dismissGap`, whose
 * server call also closes the gap's still-open suggestions in the same
 * commit -- the stage's outcome sentence says how many went with it. Reopen
 * is deliberately not offered here: this page lists open gaps only, so a
 * reopen control would act on rows it cannot show (`review_gap
 * decision=reopen` from MCP/CLI covers it).
 *
 * Extracted from `QueueStage.tsx` when the dismiss affordance pushed that
 * module to the size ceiling -- a cohesion split, not a redesign; state stays
 * in the stage, exactly as `SuggestionRow` keeps its state there.
 */
export function GapCard({
  gap,
  dismissOpen,
  reasonDraft,
  busy,
  disabled,
  onRequestDismiss,
  onCancelDismiss,
  onReasonChange,
  onConfirmDismiss,
}: {
  gap: GapRecord;
  dismissOpen: boolean;
  reasonDraft: string;
  busy: boolean;
  disabled: boolean;
  onRequestDismiss: () => void;
  onCancelDismiss: () => void;
  onReasonChange: (value: string) => void;
  onConfirmDismiss: () => void;
}): JSX.Element {
  return (
    <li class="sources-card sources-gap-card">
      <div class="sources-card-head">
        <span class="status-chip">
          {gap.fault_class} · filed {gap.detected_at.slice(0, 10)}
        </span>
        <span class="sources-card-badges">
          <GapOriginBadge origin={gap.origin} />
        </span>
      </div>

      <div class="sources-card-question">
        <span class="stat-label">Unanswered question</span>
        <p>“{gap.question}”</p>
        {gap.reference_pages.length > 0 ? (
          <p class="muted">references: {gap.reference_pages.join(", ")}</p>
        ) : null}
      </div>

      {gap.reported_reason ? (
        <div class="sources-card-source">
          <span class="stat-label">Why the wiki fell short</span>
          <p class="muted">{gap.reported_reason}</p>
        </div>
      ) : null}

      {!dismissOpen ? (
        <div class="sources-reject-actions">
          <button
            type="button"
            class="ghost"
            disabled={disabled}
            onClick={onRequestDismiss}
          >
            Dismiss…
          </button>
          <ProcessBrief process="fill.gap_dismiss" term="why close it" />
        </div>
      ) : (
        <div class="sources-reject-form">
          <label>
            <span>Reason for dismissing</span>
            <textarea
              rows={2}
              value={reasonDraft}
              disabled={busy}
              placeholder="Why is this gap not worth sourcing?"
              onInput={(event) =>
                onReasonChange((event.target as HTMLTextAreaElement).value)
              }
            />
          </label>
          <div class="sources-reject-actions">
            <button
              type="button"
              class="danger"
              disabled={busy || !reasonDraft.trim()}
              aria-busy={busy || undefined}
              onClick={onConfirmDismiss}
            >
              {verbLabel(busy, busy ? "Dismissing…" : "Confirm dismiss")}
            </button>
            <button
              type="button"
              class="ghost"
              disabled={busy}
              onClick={onCancelDismiss}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
