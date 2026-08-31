import { useEffect, useState } from "preact/hooks";

import type { LaneRailStageState } from "../types";

/**
 * The client-owned **focus** axis — *what the user is looking
 * at* — held strictly orthogonal to the server-declared **state** —  *where
 * the process is*. `aria-current="step"` stays bound to state alone; focus
 * surfaces only as `data-focus` and the disclosure's `aria-expanded`.
 *
 * The idea is not new: `laneRailState.ts::deriveChecklistStages` already
 * documents this exact distinction for checklists ("`activeId` is UI focus,
 * not a process position"). This generalises it from checklists to sequences.
 *
 * Two behaviours are load-bearing and both fall out of deriving rather than
 * synchronising:
 *
 *   - **Focus is never stolen.** Once the server declares a new `active`
 *     stage, a user reading Gate is not yanked to Observe — the initial focus
 *     is computed once per scope and only the user moves it afterwards.
 *     (A stage the server declares `active`/`blocked` still mounts its body
 *     regardless of focus; that is the render matrix's first row, not focus.)
 *   - **Focus resets on topic or vault change.** Handled by keying the stored
 *     value to its scope and re-deriving when the scope changes, rather than
 *     by a `useEffect` that would fire a second render on mount.
 *
 * **Arrival requests are the one thing that may seed focus, and they are
 * one-shot.** When the user follows an anchor — a Home queue row, a registry
 * `NEXT STEP`, a `?lane=&focus=` deep link — `App.tsx` publishes the requested
 * stage for exactly one render and then clears it. A request that persisted
 * would re-seed focus on every topic change, which *is* focus theft, only
 * delayed. Nothing on a poll path ever produces a request: the app's 2s status
 * poll can flip which stage the server declares `active` and still not move
 * the user, because `requested` stays `null` throughout.
 */

export interface FocusableStage {
  readonly id: string;
  readonly state: LaneRailStageState;
}

export interface StageFocus {
  /** The stage the user is looking at, or `null` for "nothing opened yet". */
  readonly focusedId: string | null;
  /** Open a stage (loop-strip node, or a rail row's disclosure). */
  readonly focus: (stageId: string) => void;
  /** Open a stage, or close it when it is already the focused one. */
  readonly toggleFocus: (stageId: string) => void;
}

interface ScopedFocus {
  readonly scope: string;
  readonly id: string | null;
}

/**
 * Initial focus: an honoured arrival request, else whatever the server says
 * needs attention, else nothing.
 *
 * A `requested` stage the lane does not declare is ignored rather than stored —
 * the same degrade-never-error ruling `resolveAnchor` applies to a bad `?focus=`:
 * a coordinate this build does not recognise must not cost the user the landing.
 */
export function initialFocus(
  stages: readonly FocusableStage[],
  requested?: string | null,
): string | null {
  if (requested && stages.some((stage) => stage.id === requested)) {
    return requested;
  }
  return (
    stages.find(
      (stage) => stage.state === "active" || stage.state === "blocked",
    )?.id ?? null
  );
}

export function useStageFocus(
  scope: string,
  stages: readonly FocusableStage[],
  requested?: string | null,
): StageFocus {
  const [stored, setStored] = useState<ScopedFocus>(() => ({
    scope,
    id: initialFocus(stages, requested),
  }));

  // Deriving on scope mismatch — rather than resetting through an effect —
  // keeps the reset synchronous with the render that changed topic or vault,
  // and keeps mount to a single render pass.
  const focusedId =
    stored.scope === scope ? stored.id : initialFocus(stages, requested);

  // The initializer above covers arrival *into a freshly mounted lane*, which
  // is what a pane switch produces. It cannot cover an anchor followed while
  // already standing in the target lane (a registry `NEXT STEP` from Observe to
  // Gate), because the hook is already mounted and its initializer will never
  // run again. This effect is that second case and only that case: it is gated
  // on a non-null request, so no poll and no re-render can reach it.
  const honoured =
    requested && stages.some((stage) => stage.id === requested)
      ? requested
      : null;
  useEffect(() => {
    if (honoured && honoured !== focusedId) setStored({ scope, id: honoured });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [honoured, scope]);

  return {
    focusedId,
    focus: (stageId: string) => setStored({ scope, id: stageId }),
    toggleFocus: (stageId: string) =>
      setStored({ scope, id: focusedId === stageId ? null : stageId }),
  };
}
