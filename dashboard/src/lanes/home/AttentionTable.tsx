import type { JSX } from "preact";

import type { IconName } from "../../icons";
import { Icon } from "../../icons";
import { TermHint } from "../../TermHint";
import type { OpenAnchor } from "../../paneRouting";
import type { AttentionRow, AttentionUrgency } from "../../types";
import { ATTENTION_KIND_META } from "./attentionMeta";

const URGENCY_ICON: Record<AttentionUrgency, IconName> = {
  blocked: "state:blocked",
  waiting: "state:pending",
  running: "state:running",
};

/** The ordering rule behind the leading rank column -- stated once, on the
 * Urgency column header, rather than repeated per row. */
const URGENCY_ORDER_EXPLANATION =
  "Blocked outranks waiting outranks running: stopped pipelines first, then things awaiting you, then things merely running.";

/**
 * The urgency-tinted, ranked attention queue (design §3.2) --
 * `deriveAttentionRows`'s flat, one-row-per-signal output, sorted by
 * `sortAttentionRows` before it reaches this component (rank is therefore
 * the caller's sort order, never re-derived here) and rendered as a
 * scannable table instead of the prior undifferentiated `<ul>`. Urgency is
 * never colour-alone: every row carries an icon plus the urgency word as
 * visible text (the left-border tint, driven by `data-urgency`, is a
 * reinforcing signal, not the only one). Each row's urgency label hosts a
 * `TermHint` naming why that row's `kind` is queued and what acting on it
 * unfolds (`attentionMeta.ts`).
 *
 * `[Open]`/`[Watch]` lands on the *stage* that holds the control the row is
 * about, not merely on the lane that contains it — the anchor comes from the
 * row's `kind` in `attentionMeta.ts`, which is census-validated against the
 * lane/stage model. Dropping a user at the top of a six-stage lane and letting
 * them hunt is the failure this queue exists to prevent.
 */
export function AttentionTable({
  rows,
  onOpenAnchor,
}: {
  rows: AttentionRow[];
  onOpenAnchor: OpenAnchor;
}): JSX.Element {
  return (
    <table class="attention-table">
      <caption class="microlabel">Attention queue</caption>
      <thead>
        <tr>
          <th scope="col">#</th>
          <th scope="col">
            <TermHint
              id="home:attention:urgency-order"
              term="Urgency"
              title="Queue order"
              body={URGENCY_ORDER_EXPLANATION}
            />
          </th>
          <th scope="col">Topic</th>
          <th scope="col">What needs you</th>
          <th scope="col">Lane</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => {
          const rank = index + 1;
          const meta = ATTENTION_KIND_META[row.kind];
          return (
            <tr key={`${row.topic}-${row.kind}`} data-urgency={row.urgency}>
              <td class="attention-table-rank">#{rank}</td>
              <td>
                <Icon name={URGENCY_ICON[row.urgency]} size={16} />
                <span class="attention-table-urgency-label">
                  <TermHint
                    id={`home:attention:row:${row.topic}:${row.kind}`}
                    term={row.urgency}
                    title="Why this rank"
                    body={`${meta.why} ${meta.unlocks}`}
                  />
                </span>
              </td>
              <td class="attention-table-topic">{row.topic}</td>
              <td class="muted">{row.narration}</td>
              <td class="attention-table-lane-cell">
                <span class="attention-table-lane">{row.lane}</span>
                <button
                  type="button"
                  onClick={() =>
                    onOpenAnchor(meta.anchor.lane, meta.anchor.stage)
                  }
                >
                  {row.action}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
