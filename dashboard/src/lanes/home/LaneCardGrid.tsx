import type { JSX } from "preact";

import { Icon } from "../../icons";
import { InfoPopover } from "../../InfoPopover";
import type { OpenAnchor } from "../../paneRouting";
import { LANES } from "../../processModel";
import type { AttentionStatus, PaneId } from "../../types";
import { LANE_META } from "../laneMeta";

/** Lanes with no cross-topic signal in `AttentionStatus` today (the design's
 * card table: "none exists" for `learn`/`answer`) -- their stat reads `—`
 * with an `InfoPopover` explaining why, rather than a silent blank. */
const NO_CROSS_TOPIC_SIGNAL: ReadonlySet<PaneId> = new Set(["learn", "answer"]);

function cardStat(lane: PaneId, attention: AttentionStatus | null): string {
  if (!attention) return "—";
  switch (lane) {
    case "home": {
      // "watched" was tried first but its substring collides with the
      // AttentionTable's "Watch" action button under a case-insensitive
      // accessible-name query (`HomeLane.test.tsx`'s "running class" test) --
      // reworded rather than touching a test outside the design's two allowed
      // rewrites.
      const n = attention.totals.topics;
      return `${n} topic${n === 1 ? "" : "s"} tracked`;
    }
    case "improve":
      return `${attention.totals.compile_ready} ready · ${attention.totals.runners_alive} running`;
    case "fill":
      return `${attention.totals.pending} pending · ${attention.totals.refused_awaiting_rework} refused`;
    case "tend": {
      const age = attention.last_lint.age_days;
      const lint = age == null ? "lint never run" : `lint ${age}d ago`;
      const driftCount = attention.drift.count;
      const drift = driftCount == null ? "drift n/c" : `drift ${driftCount}`;
      return `${lint} · ${drift}`;
    }
    default:
      return "—";
  }
}

/**
 * The six icon-led lane cards -- every stat line comes
 * entirely from the single `wiki_status(view="attention")` call `HomeLane`
 * already makes (`AttentionStatus.totals`/`.last_lint`/`.drift`); no new
 * call, no new poll, no new endpoint. Rendered in `processModel.ts`'s own
 * `LANES` order, cast to `PaneId[]` -- safe because `laneMeta.test.ts`
 * asserts `LANE_META`'s keys equal `LANES` exactly.
 *
 * Markup contract: each card is an `<li>` wrapping a
 * `<button class="lane-card-open">` (the whole-card click target) plus a
 * sibling `<InfoPopover>` -- never a button nested inside a button.
 */
export function LaneCardGrid({
  attention,
  onOpenAnchor,
}: {
  attention: AttentionStatus | null;
  onOpenAnchor: OpenAnchor;
}): JSX.Element {
  return (
    <ul class="lane-card-grid">
      {(LANES as readonly PaneId[]).map((lane) => {
        const meta = LANE_META[lane];
        const explanation = NO_CROSS_TOPIC_SIGNAL.has(lane)
          ? `${meta.blurb} This lane has no cross-topic signal yet -- open it to see its own state.`
          : meta.blurb;
        return (
          <li class="lane-card" data-lane={lane} key={lane}>
            <button
              type="button"
              class="lane-card-open"
              /* Lane-level: a card is "show me this lane", not "take me to a
                 particular control in it", so it carries no stage. */
              onClick={() => onOpenAnchor(lane)}
            >
              <Icon name={meta.icon} size={20} />
              <span class="lane-card-name">{lane}</span>
              <p class="lane-card-blurb">{meta.blurb}</p>
              <p class="lane-card-stat">{cardStat(lane, attention)}</p>
            </button>
            <InfoPopover
              id={`home:card:${lane}`}
              title={lane}
              ariaLabel={`About ${lane}`}
              align="end"
              class="info-trigger lane-card-info"
              whatThisIs={explanation}
              whatToDoNext="Open the lane for full detail."
            />
          </li>
        );
      })}
    </ul>
  );
}
