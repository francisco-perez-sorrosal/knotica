import type { JSX } from "preact";

import { Icon } from "../icons";
import { InfoPopover } from "../InfoPopover";
import type { LaneRailStageState } from "../types";
import { LANE_META, type LaneMeta, type LaneShape } from "./laneMeta";
import { STAGE_STATE_LEGEND, STATE_ICON, stageMeta } from "./stageMeta";

/**
 * The loop visualization that sits above a lane's rail (design §3.3). Six
 * stacked boxes in document order say nothing about *shape*: the rail below
 * carries the content, this strip carries the position and whether the lane
 * closes back on itself. The redundancy is deliberate (design §8 R7) -- it
 * encodes structure a flat list cannot.
 *
 * The strip is a projection of state the lane already holds; it makes no
 * call of its own and derives no position the rail does not already declare.
 *
 * Interactivity is opt-in: a node is a real `<button>` only when the caller
 * passes `onFocus`. Without it the nodes render as inert marks rather than
 * controls that do nothing when clicked -- an affordance that lies is the
 * failure this whole redesign is answering.
 */

export interface LoopStripStage {
  readonly id: string;
  readonly title: string;
  readonly state: LaneRailStageState;
}

/** `LANE_META` keyed by `PaneId`; the strip takes a plain lane string, so the
 * lookup has to admit a miss rather than assume exhaustiveness it cannot see. */
const LANE_LOOKUP: Record<string, LaneMeta | undefined> = LANE_META;

const SHAPE_NOUN: Record<LaneShape, string> = {
  cycle: "CYCLE",
  line: "SEQUENCE",
  checks: "CHECKS",
};

const SHAPE_ADVICE: Record<LaneShape, string> = {
  cycle:
    "Work down the rail below. Prove returns to Instrument — this lane is a cycle, not a checklist.",
  line: "Work down the rail below; each stage names what it needs.",
  checks: "Each check stands alone — run whichever one you need.",
};

/** The strip's one-line narration, derived from declared state only. */
export function loopHeadline(
  lane: string,
  shape: LaneShape,
  stages: readonly LoopStripStage[],
): string {
  const prefix = `${lane.toUpperCase()} · `;
  const noun = SHAPE_NOUN[shape];
  const blocked = stages.find((stage) => stage.state === "blocked");
  if (blocked) {
    return `${prefix}${blocked.title.toUpperCase()} BLOCKED — a precondition failed`;
  }
  const active = stages.find((stage) => stage.state === "active");
  if (active) {
    return `${prefix}${active.title.toUpperCase()} ACTIVE — in progress`;
  }
  const settled =
    stages.length > 0 && stages.every((stage) => stage.state === "complete");
  if (settled) {
    return `${prefix}${noun} COMPLETE — nothing left to run`;
  }
  return `${prefix}${noun} IDLE — nothing running`;
}

/**
 * The rail's four-state vocabulary, rendered identically wherever a popover
 * explains a stage. Shared here rather than restated per call site, and held
 * as data in `stageMeta.ts` so that module stays DOM-free.
 */
export function StageStatesLegend(): JSX.Element {
  return (
    <ul class="stage-state-legend">
      {STAGE_STATE_LEGEND.map(({ state, icon, meaning }) => (
        <li key={state}>
          <Icon name={icon} size={16} />
          <strong>{state}</strong> {meaning}
        </li>
      ))}
    </ul>
  );
}

export function LoopStrip({
  lane,
  stages,
  focusedId,
  onFocus,
}: {
  lane: string;
  stages: readonly LoopStripStage[];
  /** The stage the user is looking at, when the lane owns a focus axis. */
  focusedId?: string | null;
  /** Supplied only by a lane that can act on focus; otherwise nodes are inert. */
  onFocus?: (stageId: string) => void;
}): JSX.Element | null {
  if (stages.length === 0) return null;

  const meta = LANE_LOOKUP[lane];
  const shape = meta?.shape ?? "line";

  return (
    <section class="loop-strip" data-shape={shape}>
      <div class="loop-strip-header">
        <span class="microlabel loop-strip-headline">
          {loopHeadline(lane, shape, stages)}
        </span>
        <InfoPopover
          id={`loop-strip:${lane}`}
          title={lane.toUpperCase()}
          ariaLabel={`About the ${lane} rail`}
          align="center"
          whatThisIs={meta?.blurb ?? `The ${lane} rail.`}
          whatTheStatesMean={<StageStatesLegend />}
          whatToDoNext={SHAPE_ADVICE[shape]}
        />
      </div>
      <ol class="loop-strip-track" aria-label={`${lane} loop overview`}>
        {stages.map((stage) => (
          <LoopNode
            key={stage.id}
            lane={lane}
            stage={stage}
            focused={focusedId === stage.id}
            onFocus={onFocus}
          />
        ))}
      </ol>
      {shape === "cycle" ? (
        <p class="loop-strip-arc">
          <span class="loop-strip-arc-line" aria-hidden="true" />
          <span class="microlabel">
            {stages[stages.length - 1].title} returns to {stages[0].title}
          </span>
        </p>
      ) : null}
    </section>
  );
}

function LoopNode({
  lane,
  stage,
  focused,
  onFocus,
}: {
  lane: string;
  stage: LoopStripStage;
  focused: boolean;
  onFocus?: (stageId: string) => void;
}): JSX.Element {
  const icon = stageMeta(lane, stage.id)?.icon ?? STATE_ICON[stage.state];
  const mark = onFocus ? (
    <button
      type="button"
      class="loop-node"
      aria-label={`Show ${stage.title} — ${stage.state}`}
      onClick={() => onFocus(stage.id)}
    >
      <Icon name={icon} size={20} />
    </button>
  ) : (
    <span class="loop-node" aria-hidden="true">
      <Icon name={icon} size={20} />
    </span>
  );

  return (
    <li
      class="loop-strip-item"
      data-state={stage.state}
      data-focus={focused ? "true" : "false"}
    >
      {mark}
      <span class="microlabel loop-node-title">{stage.title}</span>
      <span class="loop-node-state">{stage.state}</span>
    </li>
  );
}
