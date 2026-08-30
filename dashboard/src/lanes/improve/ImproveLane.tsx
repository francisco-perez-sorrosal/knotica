import type { JSX } from "preact";

import type { ObsidianContext } from "../../obsidianLinks";
import type { ToolClient } from "../../toolClient";
import type {
  LaneRailStageState,
  LaneRailStageStatus,
  MetricsWindow,
  WikiStatus,
} from "../../types";
import { Icon } from "../../icons";
import { InfoPopover } from "../../InfoPopover";
import { LoopStrip, StageStatesLegend } from "../LoopStrip";
import { useStageFocus } from "../stageFocus";
import { STATE_ICON, stageMeta } from "../stageMeta";
import { GateStage } from "./GateStage";
import { HealStage } from "./HealStage";
import { InstrumentStage } from "./InstrumentStage";
import { ObserveStage } from "./ObserveStage";
import { PromoteStage } from "./PromoteStage";
import { ProveStage } from "./ProveStage";

/**
 * `ImproveLane` (`INTERFACE_DESIGN.md §2.4`) — the six-stage `improve` rail
 * (`instrument`/`observe`/`gate`/`heal`/`promote`/`prove`) assembled behind
 * one navigable list, replacing the four independent tabbed panes
 * (`DatasetsPane`, `LoopPane`, `ArenaPane`, `AskPane`) this lane absorbs.
 *
 * **Server is the one source of truth for rail position** (`core/status.py`
 * / `core/status_lanes.py`, landed in M2): each per-topic `wiki_status` row
 * carries `lanes.improve`, an already-derived `{id, state, reason}` array in
 * rail order. This assembly reads that array directly and renders each row's
 * declared `state` verbatim — it does **not** recompute a watermark index
 * and re-derive through `laneRailState.deriveSequenceStages`. Re-deriving
 * would require inferring the watermark position back out of a state array
 * the server already fully resolved (and cannot even distinguish idle
 * from terminal without extra special-casing — both have no `active`/
 * `blocked` entry), which is strictly more code for identical output. This
 * is a declared, behavior-preserving deviation from the plan's literal
 * `deriveSequenceStages` wording, in favor of the "one data spine, states
 * arrive as already-derived facts" principle the paired RED suite pins.
 *
 * Progressive disclosure (`§2.4` rule 4): only the watermark stage (whose
 * declared state is `active` or `blocked`) mounts its real, interactive
 * body. Every other stage — `pending` or `complete` — renders a one-line
 * summary instead, never a disabled control. This is why `ImproveLane`
 * cannot reuse the generic `LaneRail.tsx` shell unchanged: that shell always
 * renders a disclosure toggle for `active`/`complete` rows, whereas this
 * lane needs to swap in one of six different real components at exactly one
 * position. The `.lane-stage`/`aria-current="step"` markup contract is kept
 * identical to `LaneRail.tsx`/`TendLane.tsx` by hand, matching both's own
 * "Class contract" (`§1.5`).
 *
 * `status`/`metrics` arrive as props from the app-level poll (mirroring
 * `LoopPane.tsx`'s own shape) — this lane never calls `client.wikiStatus`
 * itself, so there is exactly one reader of that payload.
 */

const STAGE_ORDER = [
  "instrument",
  "observe",
  "gate",
  "heal",
  "promote",
  "prove",
] as const;
type StageId = (typeof STAGE_ORDER)[number];

const STAGE_TITLE: Record<StageId, string> = {
  instrument: "Instrument",
  observe: "Observe",
  gate: "Gate",
  heal: "Heal",
  promote: "Promote",
  prove: "Prove",
};

const STAGE_SUMMARY: Record<StageId, string> = {
  instrument: "Prepares the reviewed/held-out datasets ahead of an eval cycle.",
  observe: "Runs the eval cycle and watches the scalar trend.",
  gate: "Reviews the pending candidate against the gate baseline.",
  heal: "Runs a fresh compile after a gate refusal.",
  promote: "Needs a branch to promote.",
  prove: "Answers a question against the compiled program.",
};

/** The DOM id a loop-strip node scrolls to when it takes focus (§7.2). */
function rowDomId(id: string): string {
  return `improve-stage-${id}`;
}

function stagePrecondition(
  state: LaneRailStageState,
  id: StageId,
  reason: string | null,
): string {
  if (state === "blocked" && reason) return reason;
  if (state === "complete") return "Complete.";
  return STAGE_SUMMARY[id];
}

