import { useCallback, useEffect, useState } from "preact/hooks";

import { publishOpenAnchor } from "./lanes/laneNavigation";
import type { LaneAnchor, OpenAnchor } from "./paneRouting";
import type { PaneId } from "./types";

/**
 * The single crossing point between lanes, as one module.
 *
 * `App.tsx` owns cross-lane navigation, but "owns" should not mean "has sixty
 * lines of it buried in an eight-hundred-line component". The whole mechanism
 * lives here: the URL write, the one-shot arrival, the publish that lets a
 * `ProcessOutcome` deep in a stage body reach the callback without a prop, and
 * the scroll-and-tint that orients the user on landing. `App` calls one hook
 * and threads one value.
 *
 * It sits at `src/` rather than under `lanes/` deliberately: it reads the
 * routing allowlist, and the cross-lane census interdicts a lane resolving
 * panes for itself. Navigation is app chrome, not lane behaviour.
 */

/** How long an arrived-at row keeps its border tint. Long enough to find, short
 *  enough not to become a second, competing "current" marker. */
const ARRIVAL_TINT_MS = 1_600;

function prefersReducedMotion(): boolean {
  return Boolean(
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches,
  );
}

export interface AnchorNavigation {
  /**
   * The one callback that crosses lanes. Every caller passes a `(lane, stage)`
   * pair that came out of a registry — `PROCESS_META`'s `next` anchors or
   * `ATTENTION_KIND_META`'s row anchors — both census-validated against
   * `LANE_STAGES`, so it cannot be handed a destination the process model does
   * not declare.
   */
  readonly openAnchor: OpenAnchor;
  /**
   * The destination being arrived at, for exactly one render. A lane whose
   * stage bodies are focus-gated (Improve) reads it to seed focus; every other
   * railed lane needs nothing, because the scroll below finds its row by
   * `data-anchor`.
   */
  readonly arrival: LaneAnchor | null;
}

/**
 * @param setPane the pane setter `App` already owns — passed in rather than
 *   owned here, because the pane is app state with several other writers (the
 *   tab bar, the host's `ontoolinput` channel) and moving it would leave two
 *   owners of one value.
 * @param initialArrival the destination a `?lane=&focus=` deep link named, or
 *   `null`. The URL is the third entry point into a stage, alongside a Home
 *   queue row and a registry `NEXT STEP`, and all three arrive the same way.
 */
export function useAnchorNavigation(
  setPane: (pane: PaneId) => void,
  initialArrival: LaneAnchor | null,
): AnchorNavigation {
  const [arrival, setArrival] = useState<LaneAnchor | null>(initialArrival);

  /**
   * Sets the pane, records the destination in the URL so the landing is
   * shareable and survives a reload, and publishes the one-shot arrival the
   * target lane consumes. Nothing else.
   */
  const openAnchor = useCallback(
    (lane: PaneId, stage?: string | null) => {
      setPane(lane);
      const url = new URL(window.location.href);
      url.searchParams.delete("pane");
      url.searchParams.set("lane", lane);
      if (stage) url.searchParams.set("focus", stage);
      else url.searchParams.delete("focus");
      window.history.replaceState({}, "", url);
      setArrival({ lane, stage: stage ?? null });
    },
    [setPane],
  );

  useEffect(() => {
    publishOpenAnchor(openAnchor);
    return () => publishOpenAnchor(null);
  }, [openAnchor]);

  /**
   * Landing. Scroll the row into view and tint its border for a moment.
   *
   * **Focus is not moved.** A scroll-and-tint orients without hijacking the
   * keyboard, and moving focus on arrival is the same theft the rail contract
   * forbids. The tint is decoration; the position is the carrier, so a
   * reduced-motion user loses nothing.
   *
   * Runs after the target lane's own render, which is what guarantees the row
   * exists — including the case where Improve's focus seeding is what mounted
   * the stage body in the first place. The arrival is cleared here, which is
   * what makes it one-shot: a request that survived would re-seed focus on the
   * next topic change, which is focus theft with a delay.
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

  return { openAnchor, arrival };
}
