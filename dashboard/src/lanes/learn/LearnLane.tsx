import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";

import type { ObsidianContext } from "../../obsidianLinks";
import { LANE_STAGES } from "../../processModel";
import type { ToolClient } from "../../toolClient";
import type { IngestActivity, IngestRun } from "../../types";
import { HandoffStage } from "../HandoffStage";
import { deriveSequenceStages, type StageState } from "../laneRailState";
import { LoopStrip } from "../LoopStrip";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";

/**
 * `LearnLane` -- the four-stage
 * `source -> fetch/parse -> pages -> curate` rail. Absorbs `IngestPane.tsx`'s
 * journal polling and monotonic watermark derivation unchanged; the rail
 * merely groups the raw 8-stage ingest journal onto four positions a human
 * follows.
 *
 * `pages` is this lane's one handoff embed (`dec-091`) -- mounted only while
 * `pages` is the active watermark (`m4DissolutionCensus.test.tsx` group (e)
 * pins exactly one static `HandoffStage` JSX tag, command "ingest", under
 * this subtree). `curate`, though also declared `handoff: true` in
 * `LANE_STAGES.learn`, mounts no `HandoffStage` of its own -- its workflow
 * runs and completes on its own journal, with no dispatched conversational
 * turn from this rail.
 *
 * `curate` is deliberately decoupled from the ingest run's own state
 * ("a separate workflow server-side ... so an un-curated ingest is
 * not stuck"): once the ingest run is terminal, `pages` reads `complete`
 * even with no curate run yet -- the one place the rail's terminal
 * (`committed page`) precedes its own last stage.
 */

const POLL_INTERVAL_MS = 1_000;

/**
 * Which Learn rail position each raw ingest journal stage folds into.
 * Mirrors `core/process_model.py::_LEARN_JOURNAL_FOLD` -- server-declared,
 * but not shipped in the generated `processModel.ts` mirror, which carries
 * only each lane's rail structure (id/title/handoff), never the journal
 * stages behind it.
 */
const JOURNAL_FOLD: Readonly<Record<string, 0 | 1 | 2>> = {
  resolve_topic: 0,
  read_schema: 0,
  store_source: 0,
  fetch: 1,
  parse: 1,
  plan: 2,
  write_page: 2,
  complete: 2,
};

/**
 * The monotonic "reached" watermark over Learn's first three rail
 * positions (`source`/`fetch_parse`/`pages`): the maximum fold-group index
 * over every raw journal stage at or before `run.stage_index`, not a point
 * lookup on `run.current_stage`. A point lookup would regress the rail
 * backwards whenever a stage folding into an earlier position (e.g.
 * `store_source` -> `source`) is reported after a stage folding into a
 * later one (e.g. `plan` -> `pages`) -- which real ingest runs do.
 */
function foldWatermark(
  run: IngestRun | null,
  pipelineStages: readonly string[],
): number | null {
  if (!run) return null;
  let watermark: number | null = null;
  for (
    let index = 0;
    index <= run.stage_index && index < pipelineStages.length;
    index += 1
  ) {
    const railIndex = JOURNAL_FOLD[pipelineStages[index]];
    if (
      railIndex !== undefined &&
      (watermark === null || railIndex > watermark)
    ) {
      watermark = railIndex;
    }
  }
  return watermark;
}

function stageGlyph(state: StageState, position: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(position);
}

function StageRow({
  stage,
  state,
  position,
  title,
  children,
}: {
  /** The `LANE_STAGES.learn` id this row renders — the anchor coordinate an
   *  `openAnchor` jump lands on. */
  stage: string;
  state: StageState;
  position: number;
  title: string;
  children: JSX.Element | Array<JSX.Element | null>;
}): JSX.Element {
  return (
    <li
      class="lane-stage"
      data-anchor={`learn:${stage}`}
      data-state={state}
      aria-current={
        state === "active" || state === "blocked" ? "step" : undefined
      }
    >
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(state, position)}
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{title}</strong>
          <span class="lane-state-label muted">{state}</span>
        </div>
        <div class="lane-stage-body">{children}</div>
      </div>
    </li>
  );
}

const INGEST_ASK =
  "Claude is writing pages into this topic from the stored source. The rail advances as pages land.";

