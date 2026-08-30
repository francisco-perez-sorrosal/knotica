import { beforeAll, describe, expect, it } from "vitest";

/**
 * `deriveAttentionRows` -- the pure grouping function behind Home
 * (`IMPLEMENTATION_PLAN.md` Steps 113/114, `INTERFACE_DESIGN.md §2.1`,
 * `dec-092`). RED-first: this suite spawns *before* Step 113's implementer,
 * per the RED-handshake fix -- the standalone run below must fail at
 * collection with a missing-module error, which the paired implementation
 * step is gated on.
 *
 * `dashboard/src/lanes/home/attentionRows.ts` does not exist yet, so it is
 * loaded through a non-literal dynamic `import()` -- the same device
 * `laneRailState.test.ts` used for its own not-yet-existing module: a
 * literal `import { deriveAttentionRows } from "../attentionRows"` would
 * fail `tsc --noEmit` for the whole project the moment this file lands. The
 * types below are this suite's own mirror of the expected surface, not an
 * import of the real ones.
 *
 * **Payload shape is pinned from the live server seam, not from
 * `INTERFACE_DESIGN.md §2.1`'s illustrative mockup.** Reading
 * `core/status.py::_attention_status`/`_attention_row` directly (the
 * mockup's `baseline_unreachable` / "gate blocked" / stale-ingest-session
 * "Blocked" members do not exist in the shipped payload -- Step 110/111
 * already fixed the real shape to `{topic, suggestions: {pending,
 * refused_awaiting_rework}, compile_ready, runner: {alive}}` plus
 * vault-level `totals`/`last_lint`/`drift`), only the fields the server
 * actually returns are exercised here.
 *
 * **Load-bearing assumption (full reasoning in
 * `LEARNINGS_test-engineer_step114.md`; the Step 113 implementer wins on
 * conflict):** each row carries `{topic, lane, urgency, narration, action}`.
 * `lane` routes `[Open]`/`[Watch]` to the right pane per the mockup's own
 * per-row lane tags ("fill · gate", "fill · approve", "improve · observe");
 * `refused_awaiting_rework` and `pending` both route to `"fill"` (both are
 * suggestion-lifecycle signals), `compile_ready` and `runner.alive` both
 * route to `"improve"` (both are Improve-lane signals in the mockup).
 * `action` is `"Watch"` only for `urgency: "running"` rows, `"Open"`
 * otherwise -- the mockup's own `[Open]`/`[Watch]` split. A topic can
 * surface **more than one row**, one per independent signal (the mockup's
 * own `rag-patterns` appears twice, once blocked once waiting) -- this is
 * why the function returns a flat array of rows, not one row per topic.
 * Narration text itself is the implementer's wording choice; these tests
 * only assert it is non-empty and topically relevant (contains a keyword
 * tied to the signal that produced the row), never an exact string.
 */

interface AttentionSuggestions {
  pending: number;
  refused_awaiting_rework: number;
}

interface AttentionRunner {
  alive: boolean;
}

interface AttentionTopicRow {
  topic: string;
  suggestions: AttentionSuggestions;
  compile_ready: boolean;
  runner: AttentionRunner;
}

interface AttentionTotals {
  topics: number;
  pending: number;
  refused_awaiting_rework: number;
  compile_ready: number;
  runners_alive: number;
}

interface AttentionLastLint {
  date: string | null;
  age_days: number | null;
  stale: boolean;
}

interface AttentionDrift {
  default_collapsed: boolean;
  count: number | null;
}

interface AttentionStatus {
  schema_version: number;
  vault_name: string;
  topics: AttentionTopicRow[];
  totals: AttentionTotals;
  last_lint: AttentionLastLint;
  drift: AttentionDrift;
}

type AttentionUrgency = "blocked" | "waiting" | "running";
type AttentionLane = "learn" | "answer" | "improve" | "fill" | "tend";
type AttentionKind =
  | "refused_rework"
  | "pending_suggestions"
  | "compile_ready"
  | "runner_active";

interface AttentionRow {
  topic: string;
  lane: AttentionLane;
  urgency: AttentionUrgency;
  kind: AttentionKind;
  narration: string;
  action: "Open" | "Watch";
}

