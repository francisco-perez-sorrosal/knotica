import type { JSX } from "preact";

import { TermHint } from "../TermHint";
import { PROCESS_META } from "./processMeta";
import type { ProcessId, Spend } from "./processMeta";

/**
 * Phases 2 and 3 of the lifecycle contract — *why is this necessary* and
 * *what is going to happen, and what will it cost* — rendered from the
 * registry beside the trigger they describe.
 *
 * Built on `TermHint` rather than `InfoPopover` because the contract names
 * its own two slots and `InfoPopover`'s are fixed ("What this is" / "What to
 * do next"); labelling a cause as "what to do next" would be a mislabel, and
 * the whole point of the registry is that the six answers keep their names.
 * The single-open overlay signal is `TermHint`'s, so this inherits the house
 * "at most one overlay open" rule for free and adds no overlay system.
 *
 * The spend chip stays a **sibling of the trigger, never a child** — the
 * existing rule that keeps a button's accessible name from absorbing its
 * price tag. Callers replace their inline `<span class="chip cost">` with
 * this component rather than rendering both.
 */

const SPEND_CHIP: Record<Spend, string | null> = {
  billed: "billed",
  "arms-billing": "arms billing",
  free: null,
};

export interface ProcessBriefProps {
  /** The registered process this trigger runs. */
  process: ProcessId;
  /**
   * The visible link text. A *pointer label*, not lifecycle copy — it says
   * which control the brief annotates, and only needs overriding where two
   * briefs share an action row and "why this" would give both the same
   * accessible name.
   */
  term?: string;
  /** Static positioning variant, forwarded to `TermHint`. */
  align?: "start" | "end";
}

export function ProcessBrief({
  process,
  term = "why this",
  align = "start",
}: ProcessBriefProps): JSX.Element {
  const meta = PROCESS_META[process];
  const chip = SPEND_CHIP[meta.spend];

  return (
    <span class="process-brief">
      {chip ? <span class="chip cost">{chip}</span> : null}
      <TermHint
        id={`process-brief-${process}`}
        term={term}
        title={meta.title}
        align={align}
        body={
          <>
            <span class="microlabel process-brief-slot">Why this is necessary</span>
            <span class="process-brief-text">{meta.why}</span>
            <span class="microlabel process-brief-slot">What it will do</span>
            <span class="process-brief-text">{meta.willDo}</span>
          </>
        }
      />
    </span>
  );
}
