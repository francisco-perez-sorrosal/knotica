import type { ToolClient } from "../toolClient";
import type { PaneId } from "../types";

/**
 * The six-phase process lifecycle contract, engraved as data.
 *
 * > Every user-triggered process answers six questions, in this order, and
 * > each answer is an artifact — never a habit:
 * > **Surface → Justify → Preview → Progress → Outcome → Next**.
 *
 * A *process* is a user-triggered action that spends money, mutates the
 * vault, or hands work to another agent. Reads are not processes; navigation
 * is not a process.
 *
 * This is the `laneMeta.ts` / `stageMeta.ts` / `attentionMeta.ts` precedent
 * applied to actions: a typed record over a closed union carrying
 * presentation-and-contract copy the server payload does not carry, with a
 * runtime census (`__tests__/processMeta.test.ts`) that fails when the union
 * and the record drift. The census is closed over the `ToolClient` method
 * surface — the dashboard's only route to the server — so registry
 * completeness holds by construction rather than by diligence.
 *
 * `ProcessId` grows one migration wave at a time. That is deliberate and it
 * is what keeps two census groups simultaneously true at every commit: G1
 * (every client method is accounted for, the not-yet-migrated ones in a
 * named, shrinking fixture) and G2 (every registered process is actually
 * wired to a surface). A registry that declared all its rows up front would
 * satisfy the first and fail the second for the whole of the migration.
 */
export type ProcessId =
  | "improve.run_eval"
  | "improve.gate_candidate"
  | "improve.probe"
  | "answer.ask"
  | "answer.curate_example"
  | "answer.note_capture"
  | "answer.gap_report"
  | "fill.gapfill_discover"
  | "fill.suggestion_approve"
  | "fill.suggestion_reject"
  | "fill.suggestion_defer"
  | "fill.suggestion_withdraw"
  | "tend.okf_repair"
  | "tend.note_reanchor"
  | "tend.note_detach"
  | "tend.note_promote"
  | "tend.note_archive";

/** What a click costs. `arms-billing` bills nothing now and bills later. */
export type Spend = "billed" | "arms-billing" | "free";

/**
 * Phase 3 — how much ceremony the preview gets.
 *
 * - `nonce` — server-minted free quote (model, threads, estimate) then a
 *   second confirm that redeems it. The only mode that may bill on a
 *   server-quoted number (`TwoPhaseAction.tsx`).
 * - `armed` — client-side arm→confirm; the armed label names the
 *   consequence in words (`ArmedButton.tsx`).
 * - `dry-run` — the server computes the change and returns it; the user
 *   reads a diff before applying.
 * - `acknowledged` — **named exception**: billed, single click. The cost is
 *   stated in `willDo` (machine-required, see the census) and on a visible
 *   chip, and the user has accepted that this class sits below the
 *   confirmation threshold. Only ever where a decision put it.
 * - `none` — free **and** reversible. `willDo` alone is the preview.
 */
export type PreviewMode = "nonce" | "armed" | "dry-run" | "acknowledged" | "none";

/**
 * Phase 4 — how progress is carried. Declared, never defaulted: `instant` is
 * a decision on the record, an omission is not.
 */
export type ProgressMode = "busy" | "poll" | "external" | "instant";

/**
 * Phase 5 — how the outcome is reported. `refresh` requires an
 * `outcomeFallback` sentence: a silent re-render is not an outcome.
 */
export type OutcomeMode = "result" | "verdict" | "refresh" | "external";

/** How the work leaves the dashboard. */
export type Dispatch = "client" | "handoff" | "cli";

/**
 * A reachable destination: a lane, and a stage that lane actually declares in
 * the generated `processModel.ts` mirror of `core/process_model.py`, plus why
 * to go there. The census pins both halves, which is what makes Phase 6
 * structural rather than prose — a follow-up cannot point at a destination
 * the process model does not declare.
 */
export interface ProcessAnchor {
  readonly lane: PaneId;
  /**
   * A stage id in `LANE_STAGES[lane]`, or `null` for the lane's own landing
   * surface. Only `home` legitimately has no stages.
   */
  readonly stage: string | null;
  readonly why: string;
}

export interface ProcessBranch {
  /**
   * The discriminant value this branch answers. The caller already holds it;
   * a branch may never require a new fetch.
   */
  readonly when: string;
  readonly go: ProcessAnchor;
}

