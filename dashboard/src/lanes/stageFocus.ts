import { useState } from "preact/hooks";

import type { LaneRailStageState } from "../types";

/**
 * The client-owned **focus** axis (design §5.3) — *what the user is looking
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

/** Initial focus: whatever the server says needs attention, else nothing. */
export function initialFocus(stages: readonly FocusableStage[]): string | null {
  return (
    stages.find(
      (stage) => stage.state === "active" || stage.state === "blocked",
    )?.id ?? null
  );
}

export function useStageFocus(
  scope: string,
  stages: readonly FocusableStage[],
): StageFocus {
  const [stored, setStored] = useState<ScopedFocus>(() => ({
    scope,
    id: initialFocus(stages),
  }));

  // Deriving on scope mismatch — rather than resetting through an effect —
  // keeps the reset synchronous with the render that changed topic or vault,
  // and keeps mount to a single render pass.
  const focusedId =
    stored.scope === scope ? stored.id : initialFocus(stages);

  return {
    focusedId,
    focus: (stageId: string) => setStored({ scope, id: stageId }),
    toggleFocus: (stageId: string) =>
      setStored({ scope, id: focusedId === stageId ? null : stageId }),
  };
}
