import type { JSX } from "preact";

import type { IconName } from "./icons";
import { Icon } from "./icons";
import type { SectionTone } from "./SectionCard";

export interface StateListRow {
  /** Stable key -- typically the item's own id (branch name, variant id). */
  readonly id: string;
  /**
   * Opaque per-item state, rendered only as `data-state` (CSS/test hook).
   * Distinct from the rail's `LaneRailStageState`: each `StateList`
   * consumer (arena variants, gate candidates, doctor/lint rows) owns its
   * own state vocabulary and resolves its own `icon`, which keeps this
   * primitive reusable across all of them (design §2.4).
   */
  readonly state: string;
  readonly icon: IconName;
  readonly name: JSX.Element | string;
  /** The visible state word -- never colour alone (round 1 §2.5, one level deeper). */
  readonly stateLabel: string;
  readonly tone?: SectionTone;
  /** `null`/`undefined`/`""` renders `—`, matching `Stat`'s absence convention. */
  readonly value?: JSX.Element | string | number | null;
  /** A quiet, right-aligned row action -- a `PromptDiff` toggle, a copy button. Never a primary button. */
  readonly action?: JSX.Element;
}

export interface StateListProps {
  label: string;
  rows: ReadonlyArray<StateListRow>;
}

/**
 * The multi-item live readout primitive (design §2.4). Replaces every
 * `<strong>name</strong><span>state</span>` fragment -- arena variants,
 * gate candidates, compile trials, doctor/lint rows -- with a tabular row:
 * icon, name, toned state chip, right-aligned value, optional quiet action.
 */
export function StateList({ label, rows }: StateListProps): JSX.Element {
  return (
    <ul class="state-list" aria-label={label}>
      {rows.map((row) => {
        const hasValue =
          row.value !== null && row.value !== undefined && row.value !== "";
        return (
          <li key={row.id} class="state-list-row" data-state={row.state}>
            <span class="state-list-icon" aria-hidden="true">
              <Icon name={row.icon} size={16} />
            </span>
            <span class="state-list-name">{row.name}</span>
            <span class="chip" data-tone={row.tone}>
              {row.stateLabel}
            </span>
            <span class="state-list-value">{hasValue ? row.value : "—"}</span>
            {row.action ? (
              <span class="state-list-action">{row.action}</span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