/**
 * Phase 6, as a discriminated union with **no null member**. `terminal` is an
 * answer; absence is not. The type system, not a test, is what makes a dead
 * end unwritable.
 *
 * `conditional`'s `fallback` is not defensive padding: a discriminant the
 * client has not seen before (a new verdict value, a server ahead of the
 * client) must still name a destination, because the failure mode this
 * contract exists to kill is a dead end.
 */
export type ProcessNext =
  | { readonly kind: "always"; readonly go: ProcessAnchor }
  | {
      readonly kind: "conditional";
      readonly branches: readonly ProcessBranch[];
      readonly fallback: ProcessAnchor;
    }
  | { readonly kind: "terminal"; readonly why: string };

export interface ProcessMeta {
  /** Where the trigger lives. `home` is forbidden: Home routes, never acts. */
  readonly lane: PaneId;
  readonly stage: string | null;
  /**
   * The trigger's own visible label, byte-identical to what ships. Lets a
   * test assert copy and registry agree without a second literal.
   */
  readonly title: string;
  readonly spend: Spend;
  readonly mutates: boolean;
  readonly dispatch: Dispatch;
  /**
   * The single `ToolClient` method this process reaches the server with, or
   * `null` for `handoff`/`cli`. One method may back several processes
   * (`suggestionsReview` backs four fill verbs); the census requires at least
   * one row per mutating method, not exactly one.
   *
   * Typed as `keyof ToolClient` rather than `string` so a renamed client
   * method is a compile error in the registry, before the census even runs.
   */
  readonly clientMethod: keyof ToolClient | null;
  /** Phase 2 — the cause, in server semantics. One sentence, ends with a period. */
  readonly why: string;
  /** Phase 3 — the effect on the vault, the spend, and the reversibility. */
  readonly willDo: string;
  readonly previewMode: PreviewMode;
  readonly progressMode: ProgressMode;
  readonly outcomeMode: OutcomeMode;
  /**
   * Phase 5 — required when `outcomeMode === "refresh"`: the sentence that
   * keeps a silent re-render from passing as an outcome.
   */
  readonly outcomeFallback?: string;
  /** Phase 6 — never nullable. */
  readonly next: ProcessNext;
}

/**
 * The registry.
 *
 * Migration order is deliberate: the best-served processes migrate first, so
 * the schema is validated against a known-good before it is asked to fill a
 * void. `improve.gate_candidate` seeds it — it already has a server-quoted
 * preview, a verdict outcome with an honest fallback, and it is the canonical
 * `conditional` Next, so the hardest shape is proven on row one rather than
 * on row thirty.
 */
