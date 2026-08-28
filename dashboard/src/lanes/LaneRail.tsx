import { useState } from "preact/hooks";
import type { JSX } from "preact";

import { Icon } from "../icons";
import { InfoPopover } from "../InfoPopover";
import type {
  Actor,
  LaneRail as LaneRailContract,
  LaneStage,
} from "./laneRailState";
import { LoopStrip, StageStatesLegend } from "./LoopStrip";
import { STATE_ICON, stageMeta } from "./stageMeta";

/**
 * The generic lane-rail shell (`INTERFACE_DESIGN.md §1.2`, `§1.5`) — renders
 * an already-assembled `LaneRail` object with no domain logic of its own.
 * Both rail `kind`s (`sequence`, `checklist`) share one rendering: "current"
 * is a position concept for `sequence` (the watermark index, whether its
 * state is `active` or `blocked` — R3), and a focus concept for `checklist`
 * (whichever check's own `state` is `active` — C2). `count` and `outcome`
 * are deliberately not rendered here — cardinality-aware presentation is a
 * later per-lane assembly concern (`§1.4`), not this shell's job.
 */

const ACTOR_LABEL: Record<Exclude<Actor, null>, string> = {
  you: "You",
  claude: "Claude",
  system: "System",
};

function isCurrentStage(
  rail: LaneRailContract,
  stage: LaneStage,
  index: number,
): boolean {
  if (rail.kind === "checklist") {
    return stage.state === "active";
  }
  return rail.watermark !== null && rail.watermark === index;
}

/**
 * `focusedId` is the client-owned *focus* axis (design §5.3) — what the user
 * is looking at — kept strictly orthogonal to the server-declared `state`.
 * `aria-current="step"` stays bound to the declared watermark alone; focus
 * surfaces as `data-focus` and the disclosure's `aria-expanded`.
 */
export function LaneRail({
  rail,
  focusedId,
  onFocus,
}: {
  rail: LaneRailContract;
  focusedId?: string | null;
  onFocus?: (stageId: string) => void;
}): JSX.Element {
  return (
    <div class="lane-rail-shell">
      <LoopStrip
        lane={rail.lane}
        stages={rail.stages.map(({ id, title, state }) => ({
          id,
          title,
          state,
        }))}
        focusedId={focusedId}
        onFocus={onFocus}
      />
      <ol class="lane-rail" aria-label={`${rail.lane} stages`}>
        {rail.stages.map((stage, index) => (
          <LaneStageRow
            key={stage.id}
            lane={rail.lane}
            stage={stage}
            current={isCurrentStage(rail, stage, index)}
            focused={focusedId === stage.id}
          />
        ))}
      </ol>
    </div>
  );
}

function LaneStageRow({
  lane,
  stage,
  current,
  focused,
}: {
  lane: string;
  stage: LaneStage;
  current: boolean;
  focused: boolean;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const meta = stageMeta(lane, stage.id);

  return (
    <li
      class="lane-stage"
      data-state={stage.state}
      data-focus={focused ? "true" : "false"}
      aria-current={current ? "step" : undefined}
    >
      <span class="lane-stage-index" aria-hidden="true">
        <Icon name={meta?.icon ?? STATE_ICON[stage.state]} size={16} />
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{stage.title}</strong>
          <span class="lane-state-label muted">{stage.state}</span>
          {stage.actor ? (
            <span class="lane-stage-actor muted">
              {ACTOR_LABEL[stage.actor]}
            </span>
          ) : null}
          {meta ? (
            <InfoPopover
              id={`stage:${lane}:${stage.id}`}
              title={stage.title}
              ariaLabel={`About ${stage.title}`}
              whatThisIs={meta.whatThisIs}
              whatTheStatesMean={<StageStatesLegend />}
              whatToDoNext={meta.whatToDoNext}
            />
          ) : null}
        </div>
        <LaneStageBody
          stage={stage}
          expanded={expanded}
          onToggleExpanded={() => setExpanded((wasExpanded) => !wasExpanded)}
        />
      </div>
    </li>
  );
}

function LaneStageBody({
  stage,
  expanded,
  onToggleExpanded,
}: {
  stage: LaneStage;
  expanded: boolean;
  onToggleExpanded: () => void;
}): JSX.Element {
  if (stage.state === "blocked") {
    return (
      <div class="lane-stage-body">
        <p class="lane-stage-remedy">
          <strong>What:</strong> {stage.blocked?.what} <strong>Why:</strong>{" "}
          {stage.blocked?.why} <strong>Fix:</strong> {stage.blocked?.fix}
        </p>
        <LaneStageHandoff handoff={stage.handoff} />
      </div>
    );
  }

  if (stage.state === "pending") {
    return (
      <div class="lane-stage-body">
        <p class="muted">{stage.fact}</p>
        <LaneStageHandoff handoff={stage.handoff} />
      </div>
    );
  }

  // active / complete: interactive per §1.5, one disclosure at the top level.
  return (
    <div class="lane-stage-body">
      {stage.fact ? <p class="lane-stage-fact">{stage.fact}</p> : null}
      <button
        type="button"
        class="lane-stage-disclosure"
        aria-expanded={expanded}
        onClick={onToggleExpanded}
      >
        <span class="lane-disclosure-icon" aria-hidden="true">
          ▸
        </span>
        <span class="sr-only">
          {expanded ? "Hide details" : "Show details"}
        </span>
      </button>
      <LaneStageHandoff handoff={stage.handoff} />
    </div>
  );
}

function LaneStageHandoff({
  handoff,
}: {
  handoff: LaneStage["handoff"];
}): JSX.Element | null {
  if (!handoff) return null;
  return (
    <div class="lane-stage-handoff" data-testid="lane-stage-handoff">
      A handoff is available for this stage.
    </div>
  );
}
