import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The HTTP mount's self-healing contract.
 *
 * The dashboard polls every 2 s for its whole lifetime, and the server it
 * polls is a local process people freely stop and restart. Two failure shapes
 * used to pin the "MCP read failed" banner on screen until a manual reload:
 *
 * 1. The page boots before the server — the first `connect()` rejection was
 *    memoized, so every later poll awaited the same dead promise.
 * 2. The server restarts mid-session — the old session errored on every call
 *    with no reconnect path.
 *
 * These tests drive `HttpToolClient` over a scripted fake of the SDK `Client`
 * and pin the recovery: a failed connect is never cached, and a failed call
 * discards the session so the next poll opens a fresh one.
 */

const script = vi.hoisted(() => ({
  /** One entry per constructed Client: connect outcomes to play back. */
  connectOutcomes: [] as Array<Error | null>,
  connectCalls: 0,
  constructed: 0,
  closed: 0,
  callImpl: undefined as (() => Promise<unknown>) | undefined,
}));

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => {
  class FakeClient {
    constructor() {
      script.constructed += 1;
    }

    connect(): Promise<void> {
      const outcome = script.connectOutcomes[script.connectCalls] ?? null;
      script.connectCalls += 1;
      return outcome ? Promise.reject(outcome) : Promise.resolve();
    }

    callTool(): Promise<unknown> {
      const impl = script.callImpl;
      if (!impl) {
        return Promise.resolve({
          content: [{ type: "text", text: JSON.stringify({ ok: true }) }],
        });
      }
      return impl();
    }

    close(): Promise<void> {
      script.closed += 1;
      return Promise.resolve();
    }
  }
  return { Client: FakeClient };
});

vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: class {},
}));

import { HttpToolClient } from "../toolClient";

/** `call` is protected; the poll path reaches it through a public method. */
function poll(client: HttpToolClient): Promise<unknown> {
  return client.wikiStatus("", "");
}

beforeEach(() => {
  script.connectOutcomes = [];
  script.connectCalls = 0;
  script.constructed = 0;
  script.closed = 0;
  script.callImpl = undefined;
});

describe("HttpToolClient recovery", () => {
  it("does not cache a failed connect — the next poll retries and succeeds", async () => {
    script.connectOutcomes = [new Error("Failed to fetch"), null];
    const client = new HttpToolClient("http://127.0.0.1:8765/mcp");

    await expect(poll(client)).rejects.toThrow("Failed to fetch");
    // Recovery without any reload: the same client instance polls again.
    await expect(poll(client)).resolves.toEqual({ ok: true });
    expect(script.connectCalls).toBe(2);
  });

  it("discards the session on a transport-level call failure and heals on the next poll", async () => {
    const client = new HttpToolClient("http://127.0.0.1:8765/mcp");
    await expect(poll(client)).resolves.toEqual({ ok: true });

    // The server restarts: the established session now rejects at the wire.
    script.callImpl = () => Promise.reject(new Error("Failed to fetch"));
    await expect(poll(client)).rejects.toThrow("Failed to fetch");
    expect(script.closed).toBe(1);

    // Next poll runs on a fresh SDK client and a fresh connect.
    script.callImpl = undefined;
    await expect(poll(client)).resolves.toEqual({ ok: true });
    expect(script.constructed).toBe(2);
    expect(script.connectCalls).toBe(2);
  });
});
