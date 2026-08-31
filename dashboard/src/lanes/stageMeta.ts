import type { IconName } from "../icons";
import type { LaneRailStageState } from "../types";

/**
 * Per-stage presentation copy, keyed lane-then-stage. Two
 * lanes declare a stage called `gate` and they mean different things, so a
 * flat stage-id key would silently collide -- the census test
 * (`stageMeta.test.ts`) binds these keys to `LANE_STAGES` from the generated
 * `processModel.ts`, which is the single source of truth for the stage
 * census. Only the copy lives here.
 *
 * `icon: null` is deliberate rather than a gap: the design's inventory draws
 * stage glyphs for the six Improve stages only. Every other lane's strip node
 * falls back to the glyph for its own declared *state*, which is information
 * the node has to carry anyway -- inventing eighteen more glyphs to avoid a
 * nullable field would add a fetch-free asset nobody asked for.
 */
export interface StageMeta {
  readonly icon: IconName | null;
  /** The popover's *What this is* slot. */
  readonly whatThisIs: string;
  /** The popover's *What to do next* slot. */
  readonly whatToDoNext: string;
}

/**
 * The *What the states mean* slot -- identical for every rail stage, since
 * the state vocabulary is the rail's, not any one stage's
 * (`laneRailState.ts::StageState`). Data, not JSX, so this module stays a
 * plain `.ts` the census test can import without a DOM.
 *
 * `unknown` is listed last and reads as an absence rather than a position:
 * it is what the server declares when it found no evidence either way, and
 * spelling out the difference from `pending` here is the whole point of
 * having declared a fifth word instead of reusing the fourth.
 */
export const STAGE_STATE_LEGEND: ReadonlyArray<{
  readonly state: LaneRailStageState;
  readonly icon: IconName;
  readonly meaning: string;
}> = [
  { state: "pending", icon: "state:pending", meaning: "nothing has run yet" },
  { state: "active", icon: "state:active", meaning: "running or awaiting you" },
  { state: "complete", icon: "state:complete", meaning: "this stage finished" },
  { state: "blocked", icon: "state:blocked", meaning: "a precondition failed" },
  {
    state: "unknown",
    icon: "state:unknown",
    meaning: "nothing recorded either way",
  },
];

/** The state glyph a node falls back to when its stage declares no icon. */
export const STATE_ICON: Record<LaneRailStageState, IconName> = {
  pending: "state:pending",
  active: "state:active",
  complete: "state:complete",
  blocked: "state:blocked",
  unknown: "state:unknown",
};

const LEARN: Record<string, StageMeta> = {
  source: {
    icon: null,
    whatThisIs:
      "Resolves the topic's schema and stores its source in the vault.",
    whatToDoNext:
      "Point the lane at a source; the rail advances once it is stored.",
  },
  fetch_parse: {
    icon: null,
    whatThisIs:
      "Fetches the stored source and parses it into page-sized chunks.",
    whatToDoNext:
      "Runs on its own once a source is stored. Nothing to do here.",
  },
  pages: {
    icon: null,
    whatThisIs: "Writes the parsed chunks into wiki pages under this topic.",
    whatToDoNext: "Claude writes the pages; the rail advances as they land.",
  },
  curate: {
    icon: null,
    whatThisIs: "Reviews the written pages and keeps the ones worth keeping.",
    whatToDoNext: "Open the topic in Obsidian and curate what landed.",
  },
};

const ANSWER: Record<string, StageMeta> = {
  ask: {
    icon: null,
    whatThisIs: "Puts a question to this topic's compiled program.",
    whatToDoNext: "Type a question and ask. Asking calls a model.",
  },
  cite: {
    icon: null,
    whatThisIs: "Shows which pages the answer was drawn from.",
    whatToDoNext: "Follow a citation to read the page it came from.",
  },
  react: {
    icon: null,
    whatThisIs: "Records the answer as a good or bad training example.",
    whatToDoNext: "Mark the answer good or bad, note it, or report a gap.",
  },
};

