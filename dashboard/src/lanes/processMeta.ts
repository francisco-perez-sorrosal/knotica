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
  | "improve.compile_run"
  | "improve.datasets_bootstrap"
  | "improve.datasets_bootstrap_train"
  | "improve.datasets_freeze"
  | "improve.arena_scorer_switch"
  | "improve.branch_promote"
  | "improve.branch_delete"
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
  | "fill.ingest_dispatch"
  | "learn.ingest_dispatch"
  | "learn.create_topic"
  | "vault.create"
  | "vault.use"
  | "tend.migrate"
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

  "improve.compile_run": {
    lane: "improve",
    stage: "heal",
    title: "Compile now",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "compileRun",
    why: "The gate refused the last candidate, so the standing program is the best this topic has and nothing improves until a fresh optimisation produces something else to measure.",
    willDo:
      "Re-optimises the prompt program against the trainset and writes the result to a new candidate branch. Billed, and the first click only arms the control. The vault's answers do not change — a candidate branch is not promoted by compiling it.",
    previewMode: "armed",
    progressMode: "busy",
    // `compile action=run` returns the branch it wrote and no sentence about
    // it; before this row a finished compile looked exactly like a click that
    // did nothing, because the only visible effect was a status re-read.
    outcomeMode: "refresh",
    outcomeFallback: "A fresh candidate branch is compiled and waiting to be measured.",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "gate",
        why: "A candidate is only worth having once it is scored against the baseline — until it is gated, nothing knows whether this compile was an improvement.",
      },
    },
  },

  // Instrument's three. All three already print what they did; what none of
  // them said was which step the numbers they moved are owed to next.
  "improve.datasets_bootstrap": {
    lane: "improve",
    stage: "instrument",
    title: "Bootstrap",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "datasetsBootstrap",
    why: "The golden pipeline starts empty, and with no candidate questions there is nothing to review and therefore nothing that can ever be frozen into a held-out set.",
    willDo:
      "Synthesises candidate questions from this topic's entity pages and writes them to the candidates file. Billed, and the first click only arms the control. It adds candidates; it never overwrites a reviewed or held-out set.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "Candidates need a human verdict before they can be frozen, and this surface has no control for that review — the numbers above are the whole of what changed here.",
    },
  },

  "improve.datasets_bootstrap_train": {
    lane: "improve",
    stage: "instrument",
    title: "Bootstrap trainset",
    spend: "billed",
    mutates: true,
    dispatch: "client",
    clientMethod: "datasetsBootstrapTrain",
    why: "A compile optimises the prompt program against the trainset, so an empty or thin trainset caps how good any compiled candidate can be no matter how often it is run.",
    willDo:
      "Reads this topic's pages and appends labelled examples to the trainset. Billed, and the first click only arms the control. The trainset stays disjoint from the held-out set — a question the model trained on measures nothing.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "heal",
        why: "A widened trainset only pays off through a compile — that is the step that reads it and turns it into a new candidate program.",
      },
    },
  },

  "improve.datasets_freeze": {
    lane: "improve",
    stage: "instrument",
    title: "Freeze golden",
    // Writes files and commits; it calls no model.
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "datasetsFreeze",
    why: "Every later comparison in this topic is made against the held-out set, so until one is frozen there is no baseline, no gate verdict and no eval scalar that means anything.",
    willDo:
      "Moves the reviewed candidates into the held-out set and commits them. Nothing is billed. Consequential rather than destructive: the frozen set becomes the stick every future scalar is measured with, and it refuses outright to freeze a set that overlaps the trainset.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "observe",
        why: "A frozen held-out set is what an eval cycle scores against — running one is how the set turns into the first number worth comparing to.",
      },
    },
  },

  "improve.arena_scorer_switch": {
    lane: "improve",
    stage: "heal",
    // Ships under two labels, one per direction: `Use eval scorer` arms the
    // spend and is armed→confirm; `Use heuristic scorer` goes back to free on
    // a single quiet click. The asymmetry is deliberate and recorded rather
    // than flattened -- going free needs no guard.
    title: "Use eval scorer",
    spend: "arms-billing",
    // Writes one key under `[loop]` in the config file, not the vault.
    mutates: false,
    dispatch: "client",
    clientMethod: "loopCadence",
    why: "The race was refused before scoring because the arena scorer and the gate baseline are not the same instrument, so no ranking between them would mean anything and every race aborts the same way until one of them changes.",
    willDo:
      "Writes the chosen scorer under `[loop]` in your config. This click bills nothing; switching to the eval scorer arms one full golden-set eval per variant on every future race. Reversible — the same control switches back.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "Both runners rebuild from config on every tick, so the scorer takes effect without a restart and nothing else is owed here.",
    },
  },

  "improve.branch_promote": {
    lane: "improve",
    stage: "promote",
    title: "Preview promote",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "branchPromote",
    why: "A candidate that cleared the baseline changes nothing while it sits on a branch — the topic keeps answering with the program it had until the branch is merged.",
    willDo:
      "Shows you exactly what merging this branch changes, and merges it only on a second, explicit click. Nothing is billed. Reversible — the merge is a normal commit.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "prove",
        why: "The scoreboard says it scores better; the probe is the only place you see whether it actually answers better, which is the claim the whole loop is making.",
      },
    },
  },

  "improve.branch_delete": {
    lane: "improve",
    stage: "promote",
    title: "Preview delete",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "branchDelete",
    why: "This candidate is not going to be merged, and an open branch nobody drops keeps presenting itself for review every time this stage is opened.",
    willDo:
      "Shows you what dropping this branch removes, and drops it only on a second, explicit click. Nothing is billed and the vault keeps the program it already had — the branch goes, the wiki does not change.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "always",
      go: {
        lane: "improve",
        stage: "heal",
        why: "Dropping the candidate leaves the topic on its last promoted program, so a fresh compile is what produces something else to try.",
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

  // The two handoffs. Both declare `external` progress and `external`
  // outcome, and the census refuses any other pairing for a `handoff`: once
  // the payload leaves for another agent's turn this surface has no channel
  // into the work, so the only honest claims it can make are that the
  // dispatch was sent, that the poll is running at its cadence, and that the
  // panel updates itself. Never "working", never "done".
  "fill.ingest_dispatch": {
    lane: "fill",
    stage: "ingest",
    title: "Open a session",
    spend: "free",
    // Nothing here writes. The dispatched `/knotica:fill` does, in Claude's
    // turn, which is exactly why this is a handoff and not a client call.
    mutates: false,
    dispatch: "handoff",
    clientMethod: null,
    why: "Only your Claude session can write into the candidate session; this surface can read that session but has no way to write a page into it.",
    willDo:
      "Sends the ingest instruction to your Claude session. Nothing is written from here — the session writes, and this list re-reads every 3 seconds as it does.",
    previewMode: "none",
    progressMode: "external",
    outcomeMode: "external",
    next: {
      kind: "always",
      go: {
        lane: "fill",
        stage: "gate",
        why: "An ingested source does not count for anything until the gate measures it against the topic and records a verdict.",
      },
    },
  },

  "learn.ingest_dispatch": {
    lane: "learn",
    stage: "pages",
    // Ships under three labels, one per capability tier: `Send to Claude`,
    // `Queue for Claude`, or -- where the host can do neither -- the copyable
    // instruction itself. One process, three affordances for one payload.
    title: "Send to Claude",
    spend: "free",
    mutates: false,
    dispatch: "handoff",
    clientMethod: null,
    why: "The run has fetched and parsed the source and is waiting on the turn that writes pages from it, and only your Claude session can take that turn.",
    willDo:
      "Sends the ingest instruction to your Claude session. Nothing is written from here — the rail advances as the session writes, and this stage re-reads the journal every second.",
    previewMode: "none",
    progressMode: "external",
    outcomeMode: "external",
    next: {
      kind: "always",
      go: {
        lane: "learn",
        stage: "curate",
        why: "A written page is not yet a training signal — curating is the separate run that turns one into an example the compiler reads.",
      },
    },
  },

  // The three chrome processes. Their triggers live in the app chrome, which
  // belongs to no lane -- so `lane`/`stage` name the lane each process serves
  // rather than the surface it is clicked on, and all three carry `stage:
  // null` to say so. It is the one place in the registry where `lane` is not
  // literally where the control is, and the alternative -- inventing a
  // seventh lane for the chrome -- would put a lane in `processModel.ts` that
  // no rail renders.
  "learn.create_topic": {
    lane: "learn",
    stage: null,
    title: "Create",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "createTopic",
    why: "A knowledge base is normally several topics and creating a vault seeds only the first, so without this the dashboard could start a wiki and then never grow it.",
    willDo:
      "Creates the topic and its schema in the active vault and selects it. Nothing is billed. It adds a topic; it never touches the ones already there.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The topic is created in this vault and selected.",
    next: {
      kind: "always",
      go: {
        lane: "learn",
        stage: "source",
        why: "A topic with no source has nothing to answer from — storing one is the first step that gives it any content at all.",
      },
    },
  },

  "vault.create": {
    lane: "learn",
    stage: null,
    // Ships under the same `Create` label as the topic form beside it; they
    // are two forms in one drawer, told apart by which fields they carry.
    title: "Create",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "vaultCreate",
    why: "A wiki lives in its own git repository at a path you choose, and until one exists there is nowhere for a topic, a page, a note or an eval to be written.",
    willDo:
      "Creates the vault at the path you gave, seeds its first topic if you named one, and switches the dashboard to it. Nothing is billed. It writes a new repository and never touches a vault that already exists.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The knowledge base is created and the dashboard is now reading it.",
    next: {
      kind: "always",
      go: {
        lane: "learn",
        stage: "source",
        why: "A new knowledge base has no pages — storing a source is what gives it something to be a wiki about.",
      },
    },
  },

  "vault.use": {
    // Per-vault and mechanical, which is Tend's half of the lane
    // discriminator; the switch itself sits in the chrome.
    lane: "tend",
    stage: null,
    title: "Switch vault",
    spend: "free",
    // Rewrites which vault is active in the config; no wiki content changes.
    mutates: false,
    dispatch: "client",
    clientMethod: "vaultUse",
    why: "Every number on screen — the baseline, the queues, the drift count, the flywheel chip — was read from one vault, and switching replaces all of them at once with another vault's without saying so.",
    willDo:
      "Points the server at the vault you picked and re-reads everything for it. Nothing is billed and no wiki content changes — switching back is the same control.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The dashboard is now reading the vault you picked; every number on screen belongs to it.",
    next: {
      kind: "always",
      go: {
        lane: "home",
        // Home is the only lane with no stages, and the only surface that
        // re-reads every topic at once -- which is exactly what a vault
        // switch invalidates.
        stage: null,
        why: "Everything you knew a moment ago belonged to the other vault, and Home is the one surface that re-reads every topic so you can see what this one is asking for.",
      },
    },
  },

  "tend.migrate": {
    lane: "tend",
    stage: "migrate",
    title: "Copy",
    spend: "free",
    mutates: true,
    // The one `cli` row. There is no MCP surface for migrate, so the honest
    // affordance is the command itself -- and like a handoff, this surface
    // cannot see the run and may not claim to.
    dispatch: "cli",
    clientMethod: null,
    why: "A vault's on-disk layout can fall behind the schema this build expects, and one that has fallen behind is reported against by every later check without any of them being able to fix it.",
    willDo:
      "Nothing from here: this copies the CLI dry run for you to paste. The dry run only reports what would change — applying it is a second command you run yourself.",
    previewMode: "dry-run",
    progressMode: "external",
    outcomeMode: "external",
    next: {
      kind: "always",
      go: {
        lane: "tend",
        stage: "doctor",
        why: "A migration rewrites the layout every other check reads, so the health report is what confirms it landed clean.",
      },
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
