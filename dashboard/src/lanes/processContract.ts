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
