import type { ProcessId, ProcessMeta } from "../processContract";

/**
 * Fill's seven: the billed discovery drain, the gap dismissal, the four
 * triage verbs, and the ingest handoff. (Reopening a dismissed gap stays an
 * MCP/CLI verb — the dashboard lists open gaps only, so a reopen control
 * would act on rows the page cannot show.)
 *
 * Keyed by `Extract<ProcessId, "fill.*">` rather than by a hand-written union:
 * the `Record` is exhaustive over exactly the ids in this namespace, so a new
 * `fill.*` id added to `ProcessId` is a compile error here until its row is
 * written, and a row belonging to another namespace cannot be filed here by
 * accident. The id namespace is the split axis because rows change together
 * per lane -- which is how every migration wave was scoped.
 */
export const FILL_PROCESSES: Record<
  Extract<ProcessId, `fill.${string}`>,
  ProcessMeta
> = {

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
  "fill.gap_dismiss": {
    lane: "fill",
    stage: "gap",
    title: "Confirm dismiss",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "reviewGap",
    why: "This gap is not worth sourcing — an existing page answers it, or the question is out of scope — and while it sits open every discovery drain re-searches it and every triage pass re-reads it.",
    willDo:
      "Marks the gap dismissed with the reason you wrote and, in the same commit, closes its still-open suggestions as rejected so nothing is stranded waiting on a question nobody wants answered. Reversible from MCP/CLI (review_gap decision=reopen); re-draining then re-proposes sources.",
    previewMode: "armed",
    progressMode: "busy",
    outcomeMode: "result",
    next: {
      kind: "terminal",
      why: "Dismissing closes the thread on purpose — the gap and its queue rows are settled together, and nothing downstream is owed anything.",
    },
  },
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
};
