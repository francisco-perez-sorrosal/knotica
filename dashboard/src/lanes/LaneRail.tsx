import { useState } from "preact/hooks";
import type { JSX } from "preact";

import type {
  Actor,
  LaneRail as LaneRailContract,
  LaneStage,
  StageState,
} from "./laneRailState";

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

function stageGlyph(state: StageState, index: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(index + 1);
}

export function LaneRail({ rail }: { rail: LaneRailContract }): JSX.Element {
  return (
    <ol class="lane-rail" aria-label={`${rail.lane} stages`}>
      {rail.stages.map((stage, index) => (
        <LaneStageRow
          key={stage.id}
          stage={stage}
          index={index}
          current={isCurrentStage(rail, stage, index)}
        />
      ))}
    </ol>
  );
}

function LaneStageRow({
  stage,
  index,
  current,
}: {
  stage: LaneStage;
  index: number;
  current: boolean;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <li
      class="lane-stage"
      data-state={stage.state}
      aria-current={current ? "step" : undefined}
    >
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(stage.state, index)}
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