interface AttentionRowsModule {
  deriveAttentionRows(payload: AttentionStatus): AttentionRow[];
  sortAttentionRows(rows: AttentionRow[]): AttentionRow[];
}

const ATTENTION_ROWS_MODULE_PATH = "../attentionRows";

let attentionRows: AttentionRowsModule;

beforeAll(async () => {
  attentionRows = (await import(
    ATTENTION_ROWS_MODULE_PATH
  )) as AttentionRowsModule;
});

const QUIET_ROW: AttentionTopicRow = {
  topic: "quiet-topic",
  suggestions: { pending: 0, refused_awaiting_rework: 0 },
  compile_ready: false,
  runner: { alive: false },
};

function payload(topics: AttentionTopicRow[]): AttentionStatus {
  return {
    schema_version: 1,
    vault_name: "kb",
    topics,
    totals: {
      topics: topics.length,
      pending: 0,
      refused_awaiting_rework: 0,
      compile_ready: 0,
      runners_alive: 0,
    },
    last_lint: { date: "2026-08-20", age_days: 6, stale: false },
    drift: { default_collapsed: true, count: null },
  };
}

function rowsFor(topic: AttentionTopicRow): AttentionRow[] {
  return attentionRows.deriveAttentionRows(payload([topic]));
}

describe("blocked class -- a refused-awaiting-rework suggestion", () => {
  const topic: AttentionTopicRow = {
    topic: "rag-patterns",
    suggestions: { pending: 0, refused_awaiting_rework: 1 },
    compile_ready: false,
    runner: { alive: false },
  };

  it("produces exactly one blocked row routed to fill", () => {
    const rows = rowsFor(topic);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      topic: "rag-patterns",
      urgency: "blocked",
      kind: "refused_rework",
      lane: "fill",
      action: "Open",
    });
  });

  it("narrates the refusal, not a generic placeholder", () => {
    const [row] = rowsFor(topic);
    expect(row.narration.length).toBeGreaterThan(0);
    expect(row.narration).toMatch(/refus|rework/i);
  });
});

describe("waiting class -- pending suggestions", () => {
  const topic: AttentionTopicRow = {
    topic: "rag-patterns",
    suggestions: { pending: 4, refused_awaiting_rework: 0 },
    compile_ready: false,
    runner: { alive: false },
  };

  it("produces exactly one waiting row routed to fill", () => {
    const rows = rowsFor(topic);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      topic: "rag-patterns",
      urgency: "waiting",
      kind: "pending_suggestions",
      lane: "fill",
      action: "Open",
    });
  });

  it("narrates the pending count", () => {
    const [row] = rowsFor(topic);
    expect(row.narration).toMatch(/pending|suggestion/i);
  });
});

describe("waiting class -- compile-ready", () => {
  const topic: AttentionTopicRow = {
    topic: "agentic-systems",
    suggestions: { pending: 0, refused_awaiting_rework: 0 },
    compile_ready: true,
    runner: { alive: false },
  };

  it("produces exactly one waiting row routed to improve", () => {
    const rows = rowsFor(topic);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      topic: "agentic-systems",
      urgency: "waiting",
      kind: "compile_ready",
      lane: "improve",
      action: "Open",
    });
  });
});

describe("running class -- an alive runner", () => {
  const topic: AttentionTopicRow = {
    topic: "agentic-systems",
    suggestions: { pending: 0, refused_awaiting_rework: 0 },
    compile_ready: false,
    runner: { alive: true },
  };

  it("produces exactly one running row routed to improve with a Watch action", () => {
    const rows = rowsFor(topic);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      topic: "agentic-systems",
      urgency: "running",
      kind: "runner_active",
      lane: "improve",
      action: "Watch",
    });
  });
});

describe("quiet topics -- the fourth, correct class: not rendered", () => {
  it("drops a fully quiet topic entirely", () => {
    expect(attentionRows.deriveAttentionRows(payload([QUIET_ROW]))).toEqual(
      [],
    );
  });

  it("an empty vault (no topics) produces no rows", () => {
    expect(attentionRows.deriveAttentionRows(payload([]))).toEqual([]);
  });
});

