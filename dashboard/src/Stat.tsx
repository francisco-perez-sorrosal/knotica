import type { JSX } from "preact";

import type { SectionTone } from "./SectionCard";

export interface StatProps {
  /** May itself carry a `TermHint` when the label needs explaining (design §2.2). */
  label: JSX.Element | string;
  /**
   * `null`/`undefined`/`""` renders `—` in `--neutral` -- absence is
   * neutral, never a failure, and never rendered as `0` (design §2.2,
   * round 1 §7.3). May itself carry a `TermHint` when the value is the
   * thing needing explanation (a status word, a version string).
   */
  value: JSX.Element | string | number | null | undefined;
  /** Colours the value only, and only for a real verdict -- a bare count is never toned (design §2.2). */
  tone?: SectionTone;
}

/**
 * One label/value pair in a `StatGrid` (design §2.2). Replaces every
 * `Label: <strong>value</strong>` prose fragment with a scannable,
 * tabular-nums readout.
 */
export function Stat({ label, value, tone }: StatProps): JSX.Element {
  const hasValue = value !== null && value !== undefined && value !== "";
  return (
    <div class="stat" data-tone={hasValue ? tone : undefined}>
      <span class="stat-label microlabel">{label}</span>
      <span class="stat-value">{hasValue ? value : "—"}</span>
    </div>
  );
}

/** Auto-fit grid of `Stat`s (design §2.2) -- `minmax(7rem, 1fr)` columns. */
export function StatGrid({
  children,
}: {
  children: JSX.Element | JSX.Element[];
}): JSX.Element {
  return <div class="stat-grid">{children}</div>;
}
