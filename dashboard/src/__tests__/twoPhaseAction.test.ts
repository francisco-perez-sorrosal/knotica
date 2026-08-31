import { describe, expect, it } from "vitest";

import { idleTwoPhase, runConfirm, runPreview } from "../TwoPhaseAction";
import type {
  TwoPhaseEnvelope,
  TwoPhaseHandlers,
  TwoPhaseState,
} from "../TwoPhaseAction";

/**
 * The one client-side billing boundary.
 *
 * Every billed tool on this surface follows the same server protocol: a free
 * call quotes the work and mints a short-lived nonce, and only a second call
 * carrying that nonce spends anything. The primitive under test owns that
 * dance for every pane, so what is asserted here is what protects every billed
 * control on the dashboard: a quote never lands where a result is shown, and
 * the billing leg is unreachable without a nonce phase one minted.
 *
 * Both legs are supplied by the caller and are recorded fakes here, so no test
 * reaches a tool client, a transport, or a real charge.
 */

/** A quote or a result from the gate: a nonce, what it would cost, what it did. */
interface GateEnvelope extends TwoPhaseEnvelope {
  estimated_cost?: string;
  billed?: boolean;
  message?: string;
}

const GATE_QUOTE: GateEnvelope = {
  confirm_nonce: "gate-nonce",
  estimated_cost: "$0.12",
};
const GATE_RESULT: GateEnvelope = {
  billed: true,
  message: "Gate cycle finished",
};

/** What the primitive asked the caller to do, in the order it asked. */
type RecordedLeg<T extends TwoPhaseEnvelope> =
  | { leg: "quote" }
  | { leg: "bill"; nonce: string; quoted: T };

interface RecordedLegs<T extends TwoPhaseEnvelope> {
  handlers: TwoPhaseHandlers<T>;
  calls: RecordedLeg<T>[];
  errors: string[];
}

function recordingLegs<T extends TwoPhaseEnvelope>(legs: {
  quote: () => Promise<T>;
  bill: () => Promise<T>;
}): RecordedLegs<T> {
  const calls: RecordedLeg<T>[] = [];
  const errors: string[] = [];
  return {
    calls,
    errors,
    handlers: {
      preview: () => {
        calls.push({ leg: "quote" });
        return legs.quote();
      },
      confirm: (nonce, quoted) => {
        calls.push({ leg: "bill", nonce, quoted });
        return legs.bill();
      },
      onError: (message) => {
        errors.push(message);
      },
    },
  };
}

/**
 * Drives the state functions the way `useTwoPhaseAction` does: each emitted
 * state becomes the state the next invocation reads, synchronously. A caller
 * wired any other way would read a stale, never-busy state, so the in-flight
 * guards below are only meaningful against this feedback.
 */
function twoPhaseAction<T extends TwoPhaseEnvelope>(
  handlers: TwoPhaseHandlers<T>,
) {
  const emitted: TwoPhaseState<T>[] = [];
  let latest = idleTwoPhase<T>();
  const emit = (next: TwoPhaseState<T>) => {
    latest = next;
    emitted.push(next);
  };
  return {
    emitted,
    get state(): TwoPhaseState<T> {
      return latest;
    },
    preview: () => runPreview(latest, handlers, emit),
    confirm: () => runConfirm(latest, handlers, emit),
  };
}

interface Deferred<T> {
  promise: Promise<T>;
  settle: (value: T) => void;
}

