/** Wire shapes returned by the read-only `wiki_status` and `metrics_read` tools. */

export type GateState = "unknown" | "pass" | "fail";
export type LoopStage = "idle" | "evaluating" | "passed" | "failed" | "merging" | "reverting";

export interface MetricsRecord {
  schema_version: number;
  topic: string;
  timestamp: string;
  generation: number;
  harness_version: string;
  scalar: number;
  components: {
    qa_accuracy: number;
    citation_validity: number;
    lint_violations: number;
    token_cost: number;
  };
  n_examples: number;
  corpus_ref: string;
  artifact_ref: string;
}

export interface WikiStatus {
  schema_version: number;
  vault: string;
  compile_ready_threshold: number;
  topics: Array<{
    topic: string;
    pages: number;
    curated: number;
    to_compile_ready: number;
    lint_violations: number;
    last_eval: MetricsRecord | null;
  }>;
  totals: { topics: number; pages: number; curated: number; lint_violations: number };
  last_lint: string | null;
  unpushed: number | null;
  gate: { state: GateState; baseline: number | null; last_scalar: number | null };
  loop: { stage: LoopStage | null; candidate_branch?: string | null; last_decision?: string | null };
}

export interface MetricsWindow {
  topic: string;
  records: MetricsRecord[];
  has_more: boolean;
  next_before_generation: number | null;
  skipped_malformed: number;
}
