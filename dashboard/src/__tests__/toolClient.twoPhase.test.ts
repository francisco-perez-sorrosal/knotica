import type { App } from "@modelcontextprotocol/ext-apps";
import { beforeEach, describe, expect, it } from "vitest";

import { BridgeToolClient } from "../toolClient";
import type { GapfillDiscoverResult, LoopOnceResult, LoopRunEvalResult } from "../types";

/**
 * What the dashboard puts on the wire for the billed, two-phase calls.
 *
 * The client is driven through `BridgeToolClient` over a recording fake host —
 * the same seam the sandboxed `ui://` mount uses — so every assertion here is
 * about the request the server would actually receive, not about an internal
 * helper. Nothing in this file reaches a real transport, so no test can bill.
 */

/** The deadline the billed calls carry; the SDK's own default is 60 s. */
const LLM_CALL_DEADLINE_MS = 15 * 60 * 1000;

interface RecordedCall {
  name: string;
  args: Record<string, unknown>;
  timeoutMs: number | undefined;
}

/**
 * The argument object as the server sees it. JSON is the wire, so a key whose
 * value is `undefined` never leaves the browser and is not part of the request.
 */
function wireArgs(call: RecordedCall): Record<string, unknown> {
  return JSON.parse(JSON.stringify(call.args)) as Record<string, unknown>;
}

interface RecordingHost {
  client: BridgeToolClient;
  calls: RecordedCall[];
  /** Queue the payload the next tool call resolves with. */
  replyWith(payload: Record<string, unknown>): void;
}

function recordingHost(): RecordingHost {
  const calls: RecordedCall[] = [];
  const replies: Record<string, unknown>[] = [];
  const fakeApp = {
    async callServerTool(
      request: { name: string; arguments?: Record<string, unknown> },
      options?: { timeout?: number },
    ) {
      calls.push({
        name: request.name,
        args: request.arguments ?? {},
        timeoutMs: options?.timeout,
      });
      return { structuredContent: replies.shift() ?? {} };
    },
  };
  return {
    client: new BridgeToolClient(fakeApp as unknown as App),
    calls,
    replyWith(payload) {
      replies.push(payload);
    },
  };
}

describe("previewing and then confirming a billed evaluation run", () => {
  let host: RecordingHost;

  beforeEach(() => {
    host = recordingHost();
  });

  it("asks the loop dispatcher for a free preview when no nonce is supplied", async () => {
    await host.client.loopRunEval("physics");

    expect(host.calls).toHaveLength(1);
    expect(host.calls[0].name).toBe("loop");
    expect(wireArgs(host.calls[0])).toEqual({
      action: "run_eval",
      topic: "physics",
      confirm: "",
      vault: "",
    });
  });

  it("hands the caller the nonce the preview minted, so the run can be redeemed", async () => {
    host.replyWith({
      action: "run_eval",
      topic: "physics",
      worker: "w",
      judge: "j",
      num_threads: 4,
      estimated_cost: "$0.40",
      confirm_nonce: "nonce-from-preview",
      ttl: 300,
    });

    const preview: LoopRunEvalResult = await host.client.loopRunEval("physics");

    expect(preview.confirm_nonce).toBe("nonce-from-preview");
  });

  it("bills only on a second call, and that call differs from the preview by the nonce alone", async () => {
    await host.client.loopRunEval("physics", "", 4, "notes");
    await host.client.loopRunEval("physics", "nonce-from-preview", 4, "notes");

    expect(host.calls).toHaveLength(2);
    const preview = wireArgs(host.calls[0]);
    const billed = wireArgs(host.calls[1]);

    expect(preview.confirm).toBe("");
    expect(billed).toEqual({ ...preview, confirm: "nonce-from-preview" });
  });

  it("carries the thread count and vault the caller chose", async () => {
    await host.client.loopRunEval("physics", "nonce-1", 8, "research");

    expect(wireArgs(host.calls[0])).toEqual({
      action: "run_eval",
      topic: "physics",
      confirm: "nonce-1",
      num_threads: 8,
      vault: "research",
    });
  });

  it("gives both legs a deadline long enough to outlive a real eval", async () => {
    await host.client.loopRunEval("physics");
    await host.client.loopRunEval("physics", "nonce-1");

    expect(host.calls.map((call) => call.timeoutMs)).toEqual([
      LLM_CALL_DEADLINE_MS,
      LLM_CALL_DEADLINE_MS,
    ]);
  });
});