/** A leg that stays in flight until the test decides it lands. */
function deferred<T>(): Deferred<T> {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

describe("taking a free quote", () => {
  it("asks the caller's free leg and cannot reach the billing leg", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();

    expect(legs.calls).toEqual([{ leg: "quote" }]);
  });

  it("puts the quote where a pane shows an estimate, never where it shows a result", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();

    expect(action.state).toEqual({
      preview: GATE_QUOTE,
      outcome: null,
      busy: null,
    });
  });

  it("publishes a quoting phase while the free leg is in flight, and clears it once quoted", async () => {
    const pending = deferred<GateEnvelope>();
    const legs = recordingLegs<GateEnvelope>({
      quote: () => pending.promise,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    const inFlight = action.preview();
    expect(action.state.busy).toBe("preview");

    pending.settle(GATE_QUOTE);
    await inFlight;

    expect(action.state.busy).toBeNull();
  });

  it("does not ask a second time while a quote is already in flight", async () => {
    const pending = deferred<GateEnvelope>();
    const legs = recordingLegs<GateEnvelope>({
      quote: () => pending.promise,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    const inFlight = action.preview();
    await action.preview();

    expect(legs.calls).toEqual([{ leg: "quote" }]);

    pending.settle(GATE_QUOTE);
    await inFlight;
  });
});

describe("redeeming a quote", () => {
  it("redeems the very nonce and envelope the quote minted", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();
    await action.confirm();

    expect(legs.calls).toEqual([
      { leg: "quote" },
      { leg: "bill", nonce: "gate-nonce", quoted: GATE_QUOTE },
    ]);
  });

  it("refuses to bill when no quote has been taken", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.confirm();

    expect(legs.calls).toEqual([]);
    expect(action.emitted).toEqual([]);
  });

  it("refuses to bill when the quote carried no nonce to redeem", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => ({ estimated_cost: "$0.12" }),
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();
    await action.confirm();

    expect(legs.calls).toEqual([{ leg: "quote" }]);
  });

  it("bills once when a second confirm arrives before the first has landed", async () => {
    const pending = deferred<GateEnvelope>();
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: () => pending.promise,
    });
    const action = twoPhaseAction(legs.handlers);
    await action.preview();

    const inFlight = action.confirm();
    await action.confirm();

    expect(legs.calls).toEqual([
      { leg: "quote" },
      { leg: "bill", nonce: "gate-nonce", quoted: GATE_QUOTE },
    ]);

    pending.settle(GATE_RESULT);
    await inFlight;
  });

  it("publishes a billing phase while the charge is in flight, and keeps the quote visible", async () => {
    const pending = deferred<GateEnvelope>();
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: () => pending.promise,
    });
    const action = twoPhaseAction(legs.handlers);
    await action.preview();

    const inFlight = action.confirm();

    expect(action.state).toEqual({
      preview: GATE_QUOTE,
      outcome: null,
      busy: "confirm",
    });

    pending.settle(GATE_RESULT);
    await inFlight;
  });
});

describe("reporting what the charge did", () => {
  it("replaces the quote with the result, so a spent estimate cannot linger", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();
    await action.confirm();

    expect(action.state).toEqual({
      preview: null,
      outcome: GATE_RESULT,
      busy: null,
    });
  });
});

describe("when a leg fails", () => {
  it("leaves nothing to redeem when the quote itself failed, and reports the cause", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => {
        throw new Error("provider unreachable");
      },
      bill: async () => GATE_RESULT,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();

    expect(action.state).toEqual({ preview: null, outcome: null, busy: null });
    expect(legs.errors).toEqual(["provider unreachable"]);
  });

  it("keeps the quote when the charge failed, so the same confirm can be offered again", async () => {
    const legs = recordingLegs<GateEnvelope>({
      quote: async () => GATE_QUOTE,
      bill: async () => {
        throw new Error("gate refused the candidate");
      },
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();
    await action.confirm();

    expect(action.state).toEqual({
      preview: GATE_QUOTE,
      outcome: null,
      busy: null,
    });
    expect(legs.errors).toEqual(["gate refused the candidate"]);
  });
});

describe("serving more than one billed surface", () => {
  it("carries envelopes it knows nothing about, reading only the nonce", async () => {
    interface DrainEnvelope extends TwoPhaseEnvelope {
      open_gaps?: number;
      would_drain?: number;
      drained?: number;
    }
    const drainQuote: DrainEnvelope = {
      confirm_nonce: "drain-nonce",
      open_gaps: 7,
      would_drain: 7,
    };
    const drainResult: DrainEnvelope = { drained: 7 };
    const legs = recordingLegs<DrainEnvelope>({
      quote: async () => drainQuote,
      bill: async () => drainResult,
    });
    const action = twoPhaseAction(legs.handlers);

    await action.preview();
    await action.confirm();

    expect(legs.calls).toEqual([
      { leg: "quote" },
      { leg: "bill", nonce: "drain-nonce", quoted: drainQuote },
    ]);
    expect(action.state.outcome).toEqual(drainResult);
  });
});