export const PROCESS_META: Record<ProcessId, ProcessMeta> = {
  "improve.run_eval": {
    lane: "improve",
    stage: "observe",
    title: "Run eval now (billed)",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "loopRunEval",
    why: "The trend only moves when a cycle actually scores this topic against the held-out set; without a fresh scalar there is nothing for the gate to compare a candidate to.",
    willDo:
      "Runs one eval cycle on the held-out set and records the scalar as a new generation. Billed — you see the worker, the judge, the thread count and the estimate before anything runs. Nothing in the wiki changes.",
    previewMode: "nonce",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "gate",
        why: "A fresh scalar is only worth having if a candidate is measured against it — gating is what turns the number into a verdict.",
      },
    },
  },

  "improve.gate_candidate": {
    lane: "improve",
    stage: "gate",
    title: "Gate next candidate now",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "loopRunOnce",
    why: "A compiled candidate is waiting and nothing downstream moves until it is measured against the frozen baseline.",
    willDo:
      "Runs one eval cycle against the gate baseline and stamps a verdict on the candidate branch. Billed — you see the estimate before anything runs. The verdict is recorded, not applied.",
    previewMode: "nonce",
    progressMode: "busy",
    outcomeMode: "verdict",
    next: {
      kind: "conditional",
      // The discriminant is `LoopOnceResult.decision`, whose vocabulary is
      // `core/loop_state.py::LoopDecision` — `pass` / `fail` / `none`. It is
      // *not* the gapfill gate's `merged` / `refused`; the two gates are
      // different instruments and share no verdict word.
      branches: [
        {
          when: "pass",
          go: {
            lane: "improve",
            stage: "promote",
            why: "The candidate cleared the baseline — merge it into the vault so answers actually improve.",
          },
        },
        {
          when: "fail",
          go: {
            lane: "improve",
            stage: "heal",
            why: "The candidate did not clear the baseline; a fresh compile is how you try again.",
          },
        },
      ],
      fallback: {
        lane: "improve",
        stage: "observe",
        why: "Nothing was decided this tick — read the trend and the raw cycle before spending again.",
      },
    },
  },

  // The two single-click spends. Both call the same billed `query` tool, and
  // both are `acknowledged` -- the one named exception to "billed actions are
  // two-phase". The exemption is deliberately *legible*: it appears in exactly
  // these two rows out of the whole registry, the census machine-requires each
  // to state its cost in `willDo`, and flipping either to `armed` is a
  // two-field edit with no schema change. Nothing about the shipped behaviour
  // changed when they were written down.
  "improve.probe": {
    lane: "improve",
    stage: "prove",
    title: "Probe it",
    spend: "billed",
    // `query` reads pages and answers; it writes nothing back.
    mutates: false,
    dispatch: "client",
    clientMethod: "query",
    why: "A promoted program is only actually better if it answers better, and a scoreboard delta cannot tell you whether the questions this topic exists for improved.",
    willDo:
      "Asks the compiled program this question now and renders the answer beside the one you pinned. It costs tokens on a single click; the answer is not stored, so nothing in the vault changes.",
    previewMode: "acknowledged",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "The flywheel closed here: you have read what the compiled program actually says, which is the only evidence the loop was worth running.",
    },
  },

  "answer.ask": {
    lane: "answer",
    stage: "ask",
    title: "Ask",
    spend: "billed",
    mutates: false,
    dispatch: "client",
    clientMethod: "query",
    why: "You have a question this topic is supposed to cover, and until it is asked nothing knows whether the wiki can cite an answer or is missing the pages for one.",
    willDo:
      "Sends the question to the server's model, which answers only from this topic's pages and cites them. It costs tokens on a single click. Nothing is written to the vault, so there is nothing to undo.",
    previewMode: "acknowledged",
    progressMode: "busy",
    // The answer and its citations are the server's own payload, rendered by
    // `AnswerCard` in the Cite stage.
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "answer",
        stage: "react",
        why: "An answer nobody reacts to teaches the loop nothing — reacting is what turns it into training signal, a note, or a filed gap.",
      },
    },
  },

  // Answer's three React verbs. All free, all one commit, and all previously
  // silent past a single line of text -- each now names the lane its signal
  // actually feeds, which is the whole reason the verb exists.
  "answer.curate_example": {
    lane: "answer",
    stage: "react",
    // Ships under two labels, `Good example` and `Bad example`: one process,
    // one server action, the verdict is the argument.
    title: "Good example",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "curateExample",
    why: "The compiler only learns from answers a human has judged, and an answer nobody grades is a signal it never sees.",
    willDo:
      "Writes this question, this answer and your verdict into the topic's curated examples, in one commit. Nothing is billed. Reversible — a curated example can be re-reviewed or dropped.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The answer is kept as a curated example.",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "instrument",
        why: "A graded example is only worth anything once it is in the trainset a compile reads — Instrument is where that set is built and its overlaps are checked.",
      },
    },
  },

  "answer.note_capture": {
    lane: "answer",
    stage: "react",
    title: "Note it",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "noteCapture",
    why: "Something here is worth keeping that is not a grade on the answer, and a thought left uncaptured at the moment you had it is one the wiki never gets.",
    willDo:
      "Captures the question and answer as a reflection note anchored to the pages it cited, in one commit. Nothing is billed. Reversible — a note can be re-anchored, detached or archived.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The answer is captured as a note on this topic.",
    next: {
      kind: "always",
      go: {
        lane: "tend",
        stage: "drift",
        why: "A note is only as good as its anchor, and Drift is where anchors whose pages have moved get re-pointed before the citation goes stale.",
      },
    },
  },

  "answer.gap_report": {
    lane: "answer",
    stage: "react",
    title: "Report gap",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "gapReport",
    why: "The answer fell short of what this topic should be able to say, and an unreported gap is one nothing will ever go looking for sources against.",
    willDo:
      "Files an open gap record against this topic, in one commit. Nothing is fetched and nothing is billed. Reversible — a filed gap can be resolved or dismissed.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "A gap is filed against this topic.",
    next: {
      kind: "always",
      go: {
        lane: "fill",
        stage: "discover",
        why: "Discovery is what turns a filed gap into ranked candidate sources — a gap nobody discovers against stays open forever.",
      },
    },
  },

  "fill.gapfill_discover": {
    lane: "fill",
    stage: "discover",
    title: "Discover sources…",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "gapfillDiscover",
    why: "Gaps are filed against this topic and no source has been proposed for any of them, so the queue is stalled at its first step and nothing downstream has anything to triage.",
    willDo:
      "Searches the configured provider for candidate sources for the open gaps and stages the ones that rank as suggestions. Billed — you see how many gaps would drain and the estimate before anything runs. Nothing is written into the wiki itself.",
    previewMode: "nonce",
    progressMode: "busy",
    // The drained/staged counts come back on the confirm and the caller
    // prints them; the registry adds where they lead.
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "fill",
        stage: "approve",
        why: "A staged suggestion is a proposal, not a decision — nothing is ingested until someone approves it in the queue below.",
      },
    },
  },

  // The four triage verbs, all on `fill action=suggestions_review`. The
  // lifecycle statement they share -- what a decision does to the record --
  // is the state machine in `core/gapfill.py::_TARGET_STATUS`, and each row's
  // Next is what that landing status leaves owed.
  "fill.suggestion_approve": {
    lane: "fill",
    stage: "approve",
    title: "✓ Approve",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "suggestionsReview",
    why: "This source was ranked for one of your open gaps, and a candidate nobody accepts is a gap that stays open no matter how good the source was.",
    willDo:
      "Moves the record to approved and queues an ingest instruction for your next Claude session, in one commit. Nothing is billed and nothing is written into the wiki yet. Reversible — Withdraw puts it back in the queue.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "fill",
        stage: "ingest",
        why: "Approving queues the instruction; only the session that runs it writes the pages, and Ingest is where that handoff is opened.",
      },
    },
  },

  "fill.suggestion_reject": {
    lane: "fill",
    stage: "approve",
    title: "Confirm reject",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "suggestionsReview",
    why: "This source does not answer the question it was ranked for, and leaving it in the queue costs every later triage pass the same judgement again.",
    willDo:
      "Records the rejection with the reason you wrote, in one commit, and drops the record out of the pending queue. Nothing is billed. The gap it was proposed for stays open.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "fill",
        stage: "discover",
        why: "Rejecting removes the candidate, never the gap it was proposed for — another discovery run is what proposes a different source for it.",
      },
    },
  },

  "fill.suggestion_defer": {
    lane: "fill",
    stage: "approve",
    title: "⧗ Defer",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "suggestionsReview",
    why: "This one needs a judgement you are not ready to make, and guessing at it is worse than parking it where you can find it again.",
    willDo:
      "Moves the record to deferred in one commit — out of the pending list, still approvable or rejectable later. Nothing is billed and the gap stays open.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "Deferring decides nothing on purpose — the record waits here until you come back to it, and nothing downstream moves in the meantime.",
    },
  },

  "fill.suggestion_withdraw": {
    lane: "fill",
    stage: "approve",
    title: "Withdraw",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "suggestionsReview",
    why: "An approval you have changed your mind about would otherwise have to be released by claiming an ingest that never happened, which writes a false record.",
    willDo:
      "Returns the record to pending in one commit, asserting nothing about whether it was ingested. Nothing is billed. Fully reversible — it is the undo for an approval.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "The record is back where it started, in this queue, still owed the decision you withdrew — there is nowhere else to be.",
    },
  },

  // The mutating Tend family. Every one of these is free, dry-run-first, and
  // reports itself by re-reading the surface it changed -- which is why they
  // are the first `outcomeFallback` users: without that sentence a successful
  // repair and a no-op look identical.
  "tend.okf_repair": {
    lane: "tend",
    stage: "okf",
    title: "Repair apply",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "okfRepair",
    why: "The vault has drifted from the Open Knowledge Format, and a vault that does not match the format cannot be read by anything that expects it.",
    willDo:
      "Rewrites the files the dry run listed and records one git commit. Nothing is billed. Reversible — the commit is a normal one you can revert.",
    previewMode: "dry-run",
    progressMode: "busy",
    // `OkfRepairResult` carries `files_changed` / `notes` / `commit_sha` and
    // no message field, so there is no server sentence to render verbatim.
    // The registry supplies the sentence instead of inventing a server change.
    outcomeMode: "refresh",
    outcomeFallback:
      "The repair ran and its changed files and commit are listed below.",
    next: {
      kind: "always",
      go: {
        lane: "tend",
        stage: "lint",
        why: "A repair rewrote pages, and lint is what proves those pages still validate against their schemas.",
      },
    },
  },

  "tend.note_reanchor": {
    lane: "tend",
    stage: "drift",
    // Ships under two labels -- `Re-anchor here` when the passage resolved
    // itself, `Re-anchor to selected` when you pick among alternatives. Same
    // process, same server action; the second is the first with a target.
    title: "Re-anchor here",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesReanchor",
    why: "The page moved under this note's anchor, so the note now points at text that is no longer there and cites something nobody can find.",
    willDo:
      "Repoints the anchor at the passage you chose and commits the note. Nothing is billed. Reversible — re-anchoring again moves it back.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The anchor now points at the passage you chose.",
    next: {
      kind: "terminal",
      why: "The note cites live text again — nothing downstream was waiting on it.",
    },
  },

  "tend.note_detach": {
    lane: "tend",
    stage: "drift",
    title: "Detach",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesDetach",
    why: "The passage this note pinned is gone rather than moved, so there is nothing left to re-anchor to and the broken anchor will be reported for as long as it exists.",
    willDo:
      "Removes the anchor and keeps the note itself, in one commit. Nothing is billed. Reversible in the sense that the note survives — the anchor does not, and re-pinning it is manual.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The anchor is gone; the note itself is kept.",
    next: {
      kind: "terminal",
      why: "The note stands on its own now and no longer claims a page it cannot point at.",
    },
  },

  "tend.note_promote": {
    lane: "tend",
    stage: "drift",
    title: "Promote…",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesPromote",
    why: "This note carries something the wiki should act on, and a note nobody promotes stays an annotation forever.",
    willDo:
      "Writes the note into the destination you pick — a training example or a filed gap — in one commit, after showing you the resolved question and grounding pages. Nothing is billed.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The note was written into the destination you picked.",
    next: {
      kind: "conditional",
      // The discriminant is the dialog's own `target` state -- already held by
      // the caller, never re-fetched.
      branches: [
        {
          when: "trainset",
          go: {
            lane: "improve",
            stage: "instrument",
            why: "A training example only matters once it is in the trainset a compile reads — that is where it turns into a better prompt.",
          },
        },
        {
          when: "gap",
          go: {
            lane: "fill",
            stage: "discover",
            why: "A filed gap that nobody discovers against stays open forever; discovery is what proposes sources to close it.",
          },
        },
      ],
      fallback: {
        lane: "tend",
        stage: "drift",
        why: "The destination was not one this build recognises — read the note's anchors here before deciding again.",
      },
    },
  },

  "tend.note_archive": {
    lane: "tend",
    stage: "drift",
    title: "Archive",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesArchive",
    why: "This note has been dealt with or has stopped being true, and leaving it in the live set means every later drift scan re-reports it.",
    willDo:
      "Moves the note out of the live set and commits it. Nothing is billed. Reversible — an archived note is kept, not deleted.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The note is archived and out of the live set.",
    next: {
      kind: "terminal",
      why: "The note is settled — archiving is how a note stops asking for attention.",
    },
  },
};

/**
 * Resolve a `conditional` next against a discriminant the caller already
 * holds. An unknown or absent discriminant lands on the fallback rather than
 * on nothing: naming a destination is the contract, and a dead end is the
 * failure mode it exists to kill.
 */
export function resolveNextAnchor(
  next: ProcessNext,
  discriminant?: string | null,
): ProcessAnchor | null {
  if (next.kind === "terminal") return null;
  if (next.kind === "always") return next.go;
  const branch = next.branches.find((candidate) => candidate.when === discriminant);
  return branch ? branch.go : next.fallback;
}
