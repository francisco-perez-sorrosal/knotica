// GENERATED FILE -- do not edit by hand.
// Source of truth: src/knotica/core/process_model.py
// Regenerate: uv run --extra evals python scripts/generate_process_model_ts.py
//
// Structure only, no predicates: a stage's live state is served by the
// `wiki_status` tool. This mirror is the fallback the dashboard renders a
// lane's rail from before that call returns, or when the server is
// unreachable. Stage order is the array order below.

export interface ProcessStage {
  readonly id: string;
  readonly title: string;
  readonly handoff: boolean;
}

export const LANES: readonly string[] = [
  "home",
  "learn",
  "answer",
  "improve",
  "fill",
  "tend",
];

export const LANE_STAGES: Readonly<Record<string, readonly ProcessStage[]>> = {
  "home": [],
  "learn": [
    { id: "source", title: "Source", handoff: true },
    { id: "fetch_parse", title: "Fetch / parse", handoff: true },
    { id: "pages", title: "Pages", handoff: true },
    { id: "curate", title: "Curate", handoff: true },
  ],
  "answer": [
    { id: "ask", title: "Ask", handoff: false },
    { id: "cite", title: "Cite", handoff: false },
    { id: "react", title: "React", handoff: false },
  ],
  "improve": [
    { id: "instrument", title: "Instrument", handoff: false },
    { id: "observe", title: "Observe", handoff: false },
    { id: "gate", title: "Gate", handoff: false },
    { id: "heal", title: "Heal", handoff: false },
    { id: "promote", title: "Promote", handoff: false },
    { id: "prove", title: "Prove", handoff: false },
  ],
  "fill": [
    { id: "gap", title: "Gap", handoff: false },
    { id: "discover", title: "Discover", handoff: false },
    { id: "approve", title: "Approve", handoff: false },
    { id: "ingest", title: "Ingest", handoff: true },
    { id: "gate", title: "Gate", handoff: false },
  ],
  "tend": [
    { id: "doctor", title: "Doctor", handoff: false },
    { id: "lint", title: "Lint", handoff: false },
    { id: "okf", title: "OKF", handoff: false },
    { id: "migrate", title: "Migrate", handoff: true },
    { id: "drift", title: "Drift", handoff: false },
  ],
};
