import type { JSX } from "preact";

import { LANE_STAGES } from "../processModel";
import { canOpenAnchor, openAnchor } from "./laneNavigation";
import { PROCESS_META, resolveNextAnchor } from "./processMeta";
import type { ProcessAnchor, ProcessId } from "./processMeta";

/**
 * Phases 5 and 6 — *what was done* and *what is next, and why*.
 *
 * The `NEXT STEP` block is `HealStage.tsx`'s aborted-race card generalised:
 * the same `<span class="microlabel">NEXT STEP</span>` followed by the reason
 * the destination is the destination. That card was the only place in the app
 * that answered the sixth question; this is that answer made available to
 * every process.
 *
 * **The announcement is registry-driven, not caller-driven.** A caller whose
 * server returns its own sentence renders it inside its own live region and
 * passes no `message`; this component then contributes the Next block alone,
 * so two nested `role="status"` regions never announce the same event twice.
 * A `refresh` process — one whose only visible result is a list re-reading
 * itself — has no server sentence, so the registry's `outcomeFallback` is
 * announced here instead. That is the rule that keeps a silent re-render from
 * passing as an outcome.
 *
 * **The destination is reachable, not merely named.** `Go to Improve → Gate`
 * is a quiet button that calls the single App-owned `openAnchor`, which sets
 * the pane, records the coordinate in the URL and publishes a one-shot arrival
 * the target lane consumes. A Next the user cannot reach is not a Next; it is a
 * footnote. When navigation is not published — a component rendered in
 * isolation, a test harness with no `App` — the same sentence renders as plain
 * text rather than as a control that would do nothing.
 */

export interface ProcessOutcomeProps {
  /** The registered process whose outcome this is. */
  process: ProcessId;
  /**
   * The sentence to announce. Omit when the caller already renders the
   * server's own message inside its own live region.
   */
  message?: string | null;
  /**
   * The discriminant the caller already holds (a gate decision, a verdict).
   * Resolves a `conditional` next; an unrecognised value lands on the
   * registry's fallback rather than on nothing.
   */
  discriminant?: string | null;
}

export function ProcessOutcome({
  process,
  message,
  discriminant,
}: ProcessOutcomeProps): JSX.Element {
  const meta = PROCESS_META[process];
  const announced =
    message?.trim() ||
    (meta.outcomeMode === "refresh" ? meta.outcomeFallback?.trim() : "") ||
    "";
  const anchor = resolveNextAnchor(meta.next, discriminant);

  // Phrasing content only -- `TwoPhaseOutcome` renders its children inside a
  // `<p>`, and a block element there is invalid markup the browser silently
  // reparents. The block layout comes from `display: block` in `app.css`.
  return (
    <span class="process-outcome">
      {announced ? (
        <span class="process-outcome-line" role="status">
          {announced}
        </span>
      ) : null}
      <span class="microlabel">NEXT STEP</span>
      <span class="process-outcome-next">
        {anchor ? (
          <>
            {anchor.why} <NextDestination anchor={anchor} />
          </>
        ) : (
          // `anchor` is null only for a terminal next, whose `why` is the
          // whole answer -- the loop closed, and saying so is the answer.
          meta.next.kind === "terminal" && meta.next.why
        )}
      </span>
    </span>
  );
}

/**
 * The destination itself. A button when navigation is wired, the same words as
 * plain text when it is not — never a control that silently does nothing.
 */
function NextDestination({ anchor }: { anchor: ProcessAnchor }): JSX.Element {
  const label = `Go to ${anchorLabel(anchor)}.`;
  if (!canOpenAnchor()) {
    return <span class="process-outcome-dest">{label}</span>;
  }
  return (
    <button
      type="button"
      class="process-outcome-dest process-outcome-go"
      onClick={() => openAnchor(anchor.lane, anchor.stage)}
    >
      {label}
    </button>
  );
}

/** `improve` + `promote` reads as `Improve → Promote`; a lane with no stage reads bare. */
function anchorLabel(anchor: ProcessAnchor): string {
  const lane = anchor.lane.charAt(0).toUpperCase() + anchor.lane.slice(1);
  if (anchor.stage === null) return lane;
  const title =
    LANE_STAGES[anchor.lane]?.find((stage) => stage.id === anchor.stage)
      ?.title ?? anchor.stage;
  return `${lane} → ${title}`;
}