describe("previewing and then confirming a billed gap drain", () => {
  let host: RecordingHost;

  beforeEach(() => {
    host = recordingHost();
  });

  it("asks for a free preview of the drain when no nonce is supplied", async () => {
    await host.client.gapfillDiscover("physics");

    expect(host.calls).toHaveLength(1);
    expect(host.calls[0].name).toBe("gapfill_discover");
    expect(wireArgs(host.calls[0])).toEqual({
      topic: "physics",
      max_gaps: 0,
      confirm: "",
      vault: "",
    });
  });

  it("hands the caller the nonce the preview minted, so the drain can be redeemed", async () => {
    host.replyWith({
      action: "gapfill_discover",
      topic: "physics",
      provider_configured: true,
      open_gaps: 7,
      would_drain: 7,
      estimated_cost: "$0.20",
      confirm_nonce: "drain-nonce",
      ttl: 300,
    });

    const preview: GapfillDiscoverResult = await host.client.gapfillDiscover("physics");

    expect(preview.confirm_nonce).toBe("drain-nonce");
  });

  it("bills only on a second call, and that call differs from the preview by the nonce alone", async () => {
    await host.client.gapfillDiscover("physics", 5, "", "research");
    await host.client.gapfillDiscover("physics", 5, "drain-nonce", "research");

    expect(host.calls).toHaveLength(2);
    const preview = wireArgs(host.calls[0]);
    const billed = wireArgs(host.calls[1]);

    expect(preview.confirm).toBe("");
    expect(billed).toEqual({ ...preview, confirm: "drain-nonce" });
  });

  it("carries the gap cap the caller chose", async () => {
    await host.client.gapfillDiscover("physics", 5, "drain-nonce", "research");

    expect(wireArgs(host.calls[0])).toEqual({
      topic: "physics",
      max_gaps: 5,
      confirm: "drain-nonce",
      vault: "research",
    });
  });

  it("gives both legs a deadline long enough to outlive a real drain", async () => {
    await host.client.gapfillDiscover("physics");
    await host.client.gapfillDiscover("physics", 0, "drain-nonce");

    expect(host.calls.map((call) => call.timeoutMs)).toEqual([
      LLM_CALL_DEADLINE_MS,
      LLM_CALL_DEADLINE_MS,
    ]);
  });
});

describe("previewing and then confirming a single billed loop pass", () => {
  let host: RecordingHost;

  beforeEach(() => {
    host = recordingHost();
  });

  it("asks the loop dispatcher for a free preview when no nonce is supplied", async () => {
    await host.client.loopRunOnce("physics", "", "research");

    expect(host.calls).toHaveLength(1);
    expect(host.calls[0].name).toBe("loop");
    const sent = wireArgs(host.calls[0]);

    expect(Object.keys(sent).sort()).toEqual(["action", "confirm", "topic", "vault"]);
    expect(sent).toEqual({
      action: "run_once",
      topic: "physics",
      confirm: "",
      vault: "research",
    });
  });

  it("defaults the vault the same way the confirmable calls do", async () => {
    await host.client.loopRunOnce("physics");

    expect(wireArgs(host.calls[0])).toEqual({
      action: "run_once",
      topic: "physics",
      confirm: "",
      vault: "",
    });
  });

  it("carries the same long deadline as the calls that do confirm", async () => {
    await host.client.loopRunOnce("physics");

    expect(host.calls[0].timeoutMs).toBe(LLM_CALL_DEADLINE_MS);
  });

  it("returns a result with somewhere to put the nonce the server minted", () => {
    // `confirm_nonce` is what tells a preview apart from an outcome. All three
    // confirmable results now declare it, so a caller holding any of them can
    // tell whether anything ran. The three constants below are checked by
    // `tsc --noEmit`, so dropping the field from any of them fails the build.
    const runEvalDeclaresNonce: DeclaresConfirmNonce<LoopRunEvalResult> = true;
    const gapfillDeclaresNonce: DeclaresConfirmNonce<GapfillDiscoverResult> = true;
    const runOnceDeclaresNonce: DeclaresConfirmNonce<LoopOnceResult> = true;

    expect([runEvalDeclaresNonce, gapfillDeclaresNonce, runOnceDeclaresNonce]).toEqual([
      true,
      true,
      true,
    ]);
  });
});

/** True when `T` declares a `confirm_nonce` field, optional or not. */
type DeclaresConfirmNonce<T> = "confirm_nonce" extends keyof T ? true : false;
