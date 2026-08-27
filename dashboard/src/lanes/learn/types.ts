/**
 * Learn-lane wire shapes: the ingest/curate activity stream.
 *
 * Re-exported verbatim from `src/types.ts`, so
 * `import type { X } from "../../types"` still resolves.
 */

export type ActivityWorkflow = "ingest" | "curate";

export interface IngestEvent {
  schema_version: number;
  ts: string;
  run_id: string;
  workflow?: ActivityWorkflow;
  topic: string;
  stage: string;
  status: string;
  title: string;
  detail: string;
  citation_key: string;
  path: string;
  commit_sha: string;
  source: "client" | "server";
  /** True when this stage was reported after a later pipeline step. */
  out_of_order?: boolean;
}

export interface IngestRun {
  run_id: string;
  workflow?: ActivityWorkflow;
  topic: string;
  citation_key: string;
  started_at?: string;
  updated_at?: string;
  current_stage: string;
  current_title: string;
  status: string;
  terminal: boolean;
  stage_index: number;
  event_count: number;
  stages_seen: string[];
}

export interface IngestActivity {
  schema_version: number;
  activity_path: string;
  pipeline_stages: string[];
  curate_pipeline_stages?: string[];
  events: IngestEvent[];
  active_run: IngestRun | null;
  runs: IngestRun[];
  has_more: boolean;
}