const IMPROVE: Record<string, StageMeta> = {
  instrument: {
    icon: "stage:instrument",
    whatThisIs:
      "Prepares the reviewed and held-out datasets an eval cycle reads.",
    whatToDoNext:
      "Bootstrap or review this topic's datasets, then open Observe.",
  },
  observe: {
    icon: "stage:observe",
    whatThisIs: "Runs the eval cycle and records the scalar against the trend.",
    whatToDoNext:
      "Start a cycle here. Running costs model tokens — you are asked to confirm before anything is billed.",
  },
  gate: {
    icon: "stage:gate",
    whatThisIs:
      "Reviews the pending candidate against the frozen gate baseline.",
    whatToDoNext: "Read the verdict, then accept or refuse the candidate.",
  },
  heal: {
    icon: "stage:heal",
    whatThisIs: "Runs a fresh compile after a gate refusal.",
    whatToDoNext:
      "Start a compile. Compiling is billed and two-phase — one click never bills.",
  },
  promote: {
    icon: "stage:promote",
    whatThisIs: "Merges an accepted candidate branch back into the vault.",
    whatToDoNext: "Review the branch, then promote it.",
  },
  prove: {
    icon: "stage:prove",
    whatThisIs:
      "Answers a question against the compiled program to prove the loop closed.",
    whatToDoNext: "Ask a question here and read the cited answer.",
  },
};

const FILL: Record<string, StageMeta> = {
  gap: {
    icon: null,
    whatThisIs: "Holds the gaps this topic has reported but not yet closed.",
    whatToDoNext: "Report a gap, or read the ones already filed.",
  },
  discover: {
    icon: null,
    whatThisIs: "Searches for sources that could close an open gap.",
    whatToDoNext: "Run discovery for a gap to collect candidate sources.",
  },
  approve: {
    icon: null,
    whatThisIs: "Reviews each suggested source before anything is ingested.",
    whatToDoNext: "Approve or refuse the pending suggestions.",
  },
  ingest: {
    icon: null,
    whatThisIs: "Pulls an approved source into the topic as pages.",
    whatToDoNext: "Hand the approved suggestion to Claude to ingest.",
  },
  gate: {
    icon: null,
    whatThisIs: "Checks an ingested source against the topic before it counts.",
    whatToDoNext: "Read the gate verdict for what was ingested.",
  },
};

const TEND: Record<string, StageMeta> = {
  doctor: {
    icon: null,
    whatThisIs: "Checks the vault's structure and configuration.",
    whatToDoNext: "Read the report; each finding names its own fix.",
  },
  lint: {
    icon: null,
    whatThisIs: "Checks every page in the vault against its schema.",
    whatToDoNext: "Open a violation in Obsidian and fix the page.",
  },
  okf: {
    icon: null,
    whatThisIs: "Compares the vault against the Open Knowledge Format.",
    whatToDoNext: "Run a dry run first; applying is two-phase.",
  },
  migrate: {
    icon: null,
    whatThisIs: "Moves the vault to a newer layout when one lands.",
    whatToDoNext: "Hand the migration to Claude when one is offered.",
  },
  drift: {
    icon: null,
    whatThisIs: "Reports notes that drifted from the pages they anchor to.",
    whatToDoNext:
      "Run one scan per anchor. It is never run automatically, so `not checked` is the honest resting state.",
  },
};

/** Keyed lane → stage id. `home` carries no rail, hence no stage copy. */
export const STAGE_META: Record<string, Record<string, StageMeta>> = {
  home: {},
  learn: LEARN,
  answer: ANSWER,
  improve: IMPROVE,
  fill: FILL,
  tend: TEND,
};

/** `null` for a lane/stage pair the model does not declare -- callers render
 * the row without a popover rather than inventing copy for it. */
export function stageMeta(lane: string, stageId: string): StageMeta | null {
  return STAGE_META[lane]?.[stageId] ?? null;
}
