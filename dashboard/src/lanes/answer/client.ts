/**
 * Answer's half of `ToolClient`: ask a question, then react to the answer.
 *
 * The three reactions -- curate the exchange into the trainset, capture a
 * personal note, report a gap -- are all dispatched from Answer's `react`
 * stage, so their client methods live here. Their *payload types* live with
 * the lanes that own the resulting objects (`tend` owns the note family,
 * `fill` owns the gap queue), which is why those two arrive through the root
 * barrel rather than from `./types`; the same cohesion ruling `td-057`'s
 * types half recorded, seen from the calling side.
 */

import type {
  GapReportResult,
  NoteCaptureResult,
  NoteIntent,
} from "../../types";
import { LLM_CALL_TIMEOUT_MS, type ToolCallGroup } from "../../toolClientCore";

import type { QueryAnswer } from "./types";

export interface AnswerToolCalls {
  query(topic: string, question: string, vault?: string): Promise<QueryAnswer>;
  curateExample(
    topic: string,
    query: string,
    answer: string,
    verdict: "good" | "bad",
    pagesUsed?: string[],
    vault?: string,
  ): Promise<Record<string, unknown>>;
  noteCapture(
    topic: string,
    note: string,
    quote?: string,
    pages?: string[],
    intent?: NoteIntent,
    tags?: string[],
    vault?: string,
  ): Promise<NoteCaptureResult>;
  gapReport(
    topic: string,
    question: string,
    reason?: string,
    referencePages?: string[],
    vault?: string,
  ): Promise<GapReportResult>;
}

export const answerToolCalls: ToolCallGroup<AnswerToolCalls> = {
  query(topic: string, question: string, vault = ""): Promise<QueryAnswer> {
    return this.call("query", { topic, question, vault }, LLM_CALL_TIMEOUT_MS);
  },

  curateExample(
    topic: string,
    query: string,
    answer: string,
    verdict: "good" | "bad",
    pagesUsed: string[] = [],
    vault = "",
  ): Promise<Record<string, unknown>> {
    return this.call("curate_example", {
      topic,
      query,
      answer,
      verdict,
      pages_used: pagesUsed,
      vault,
    });
  },

  noteCapture(
    topic: string,
    note: string,
    quote = "",
    pages: string[] = [],
    intent: NoteIntent = "reflection",
    tags: string[] = [],
    vault = "",
  ): Promise<NoteCaptureResult> {
    return this.call("note_capture", {
      topic,
      note,
      quote,
      pages,
      intent,
      tags,
      vault,
    });
  },

  gapReport(
    topic: string,
    question: string,
    reason = "",
    referencePages: string[] = [],
    vault = "",
  ): Promise<GapReportResult> {
    return this.call("gap_report", {
      topic,
      question,
      reason,
      reference_pages: referencePages,
      vault,
    });
  },
};
