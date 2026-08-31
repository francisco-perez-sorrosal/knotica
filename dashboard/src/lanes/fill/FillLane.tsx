import type { JSX } from "preact";

import { LANE_STAGES } from "../../processModel";
import type { ToolClient } from "../../toolClient";
import type { WikiStatus } from "../../types";
import { LoopStrip } from "../LoopStrip";
import { IngestGateStage } from "./IngestGateStage";
import { QueueStage } from "./QueueStage";

/**
 * `FillLane` -- assembles Fill's five rail
 * stages behind one shared `<ol class="lane-rail">`: `gap`/`discover`/
 * `approve` from `QueueStage`, `ingest`/`gate` from
 * `IngestGateStage`. Mirrors `ImproveLane.tsx`/`TendLane.tsx`'s
 * "stages behind one rail" shape (M3), but is a pure assembly rather than a
 * stage-body builder: both absorbed components already render their own
 * unwrapped `<li class="lane-stage">` rows and derive their own per-stage
 * `data-state` from `status.topics[].lanes.fill` (each documented "no owned
 * `<ol>`" in their own file header), so `FillLane` owns no per-stage markup,
 * no state derivation, and makes no tool call of its own.
 *
 * `client`/`topic`/`vault`/`status`/`onStatusRefresh` reach both children
 * unmodified and identical -- one data spine, not two independently
 * configured reads. `QueueStage` reads the `pending` suggestion queue plus
 * open gaps; `IngestGateStage` self-fetches the disjoint `approved` queue.
 *
 * REGISTER OBJECTION (carried from the paired RED suite): the plan's prose
 * asks for the item
 * selected in `QueueStage`'s queue to be "threaded down to `IngestGateStage`
 * as the active item." The two components' queues are disjoint by domain
 * construction (`pending` vs `approved` suggestions) -- no suggestion is
 * ever a member of both, so there is no single item that could be "selected"
 * in one and "opened" in the other, and neither component exposes an
 * expand/select prop for a parent to lift regardless. `FillLane` therefore
 * threads one shared data spine (the closest reachable interpretation) and
 * does not attempt cross-component selection lifting.
 */
export function FillLane({
  client,
  topic,
  vault,
  status,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status?: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  // The same already-derived array both children read (`QueueStage`,
  // `IngestGateStage`) -- the strip is a projection of it, never a second read.
  const declared =
    status?.topics.find((row) => row.topic === topic)?.lanes?.fill ?? [];
  const byId = new Map(declared.map((stage) => [stage.id, stage] as const));

  return (
    <main class="pane-main fill">
      <LoopStrip
        lane="fill"
        stages={LANE_STAGES.fill.map(({ id, title }) => ({
          id,
          title,
          state: byId.get(id)?.state ?? "pending",
        }))}
      />

      <ol class="lane-rail" aria-label="fill stages">
        <QueueStage
          client={client}
          topic={topic}
          vault={vault}
          status={status}
          onStatusRefresh={onStatusRefresh}
        />
        <IngestGateStage
          client={client}
          topic={topic}
          vault={vault}
          status={status}
        />
      </ol>
    </main>
  );
}
