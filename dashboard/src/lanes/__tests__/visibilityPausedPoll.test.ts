import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

/**
 * `startVisibilityPausedPoll` — the first `document.visibilitychange`-aware
 * primitive in the dashboard (`dec-092` budget rule 3: "poll at 10s and
 * pause on `visibilitychange`"). Pinned here before the module exists.
 *
 * Contract under test: schedule `callback` every `intervalMs` while the
 * document is visible; while hidden, the interval itself may keep running,
 * but `callback` must not fire; on the hidden-to-visible edge, fire exactly
 * one immediate `callback` (no queued catch-up burst for ticks missed while
 * hidden), then resume the normal cadence; the returned teardown function
 * stops both the interval and the `visibilitychange` listener.
 *
 * The module under test does not exist yet — this is the RED half of a
 * paired step. A literal `import { ... } from "../visibilityPausedPoll"`
 * would fail `tsc --noEmit` for the whole project the moment this file is
 * added, not just this suite, so the specifier is loaded through a
 * non-literal binding below: TypeScript does not resolve a dynamic
 * `import()` whose argument isn't a string literal, so the rest of the tree
 * keeps type-checking while this file still fails at runtime with the
 * missing-module error the paired implementation step is gated on. The
 * types below are this suite's own mirror of the expected surface, not an
 * import of the real one — once the module lands, sibling files that
 * `import type` it directly are what actually prove its exports exist.
 *
 * This file is `.test.ts` (no JSX), so `vitest.config.ts`'s project split
 * runs it under the "node" environment, not jsdom — there is no real
 * `document`/`window` here. That is why `startVisibilityPausedPoll` takes
 * `doc`/`win` as explicit parameters in the first place: the tests below
 * pass minimal fake objects satisfying only the members the module reads
 * (`hidden`, `addEventListener`/`removeEventListener` for
 * `"visibilitychange"`, `setInterval`/`clearInterval`), never the real
 * globals.
 */

type VisibilityChangeListener = () => void;

interface FakeDocument {
  hidden: boolean;
  addEventListener(
    type: "visibilitychange",
    listener: VisibilityChangeListener,
  ): void;
  removeEventListener(
    type: "visibilitychange",
    listener: VisibilityChangeListener,
  ): void;
}

interface FakeWindow {
  setInterval(handler: () => void, timeoutMs: number): number;
  clearInterval(id: number): void;
}

interface VisibilityPausedPollModule {
  startVisibilityPausedPoll(
    callback: () => void,
    intervalMs: number,
    doc: FakeDocument,
    win: FakeWindow,
  ): () => void;
}

const VISIBILITY_PAUSED_POLL_MODULE_PATH = "../visibilityPausedPoll";

let visibilityPausedPoll: VisibilityPausedPollModule;

beforeAll(async () => {
  visibilityPausedPoll = (await import(
    VISIBILITY_PAUSED_POLL_MODULE_PATH
  )) as VisibilityPausedPollModule;
});

/**
 * dec-092's own cadence for Home's attention poll. Arbitrary to this pure
 * module, but reusing the real value lets a reader map each assertion
 * straight onto the product behavior it backs.
 */
const INTERVAL_MS = 10_000;

interface FakeDoc extends FakeDocument {
  /** Notifies every registered `visibilitychange` listener, synchronously. */
  dispatchVisibilityChange(): void;
}

function createFakeDocument(initialHidden: boolean): FakeDoc {
  let hiddenState = initialHidden;
  const listeners = new Set<VisibilityChangeListener>();

  return {
    get hidden() {
      return hiddenState;
    },
    set hidden(value: boolean) {
      hiddenState = value;
    },
    addEventListener(type, listener) {
      if (type === "visibilitychange") {
        listeners.add(listener);
      }
    },
    removeEventListener(type, listener) {
      if (type === "visibilitychange") {
        listeners.delete(listener);
      }
    },
    dispatchVisibilityChange() {
      for (const listener of listeners) {
        listener();
      }
    },
  };
}

/**
 * Delegates to the real global timer functions so `vi.useFakeTimers()` —
 * which patches `globalThis.setInterval`/`clearInterval` directly — governs
 * this fake window's cadence exactly as it would the real one.
 */
function createFakeWindow(): FakeWindow {
  return {
    setInterval: (handler, timeoutMs) =>
      globalThis.setInterval(handler, timeoutMs),
    clearInterval: (id) => globalThis.clearInterval(id),
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("startVisibilityPausedPoll — cadence while the document stays visible", () => {
  it("does not call back before the first interval elapses", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );

    expect(callback).not.toHaveBeenCalled();
  });

  it("calls back once per interval across several ticks", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );

    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(3);
  });
});

describe("startVisibilityPausedPoll — stops entirely while hidden (dec-092 budget rule 3)", () => {
  it("produces zero further callback invocations once hidden, across several missed intervals", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );
    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(1);

    doc.hidden = true;
    doc.dispatchVisibilityChange();

    vi.advanceTimersByTime(INTERVAL_MS * 3);

    expect(callback).toHaveBeenCalledTimes(1);
  });
});

describe("startVisibilityPausedPoll — resumes with one immediate call on becoming visible again", () => {
  it("fires exactly one immediate call on the hidden-to-visible edge — no queued catch-up burst", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );

    doc.hidden = true;
    doc.dispatchVisibilityChange();
    vi.advanceTimersByTime(INTERVAL_MS * 5); // several ticks missed while hidden

    doc.hidden = false;
    doc.dispatchVisibilityChange();

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("resumes the normal cadence after the immediate resume call — no double-firing", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );

    doc.hidden = true;
    doc.dispatchVisibilityChange();
    doc.hidden = false;
    doc.dispatchVisibilityChange();
    expect(callback).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it("does not fire an immediate call on a visibilitychange that leaves the document visible", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );

    doc.dispatchVisibilityChange(); // hidden stays false — not a hidden->visible edge

    expect(callback).not.toHaveBeenCalled();
  });
});

describe("startVisibilityPausedPoll — teardown", () => {
  it("stops the interval once the returned teardown function is called", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    const stop = visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );
    vi.advanceTimersByTime(INTERVAL_MS);
    expect(callback).toHaveBeenCalledTimes(1);

    stop();
    vi.advanceTimersByTime(INTERVAL_MS * 3);

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("removes the visibilitychange listener on teardown — a resume dispatch after stop() does not refire", () => {
    const callback = vi.fn();
    const doc = createFakeDocument(false);
    const win = createFakeWindow();

    const stop = visibilityPausedPoll.startVisibilityPausedPoll(
      callback,
      INTERVAL_MS,
      doc,
      win,
    );

    stop();

    doc.hidden = true;
    doc.dispatchVisibilityChange();
    doc.hidden = false;
    doc.dispatchVisibilityChange();

    expect(callback).not.toHaveBeenCalled();
  });
});
