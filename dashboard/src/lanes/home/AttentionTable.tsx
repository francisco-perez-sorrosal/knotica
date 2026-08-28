import type { JSX } from "preact";

import type { IconName } from "../../icons";
import { Icon } from "../../icons";
import type { AttentionRow, AttentionUrgency, PaneId } from "../../types";

const URGENCY_ICON: Record<AttentionUrgency, IconName> = {
  blocked: "state:blocked",
  waiting: "state:pending",
  running: "state:running",
};

/**
 * The urgency-tinted attention queue (design §3.2) -- `deriveAttentionRows`'s
 * flat, one-row-per-signal output rendered as a scannable table instead of
 * the prior undifferentiated `<ul>`. Urgency is never colour-alone: every
 * row carries an icon plus the urgency word as visible text (the left-border
 * tint, driven by `data-urgency`, is a reinforcing signal, not the only one).
 */
export function AttentionTable({
  rows,
  onOpenLane,
}: {
  rows: AttentionRow[];
  onOpenLane: (lane: PaneId) => void;
}): JSX.Element {
  return (
    <table class="attention-table">
      <caption class="microlabel">Attention queue</caption>
      <thead>
        <tr>
          <th scope="col">Urgency</th>
          <th scope="col">Topic</th>
          <th scope="col">What needs you</th>
          <th scope="col">Lane</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr
            key={`${row.topic}-${row.urgency}-${index}`}
            data-urgency={row.urgency}
          >
            <td>
              <Icon name={URGENCY_ICON[row.urgency]} size={16} />
              <span class="attention-table-urgency-label">{row.urgency}</span>
            </td>
            <td class="attention-table-topic">{row.topic}</td>
            <td class="muted">{row.narration}</td>
            <td class="attention-table-lane-cell">
              <span class="attention-table-lane">{row.lane}</span>
              <button type="button" onClick={() => onOpenLane(row.lane)}>
                {row.action}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
