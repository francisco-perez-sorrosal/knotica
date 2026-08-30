import { signal } from "@preact/signals";

import type { OpenAnchor } from "../paneRouting";
import type { PaneId } from "../types";

/**
 * The one place a lane can reach `App.tsx`'s `openAnchor` without a prop.
 *
 * `App` owns the callback — it is the only thing that can set the pane, write
 * the URL and publish an arrival — and registers it here on mount. This module
 * is a *seam*, not a second owner: it holds exactly one function, set by
 * exactly one component, and `openAnchor` is a no-op until `App` publishes.
 *
 * Why a module-level signal rather than a threaded prop: `ProcessOutcome`
 * renders inside roughly twenty stage bodies, five of them three components
 * deep. Threading one callback through all of them would put a cross-lane prop
 * on every intermediate component — the precise coupling M4's "no lane invents
 * its own cross-lane prop" rule exists to prevent. One published callback is
 * the opposite of that: one owner, one name, one validated allowlist. It is the
 * same shape `infoPopoverState.ts` already uses for the app's other
 * cross-component singleton.
 *
 * A signal rather than a plain module variable so a component that rendered
 * before `App`'s effect ran re-renders when the callback arrives, instead of
 * silently keeping a dead affordance on screen.
 */
const published = signal<OpenAnchor | null>(null);

/** Called by `App.tsx` alone. Passing `null` unpublishes (unmount). */
export function publishOpenAnchor(open: OpenAnchor | null): void {
  published.value = open;
}

/**
 * Whether navigation is wired. Read during render so the caller can offer
 * prose instead of a control it cannot honour — an affordance that does
 * nothing is worse than no affordance.
 */
export function canOpenAnchor(): boolean {
  return published.value !== null;
}

/** Navigate. Silently does nothing when nothing is published. */
export function openAnchor(lane: PaneId, stage?: string | null): void {
  published.value?.(lane, stage);
}