describe("a single topic can surface more than one signal", () => {
  it("emits one row per independent signal, not one row per topic", () => {
    const topic: AttentionTopicRow = {
      topic: "rag-patterns",
      suggestions: { pending: 4, refused_awaiting_rework: 1 },
      compile_ready: false,
      runner: { alive: false },
    };
    const rows = rowsFor(topic);
    const urgencies = rows.map((row) => row.urgency).sort();
    expect(urgencies).toEqual(["blocked", "waiting"]);
    expect(rows.every((row) => row.topic === "rag-patterns")).toBe(true);
  });

  it("mixes quiet and non-quiet topics, keeping only the latter's rows", () => {
    const busy: AttentionTopicRow = {
      topic: "agentic-systems",
      suggestions: { pending: 0, refused_awaiting_rework: 0 },
      compile_ready: false,
      runner: { alive: true },
    };
    const rows = attentionRows.deriveAttentionRows(
      payload([QUIET_ROW, busy]),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].topic).toBe("agentic-systems");
  });
});

describe("sortAttentionRows -- urgency-class ordering (blocked < waiting < running)", () => {
  function row(
    topic: string,
    urgency: AttentionUrgency,
    kind: AttentionKind,
  ): AttentionRow {
    return {
      topic,
      lane: "improve",
      urgency,
      kind,
      narration: "n",
      action: urgency === "running" ? "Watch" : "Open",
    };
  }

  it("orders a cross-topic interleave into blocked, then waiting, then running", () => {
    const running = row("topic-c", "running", "runner_active");
    const blocked = row("topic-a", "blocked", "refused_rework");
    const waiting = row("topic-b", "waiting", "pending_suggestions");

    const sorted = attentionRows.sortAttentionRows([running, blocked, waiting]);

    expect(sorted.map((r) => r.urgency)).toEqual([
      "blocked",
      "waiting",
      "running",
    ]);
    expect(sorted.map((r) => r.topic)).toEqual([
      "topic-a",
      "topic-b",
      "topic-c",
    ]);
  });

  it("is stable within a class -- same-urgency rows keep their original relative order", () => {
    const first = row("topic-a", "blocked", "refused_rework");
    const second = row("topic-b", "blocked", "refused_rework");
    const third = row("topic-c", "blocked", "refused_rework");

    const sorted = attentionRows.sortAttentionRows([third, first, second]);

    expect(sorted.map((r) => r.topic)).toEqual([
      "topic-c",
      "topic-a",
      "topic-b",
    ]);
  });

  it("does not mutate its input array", () => {
    const rows = [
      row("topic-b", "waiting", "pending_suggestions"),
      row("topic-a", "blocked", "refused_rework"),
    ];
    const original = [...rows];

    attentionRows.sortAttentionRows(rows);

    expect(rows).toEqual(original);
  });

  it("mirrors deriveAttentionRows' own cross-topic output into class order", () => {
    const blockedTopic: AttentionTopicRow = {
      topic: "rag-patterns",
      suggestions: { pending: 0, refused_awaiting_rework: 1 },
      compile_ready: false,
      runner: { alive: false },
    };
    const runningTopic: AttentionTopicRow = {
      topic: "agentic-systems",
      suggestions: { pending: 0, refused_awaiting_rework: 0 },
      compile_ready: false,
      runner: { alive: true },
    };
    const waitingTopic: AttentionTopicRow = {
      topic: "gap-fill",
      suggestions: { pending: 2, refused_awaiting_rework: 0 },
      compile_ready: false,
      runner: { alive: false },
    };

    // Derivation order deliberately not urgency order, so this test proves
    // sortAttentionRows -- not deriveAttentionRows -- did the reordering.
    const derived = attentionRows.deriveAttentionRows(
      payload([runningTopic, waitingTopic, blockedTopic]),
    );
    const sorted = attentionRows.sortAttentionRows(derived);

    expect(sorted.map((r) => r.urgency)).toEqual([
      "blocked",
      "waiting",
      "running",
    ]);
  });
});
