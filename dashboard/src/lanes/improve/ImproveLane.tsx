import type { JSX } from "preact";

import type { ObsidianContext } from "../../obsidianLinks";
import type { ToolClient } from "../../toolClient";
import type {
  LaneRailStageState,
  LaneRailStageStatus,
  MetricsWindow,
  WikiStatus,
} from "../../types";
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

function stageGlyph(state: LaneRailStageState, position: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(position);
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

  return (
    <main class="pane-main improve">
      <ol class="lane-rail" aria-label="improve stages">
        {STAGE_ORDER.map((id, index) => (
          <ImproveStageRow
            key={id}
            id={id}
            position={index + 1}
            declared={byId.get(id) ?? null}
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
  position,
  declared,
  client,
  topic,
  vault,
  status,
  metrics,
  obsidianCtx,
  onStatusRefresh,
}: {
  id: StageId;
  position: number;
  declared: LaneRailStageStatus | null;
  client: ToolClient | null;
  topic: string;
  vault: string;
  status: WikiStatus | null;
  metrics: MetricsWindow | null;
  obsidianCtx: ObsidianContext;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const state: LaneRailStageState = declared?.state ?? "pending";
  const isCurrent = state === "active" || state === "blocked";

  return (
    <li
      class="lane-stage"
      data-state={state}
      aria-current={isCurrent ? "step" : undefined}
    >
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(state, position)}
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{STAGE_TITLE[id]}</strong>
          <span class="lane-state-label muted">{state}</span>
        </div>
        <div class="lane-stage-body">
          {isCurrent ? (
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
