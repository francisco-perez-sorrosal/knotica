import type { App } from "@modelcontextprotocol/ext-apps";
import { beforeEach, describe, expect, it } from "vitest";

import { BridgeToolClient } from "../toolClient";
import type { LoopOnceResult } from "../types";

/**
 * The single billed loop pass, as a two-legged sequence.
 *
 * Its sibling `describe` in `toolClient.twoPhase.test.ts` pins what one call
 * puts on the wire. This file pins the *sequence*: a free quote, then a second
 * request that redeems it — the leg that spends money, and the one that was
 * missing when a single click minted a quote and rendered it as a result.
 *
 * The client is driven through `BridgeToolClient` over a recording fake host,
 * the same seam the sandboxed `ui://` mount uses, so every assertion is about
 * the request the server would actually receive. Nothing here reaches a real
 * transport, so no test can bill.
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

describe("putting both legs of a single billed loop pass on the wire", () => {
  let host: RecordingHost;

  beforeEach(() => {
    host = recordingHost();
  });

  it("bills only on a second call, and that call differs from the preview by the nonce alone", async () => {
    await host.client.loopRunOnce("physics", "", "research");
    await host.client.loopRunOnce("physics", "gate-nonce", "research");

    expect(host.calls).toHaveLength(2);
    const preview = wireArgs(host.calls[0]);
    const billed = wireArgs(host.calls[1]);

    expect(preview.confirm).toBe("");
    expect(billed).toEqual({ ...preview, confirm: "gate-nonce" });
  });

  it("hands the caller the nonce and the estimate the preview quoted", async () => {
    host.replyWith({
      action: "run_once",
      topic: "physics",
      estimated_cost: "$0.12",
      confirm_nonce: "gate-nonce",
      ttl: 300,
    });

    const preview: LoopOnceResult = await host.client.loopRunOnce("physics");

    expect(preview.confirm_nonce).toBe("gate-nonce");
    expect(preview.estimated_cost).toBe("$0.12");
  });

  it("reports what the redeemed pass did, with no nonce left over to redeem again", async () => {
    host.replyWith({
      action: "run_once",
      topic: "physics",
      billed: true,
      acted: true,
      decision: "promote",
      message: "Gate cycle finished",
    });

    const outcome: LoopOnceResult = await host.client.loopRunOnce(
      "physics",
      "gate-nonce",
    );

    expect(outcome.billed).toBe(true);
    expect(outcome.confirm_nonce).toBeUndefined();
  });

  it("gives the billing leg the same long deadline as the free preview", async () => {
    await host.client.loopRunOnce("physics");
    await host.client.loopRunOnce("physics", "gate-nonce");

    expect(host.calls.map((call) => call.timeoutMs)).toEqual([
      LLM_CALL_DEADLINE_MS,
      LLM_CALL_DEADLINE_MS,
    ]);
  });
});