export function LearnLane({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  // Accepted for prop-surface parity with `IngestPane.tsx` (this rail's own
  // stages carry no Obsidian links yet -- `IngestPane`'s timeline events
  // did, but the timeline itself is out of this rail's scope).
  obsidianCtx: ObsidianContext;
}): JSX.Element {
  const [activity, setActivity] = useState<IngestActivity | null>(null);

  useEffect(() => {
    if (!client) return;
    const active = client;
    let stopped = false;

    async function refresh() {
      try {
        const payload = await active.ingestActivityRead(topic, vault, "");
        if (!stopped) setActivity(payload);
      } catch {
        // Learn's rail simply keeps its last-known state on a failed poll;
        // `IngestPane.tsx`'s dedicated error banner is out of this lane's
        // rail-only scope.
      }
    }

    void refresh();
    const interval = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [client, topic, vault]);

  const ingestRun: IngestRun | null =
    activity?.runs.find((run) => run.workflow !== "curate") ?? null;
  const curateRun: IngestRun | null =
    activity?.runs.find((run) => run.workflow === "curate") ?? null;
  const pipelineStages = activity?.pipeline_stages ?? [];

  const [sourceStage, fetchParseStage, pagesStage] = deriveSequenceStages(
    foldWatermark(ingestRun, pipelineStages),
    LANE_STAGES.learn.slice(0, 3),
  );
  const curateDeclared = LANE_STAGES.learn[3];

  const pagesState: StageState = ingestRun?.terminal
    ? "complete"
    : pagesStage.state;
  const curateState: StageState = curateRun
    ? curateRun.terminal
      ? "complete"
      : "active"
    : "pending";

  return (
    <main class="pane-main learn">
      <LoopStrip
        lane="learn"
        stages={[
          {
            id: sourceStage.id,
            title: sourceStage.title,
            state: sourceStage.state,
          },
          {
            id: fetchParseStage.id,
            title: fetchParseStage.title,
            state: fetchParseStage.state,
          },
          { id: pagesStage.id, title: pagesStage.title, state: pagesState },
          {
            id: curateDeclared.id,
            title: curateDeclared.title,
            state: curateState,
          },
        ]}
      />

      <ol class="lane-rail" aria-label="learn stages">
        <StageRow
          stage={sourceStage.id}
          state={sourceStage.state}
          position={1}
          title={sourceStage.title}
        >
          <p class="muted">
            {sourceStage.state === "pending"
              ? "Waiting for a source to be resolved and stored."
              : "Topic and schema resolved; source stored to the vault."}
          </p>
        </StageRow>

        <StageRow
          stage={fetchParseStage.id}
          state={fetchParseStage.state}
          position={2}
          title={fetchParseStage.title}
        >
          <p class="muted">
            {fetchParseStage.state === "pending"
              ? "Fetch and parse run after the source is stored."
              : "Full text fetched and parsed into sections."}
          </p>
        </StageRow>

        <StageRow
          stage={pagesStage.id}
          state={pagesState}
          position={3}
          title={pagesStage.title}
        >
          {pagesState === "active" && client && ingestRun ? (
            <>
              {/* Above the shell rather than inside it: `HandoffStage` is
                  shared with Fill, and the reason *this* lane's pages are
                  written in Claude belongs to this lane. */}
              <ProcessBrief
                process="learn.ingest_dispatch"
                term="why in Claude"
              />
              <HandoffStage
                client={client}
                topic={topic}
                suggestionId={ingestRun.citation_key}
                vault={vault}
                command="ingest"
                active
                ask={INGEST_ASK}
                renderYouControl={() => null}
              />
            </>
          ) : (
            <>
              <p class="muted">
                {pagesState === "complete"
                  ? "Pages written for this run."
                  : "Pending — writes pages once fetch/parse land."}
              </p>
              {/* Only a finished run has somewhere to send you. While pages
                  are still landing the follow-up is the handoff itself. */}
              {pagesState === "complete" ? (
                <ProcessOutcome process="learn.ingest_dispatch" />
              ) : null}
            </>
          )}
        </StageRow>

        <StageRow
          stage={curateDeclared.id}
          state={curateState}
          position={4}
          title={curateDeclared.title}
        >
          <p class="muted">
            {curateState === "pending"
              ? "Pending — after the pages land. Saving an example opens its own run."
              : curateState === "complete"
                ? "Example curated."
                : "Curating a training example."}
          </p>
        </StageRow>
      </ol>

      {pagesState === "complete" ? (
        <p class="lane-terminal">Terminal: committed page</p>
      ) : null}
    </main>
  );
}
