import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import { startVisibilityPausedPoll } from "../visibilityPausedPoll";
import type { ToolClient } from "../../toolClient";
import type { AttentionRow, AttentionStatus, PaneId } from "../../types";
import { deriveAttentionRows } from "./attentionRows";

/**
 * `HomeLane` (`INTERFACE_DESIGN.md §2.1`, `dec-092`) -- the cross-topic
 * attention inbox. No `LaneRail`, no per-stage state: this is a flat list of
 * actionable rows plus a success state, not a process rail.
 *
 * Self-fetches `client.wikiStatus("", vault, "attention")` on mount and on
 * every tick of its own `startVisibilityPausedPoll` (`§4.2` rule 3, `dec-092`)
 * at 10s -- independent of `App.tsx`'s 2s `view="summary"` poll every other
 * lane's rail reads from. Home is cross-topic, so it owns its own read.
 *
 * `onOpenLane` is the one legitimate `onOpen*`-shaped prop left in the tree
 * (`§2.0` clause 3): every row's `[Open]`/`[Watch]` button calls it with the
 * row's own `lane` and nothing else -- Home is the router, every other lane
 * may only narrate.
 *
 * The drift row renders unconditionally, default-collapsed to a single line
 * with a `[Check]` affordance -- resolving note anchors is the one cost
 * `attention` does not pay unconditionally (`§4.2` rule 2). Wiring the
 * affordance's click behavior is a declared non-goal of this step; it
 * renders with no handler.
 */
const ATTENTION_POLL_MS = 10_000;

export function HomeLane({
  client,
  vault,
  onOpenLane,
}: {
  client: ToolClient | null;
  vault: string;
  onOpenLane: (lane: PaneId) => void;
}): JSX.Element {
  const [attention, setAttention] = useState<AttentionStatus | null>(null);

  useEffect(() => {
    if (!client) return;
    let cancelled = false;

    function fetchAttention() {
      client!
        .wikiStatus("", vault, "attention")
        .then((next) => {
          // `wikiStatus`'s declared return type is `WikiStatus`, but
          // `view="attention"` returns the disjoint `AttentionStatus` shape
          // at runtime -- the same declared-shape-mismatch cast
          // `ImproveLane.tsx` uses for `ObserveStageStatus`.
          if (!cancelled) setAttention(next as unknown as AttentionStatus);
        })
        .catch(() => {
          // Home is a best-effort inbox: a failed poll leaves the last
          // known rows on screen rather than surfacing an error banner.
        });
    }

    fetchAttention();
    const stop = startVisibilityPausedPoll(fetchAttention, ATTENTION_POLL_MS);
    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, vault]);

  const rows = attention ? deriveAttentionRows(attention) : [];

  return (
    <main class="pane-main home">
      {rows.length === 0 ? (
        <p class="home-empty">Nothing needs you.</p>
      ) : (
        <ul class="home-attention-list">
          {rows.map((row, index) => (
            <AttentionRowView
              key={`${row.topic}-${row.urgency}-${index}`}
              row={row}
              onOpenLane={onOpenLane}
            />
          ))}
        </ul>
      )}
      <DriftRow />
    </main>
  );
}

function AttentionRowView({
  row,
  onOpenLane,
}: {
  row: AttentionRow;
  onOpenLane: (lane: PaneId) => void;
}): JSX.Element {
  return (
    <li class="home-attention-row" data-urgency={row.urgency}>
      <span class="home-attention-topic">{row.topic}</span>
      <p class="home-attention-narration muted">{row.narration}</p>
      <button type="button" onClick={() => onOpenLane(row.lane)}>
        {row.action}
      </button>
    </li>
  );
}

function DriftRow(): JSX.Element {
  return (
    <p class="home-drift-row muted">
      Note drift -- not checked (one scan per anchor).{" "}
      <button type="button" class="ghost">
        Check
      </button>
    </p>
  );
}
