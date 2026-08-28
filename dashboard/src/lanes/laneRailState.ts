// The pure lane-rail state machine (INTERFACE_DESIGN.md §1.2-1.4).
//
// Framework-free: no Preact import, no DOM, no fetch. Two derivation
// functions turn a lane's process position into the four-state rail
// vocabulary (`pending`/`active`/`complete`/`blocked`) so the illegal
// combination the two sibling lane panes render today (a per-stage
// independent `current`/`ready` pair, letting two stages read "in play" at
// once) is unrepresentable through this API rather than merely avoided by
// convention.
//
// `LaneRail`/`LaneStage` below are the full wire contract from §1.2,
// carried verbatim for downstream assembly code. The two derive* functions
// only ever produce a `DerivedStage` — the state-machine slice of a
// `LaneStage` (`id`/`title`/`state`/`blocked`). Populating the remaining
// per-stage fields (`fact`, `count`, `handoff`, `actor`) requires domain
// data this pure layer does not have; that assembly belongs to whichever
// code calls these functions with real stage/check data.

// `unknown` is the server's fifth word (`core/process_model.py::StageState`)
// and is deliberately not a position: an adapter that found no evidence says
// it instead of claiming `pending` ("not reached yet"). The two derive*
// functions below never *produce* it -- both are given a real position -- but
// the union admits it so an assembled rail carrying one is representable, and
// so the shell that renders it has to decide what an unknown row does.
export type StageState =
  | "pending"
  | "active"
  | "complete"
  | "blocked"
  | "unknown";

export type LaneKind = "sequence" | "checklist";

export type LaneCardinality = "singleton" | "aggregate";

export type Actor = "you" | "claude" | "system" | null;

export interface BlockedInfo {
  readonly what: string;
  readonly why: string;
  readonly fix: string;
}

// The dispatch contract (§3) has not been designed yet as of this module —
// only its presence on a stage is contracted so far. Left opaque rather
// than guessed at.
export type HandoffSpec = Readonly<Record<string, unknown>>;

export interface LaneStage {
  readonly id: string;
  readonly title: string;
  readonly state: StageState;
  readonly fact: string;
  readonly count: number | null;
  readonly blocked: BlockedInfo | null;
  readonly handoff: HandoffSpec | null;
  readonly actor: Actor;
}

export interface LaneRail {
  readonly lane: "home" | "learn" | "answer" | "improve" | "fill" | "tend";
  readonly kind: LaneKind;
  readonly cardinality: LaneCardinality;
  readonly scope: { readonly topic: string; readonly vault: string };
  readonly watermark: number | null;
  readonly outcome: { readonly state: string; readonly label: string } | null;
  readonly stages: readonly LaneStage[];
}

/** The state-machine slice of a `LaneStage` — what the two derive functions below produce. */
export interface DerivedStage {
  readonly id: string;
  readonly title: string;
  readonly state: StageState;
  readonly blocked: BlockedInfo | null;
}

export interface SequenceStageInput {
  readonly id: string;
  readonly title: string;
}

export interface ChecklistCheckInput {
  readonly id: string;
  readonly title: string;
  readonly status: "complete" | "blocked" | "pending";
  readonly reason?: BlockedInfo | null;
}

function toDerivedStage(
  id: string,
  title: string,
  state: StageState,
  blocked: BlockedInfo | null = null,
): DerivedStage {
  return { id, title, state, blocked };
}

/**
 * Derives every stage's state from one monotonic `watermark` (R1-R5,
 * INTERFACE_DESIGN.md §1.3). `watermark === null` means the lane is idle —
 * every stage renders pending, never omitted. A precondition failure at the
 * watermark stage is expressed by passing `blockedReason`; it never becomes
 * a separate position, only a modifier on the active one.
 */
export function deriveSequenceStages(
  watermark: number | null,
  stages: readonly SequenceStageInput[],
  blockedReason: BlockedInfo | null = null,
): DerivedStage[] {
  return stages.map(({ id, title }, index) => {
    if (watermark === null || index > watermark) {
      return toDerivedStage(id, title, "pending");
    }
    if (index < watermark) {
      return toDerivedStage(id, title, "complete");
    }
    return blockedReason === null
      ? toDerivedStage(id, title, "active")
      : toDerivedStage(id, title, "blocked", blockedReason);
  });
}

/**
 * Derives every check's state from its own `status` alone (C1-C3,
 * INTERFACE_DESIGN.md §1.3) — independent peers, no watermark. `activeId`
 * is UI focus, not a process position: it can only promote an otherwise
 * `pending` check to `active`. A `blocked` check keeps its remedy visible
 * and a `complete` check keeps its own state even while in focus.
 */
export function deriveChecklistStages(
  checks: readonly ChecklistCheckInput[],
  activeId: string | null = null,
): DerivedStage[] {
  return checks.map(({ id, title, status, reason }) => {
    if (status === "blocked") {
      return toDerivedStage(id, title, "blocked", reason ?? null);
    }
    if (status === "complete") {
      return toDerivedStage(id, title, "complete");
    }
    return toDerivedStage(id, title, id === activeId ? "active" : "pending");
  });
}