export function ImproveLane({
  client,
  topic,
  vault,
  status,
  metrics,
  obsidianCtx,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
  obsidianCtx: ObsidianContext;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const declared =
    status?.topics.find((row) => row.topic === topic)?.lanes?.improve ?? [];
  const byId = new Map(declared.map((stage) => [stage.id, stage] as const));
  const stages = STAGE_ORDER.map((id) => ({
    id,
    title: STAGE_TITLE[id],
    state: byId.get(id)?.state ?? "pending",
  }));

  // Focus is per topic+vault: switching either is a different process, so the
  // stage the user had open no longer means anything (§7.2).
  const { focusedId, focus, toggleFocus } = useStageFocus(
    `${vault}/${topic}`,
    stages,
  );

  function focusFromStrip(stageId: string): void {
    focus(stageId);
    // Optional-call rather than a ref per row: `scrollIntoView` is absent in
    // jsdom, and the row is already in the DOM when the node is clicked.
    document
      .getElementById(rowDomId(stageId))
      ?.scrollIntoView?.({ block: "nearest" });
  }

  return (
    <main class="pane-main improve">
      <LoopStrip
        lane="improve"
        stages={stages}
        focusedId={focusedId}
        onFocus={focusFromStrip}
      />
      <ol class="lane-rail" aria-label="improve stages">
        {STAGE_ORDER.map((id, index) => (
          <ImproveStageRow
            key={id}
            id={id}
            declared={byId.get(id) ?? null}
            focused={focusedId === id}
            /* The "start here" cue belongs to the first row only, and only
               while nothing at all is open (§7.2). */
            startHere={focusedId === null && index === 0}
            onToggleFocus={() => toggleFocus(id)}
            client={client}
            topic={topic}
            vault={vault}
            status={status}
            metrics={metrics}
            obsidianCtx={obsidianCtx}
            onStatusRefresh={onStatusRefresh}
          />
        ))}
      </ol>
    </main>
  );
}

function ImproveStageRow({
  id,
  declared,
  focused,
  startHere,
  onToggleFocus,
  client,
  topic,
  vault,
  status,
  metrics,
  obsidianCtx,
  onStatusRefresh,
}: {
  id: StageId;
  declared: LaneRailStageStatus | null;
  focused: boolean;
  startHere: boolean;
  onToggleFocus: () => void;
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
  obsidianCtx: ObsidianContext;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const state: LaneRailStageState = declared?.state ?? "pending";
  // The server's axis. `aria-current="step"` is bound to this and nothing
  // else — focus must never move the process marker (§5.3).
  const isDeclaredCurrent = state === "active" || state === "blocked";
  // The §5.3 render matrix: a declared-current stage is always open; a
  // pending/complete stage opens only when the user focuses it.
  const open = isDeclaredCurrent || focused;
  const meta = stageMeta("improve", id);

  return (
    <li
      id={rowDomId(id)}
      class="lane-stage"
      data-state={state}
      data-focus={focused ? "true" : "false"}
      aria-current={isDeclaredCurrent ? "step" : undefined}
    >
      <span class="lane-stage-index" aria-hidden="true">
        <Icon name={meta?.icon ?? STATE_ICON[state]} size={16} />
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{STAGE_TITLE[id]}</strong>
          <span class="lane-state-label muted">{state}</span>
          {startHere ? <span class="lane-stage-cue">Start here</span> : null}
          {meta ? (
            <InfoPopover
              id={`stage:improve:${id}`}
              title={STAGE_TITLE[id]}
              ariaLabel={`About ${STAGE_TITLE[id]}`}
              whatThisIs={meta.whatThisIs}
              whatTheStatesMean={<StageStatesLegend />}
              whatToDoNext={meta.whatToDoNext}
            />
          ) : null}
          {isDeclaredCurrent ? null : (
            <button
              type="button"
              class="lane-stage-disclosure"
              aria-expanded={focused}
              onClick={onToggleFocus}
            >
              <span class="lane-disclosure-icon" aria-hidden="true">
                <Icon name="chevron-right" size={16} />
              </span>
              <span class="sr-only">
                {focused
                  ? `Close ${STAGE_TITLE[id]}`
                  : `Open ${STAGE_TITLE[id]}`}
              </span>
            </button>
          )}
        </div>
        <div class="lane-stage-body">
          {open ? (
            <>
              {state === "blocked" && declared?.reason ? (
                <p class="lane-stage-remedy">{declared.reason}</p>
              ) : null}
              {meta ? (
                <p class="lane-stage-explainer muted">{meta.whatThisIs}</p>
              ) : null}
              <ImproveStageBody
                id={id}
                client={client}
                topic={topic}
                vault={vault}
                status={status}
                metrics={metrics}
                obsidianCtx={obsidianCtx}
                onStatusRefresh={onStatusRefresh}
              />
            </>
          ) : (
            <p class="muted">
              {stagePrecondition(state, id, declared?.reason ?? null)}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

/** `ObserveStage.tsx` narrows `status` to only the fields it renders --
 * assignable from the full `WikiStatus` at runtime (the real payload always
 * carries every field the narrower shape names), but not structurally
 * assignable at the type level since a couple of `WikiStatus.loop`'s fields
 * are optional where `ObserveStage`'s own shape declares them required.
 * Extracted via `Parameters` rather than exporting a new type from
 * `ObserveStage.tsx`, which is outside this step's declared `Files`. */
type ObserveStageStatus = Parameters<typeof ObserveStage>[0]["status"];

function ImproveStageBody({
  id,
  client,
  topic,
  vault,
  status,
  metrics,
  obsidianCtx,
  onStatusRefresh,
}: {
  id: StageId;
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
  obsidianCtx: ObsidianContext;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  switch (id) {
    case "instrument":
      return <InstrumentStage client={client} topic={topic} vault={vault} />;
    case "observe":
      return (
        <ObserveStage
          client={client}
          topic={topic}
          vault={vault}
          status={status as unknown as ObserveStageStatus}
          metrics={metrics}
        />
      );
    case "gate":
      return (
        <GateStage
          client={client}
          topic={topic}
          vault={vault}
          status={status}
          onStatusRefresh={onStatusRefresh}
        />
      );
    case "heal":
      return (
        <HealStage
          client={client}
          topic={topic}
          vault={vault}
          status={status}
          onStatusRefresh={onStatusRefresh}
        />
      );
    case "promote":
      return (
        <PromoteStage
          client={client}
          topic={topic}
          vault={vault}
          status={status}
          onStatusRefresh={onStatusRefresh}
        />
      );
    case "prove":
      return (
        <ProveStage
          client={client}
          topic={topic}
          vault={vault}
          status={status}
          obsidianCtx={obsidianCtx}
        />
      );
  }
}
