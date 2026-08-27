/**
 * Learn's half of `ToolClient`: the read-only ingest-activity feed.
 *
 * Learn explores what is already in the vault, so it mutates nothing and
 * reaches for exactly one tool.
 */

import type { ToolCallGroup } from "../../toolClientCore";

import type { IngestActivity } from "./types";

export interface LearnToolCalls {
  ingestActivityRead(
    topic: string,
    vault?: string,
    runId?: string,
  ): Promise<IngestActivity>;
}

export const learnToolCalls: ToolCallGroup<LearnToolCalls> = {
  ingestActivityRead(
    topic: string,
    vault = "",
    runId = "",
  ): Promise<IngestActivity> {
    return this.call("ingest_activity_read", {
      topic,
      vault,
      run_id: runId,
      limit: 120,
    });
  },
};
