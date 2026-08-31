import type { App } from "@modelcontextprotocol/ext-apps";
import { beforeEach, describe, expect, it } from "vitest";

import { BridgeToolClient } from "../toolClient";

/**
 * `BridgeToolClient`/`ToolClient` carry no `noteCapture`/`gapReport` methods
 * yet -- this is the RED half of a paired implementation/test step
 * (the design's `react` stage; Answer's "Note it"/"Report
 * gap" actions). Calling either method through a *typed*
 * `BridgeToolClient` reference would make `tsc --noEmit` fail for the whole
 * project the moment this file lands (the same failure mode a literal
 * `import { AnswerLane } from "../AnswerLane"` would cause in
 * `AnswerLane.test.tsx`), so both calls go through `asExtended()`, a local
 * cast to a structurally-typed supertype the compiler accepts unconditionally
 * (any type is assignable to `unknown`, and `unknown` is assignable to
 * anything via an explicit cast). This keeps `tsc --noEmit` green while the
 * methods don't exist; at *runtime*, calling a method absent from the real
 * class throws `TypeError: ... is not a function`, which is this file's
 * actual RED signal under `npm test`.
 *
 * The server-side wire contract both methods target
 * (`src/knotica/mcp_server/tools_notes.py::note_capture`,
 * `src/knotica/mcp_server/tools_gaps.py::gap_report`) is read directly from
 * source, not guessed: `note_capture(topic, note, quote="", pages=[],
 * intent="reflection", tags=[], vault="")`; `gap_report(topic, question,
 * reason="", reference_pages=None, vault="")`. Argument *names* on the wire
 * (`pages_used` vs `pages`, `reference_pages`) are asserted exactly; the two
 * new `ToolClient` method's own parameter order/defaults are a load-bearing
 * assumption of this suite (the paired implementation wins on
 * conflict) -- chosen to mirror `curateExample`'s existing
 * `(topic, ..., pagesUsed: string[] = [], vault = "")` shape, the only
 * other flat lane-only tool `toolClient.ts` already wraps this way.
 *
 * Driven through `BridgeToolClient` over a recording fake host, the same
 * seam `toolClient.runOnce.test.ts`/`toolClient.twoPhase.test.ts` use --
 * every assertion is about the request the server would actually receive.
 * Nothing here reaches a real transport, so no test can bill (neither tool
 * is billed to begin with).
 */

interface NoteAndGapClient {
  noteCapture(
    topic: string,
    note: string,
    quote?: string,
    pages?: string[],
    intent?: string,
    tags?: string[],
    vault?: string,
  ): Promise<Record<string, unknown>>;
  gapReport(
    topic: string,
    question: string,
    reason?: string,
    referencePages?: string[],
    vault?: string,
  ): Promise<Record<string, unknown>>;
}

function asExtended(client: BridgeToolClient): NoteAndGapClient {
  return client as unknown as NoteAndGapClient;
}

interface RecordedCall {
  name: string;
  args: Record<string, unknown>;
}

/** The argument object as the server sees it -- JSON is the wire, so a key
 * whose value is `undefined` never leaves the browser and is not part of the
 * request. */
function wireArgs(call: RecordedCall): Record<string, unknown> {
  return JSON.parse(JSON.stringify(call.args)) as Record<string, unknown>;
}

interface RecordingHost {
  client: BridgeToolClient;
  calls: RecordedCall[];
}

function recordingHost(): RecordingHost {
  const calls: RecordedCall[] = [];
  const fakeApp = {
    async callServerTool(request: {
      name: string;
      arguments?: Record<string, unknown>;
    }) {
      calls.push({ name: request.name, args: request.arguments ?? {} });
      return { structuredContent: {} };
    },
  };
  return { client: new BridgeToolClient(fakeApp as unknown as App), calls };
}

describe("putting note_capture on the wire", () => {
  let host: RecordingHost;

  beforeEach(() => {
    host = recordingHost();
  });

  it("sends the full argument set when every optional is supplied", async () => {
    await asExtended(host.client).noteCapture(
      "agentic-systems",
      "MIPROv2 bootstraps demonstrations from the trainset.",
      "How does MIPROv2 pick demonstrations?",
      ["mipro-overview.md"],
      "insight",
      ["mipro"],
      "research",
    );

    expect(host.calls).toHaveLength(1);
    expect(host.calls[0].name).toBe("note_capture");
    expect(wireArgs(host.calls[0])).toEqual({
      topic: "agentic-systems",
      note: "MIPROv2 bootstraps demonstrations from the trainset.",
      quote: "How does MIPROv2 pick demonstrations?",
      pages: ["mipro-overview.md"],
      intent: "insight",
      tags: ["mipro"],
      vault: "research",
    });
  });

  it("defaults every optional to the server's own default when omitted", async () => {
    await asExtended(host.client).noteCapture("agentic-systems", "A note.");

    expect(wireArgs(host.calls[0])).toEqual({
      topic: "agentic-systems",
      note: "A note.",
      quote: "",
      pages: [],
      intent: "reflection",
      tags: [],
      vault: "",
    });
  });
});

describe("putting gap_report on the wire", () => {
  let host: RecordingHost;

  beforeEach(() => {
    host = recordingHost();
  });

  it("sends the full argument set when every optional is supplied", async () => {
    await asExtended(host.client).gapReport(
      "agentic-systems",
      "How does MIPROv2 pick demonstrations?",
      "the answer never mentioned bootstrapping",
      ["mipro-overview.md"],
      "research",
    );

    expect(host.calls).toHaveLength(1);
    expect(host.calls[0].name).toBe("gap_report");
    expect(wireArgs(host.calls[0])).toEqual({
      topic: "agentic-systems",
      question: "How does MIPROv2 pick demonstrations?",
      reason: "the answer never mentioned bootstrapping",
      reference_pages: ["mipro-overview.md"],
      vault: "research",
    });
  });

  it("defaults every optional to the server's own default when omitted", async () => {
    await asExtended(host.client).gapReport(
      "agentic-systems",
      "How does MIPROv2 pick demonstrations?",
    );

    expect(wireArgs(host.calls[0])).toEqual({
      topic: "agentic-systems",
      question: "How does MIPROv2 pick demonstrations?",
      reason: "",
      reference_pages: [],
      vault: "",
    });
  });
});
