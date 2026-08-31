import type { ProcessId, ProcessMeta } from "../processContract";

/**
 * Answer's four: one billed question and the three free signals it can produce.
 *
 * Keyed by `Extract<ProcessId, "answer.*">` rather than by a hand-written union:
 * the `Record` is exhaustive over exactly the ids in this namespace, so a new
 * `answer.*` id added to `ProcessId` is a compile error here until its row is
 * written, and a row belonging to another namespace cannot be filed here by
 * accident. The id namespace is the split axis because rows change together
 * per lane -- which is how every migration wave was scoped.
 */
export const ANSWER_PROCESSES: Record<
  Extract<ProcessId, `answer.${string}`>,
  ProcessMeta
> = {

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
      "Sends the question to the server's model, which answers only from this topic's pages and cites them. It costs tokens, so only a second, explicit click sends it. Nothing is written to the vault, so there is nothing to undo.",
    previewMode: "armed",
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
};
