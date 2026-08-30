import { describe, expect, it } from "vitest";

import { initialFocus } from "../stageFocus";

/**
 * The focus axis's one piece of pure logic (design §5.3). The hook itself is
 * exercised through `ImproveLane.test.tsx`, where focus is observable as
 * rendered behaviour rather than as a return value.
 */
describe("initialFocus", () => {
  it("opens the stage the server says is active", () => {
    expect(
      initialFocus([
        { id: "instrument", state: "complete" },
        { id: "observe", state: "active" },
        { id: "gate", state: "pending" },
      ]),
    ).toBe("observe");
  });

  it("opens a blocked stage too -- blocked is a modifier on the position, not a separate one", () => {
    expect(
      initialFocus([
        { id: "instrument", state: "complete" },
        { id: "observe", state: "blocked" },
      ]),
    ).toBe("observe");
  });

  it("opens nothing when the process is idle", () => {
    expect(
      initialFocus([
        { id: "instrument", state: "pending" },
        { id: "observe", state: "pending" },
      ]),
    ).toBeNull();
  });

  it("opens nothing when the process is terminal", () => {
    expect(
      initialFocus([
        { id: "instrument", state: "complete" },
        { id: "observe", state: "complete" },
      ]),
    ).toBeNull();
  });

  it("opens nothing for an empty rail", () => {
    expect(initialFocus([])).toBeNull();
  });
});

/**
 * The arrival request -- the one thing allowed to seed focus. These are added
 * beside the cases above rather than replacing any of them: the guardrail this
 * axis exists to protect is *focus is never stolen*, and a request is a second
 * way to violate it, so every pre-existing expectation must still hold with a
 * request in play.
 */
describe("initialFocus with an arrival request", () => {
  const RAIL = [
    { id: "instrument", state: "complete" },
    { id: "observe", state: "active" },
    { id: "gate", state: "pending" },
  ] as const;

  it("honours a request naming a real stage, over the server's active one", () => {
    expect(initialFocus(RAIL, "gate")).toBe("gate");
  });

  it("falls back to the declared active stage when the request names no stage in this rail", () => {
    expect(initialFocus(RAIL, "not-a-stage")).toBe("observe");
  });

  it("falls back for an absent request, which is the no-navigation case every poll takes", () => {
    expect(initialFocus(RAIL, null)).toBe("observe");
    expect(initialFocus(RAIL, undefined)).toBe("observe");
  });

  it("a request on an idle rail opens the requested stage, not nothing", () => {
    expect(
      initialFocus(
        [
          { id: "instrument", state: "pending" },
          { id: "gate", state: "pending" },
        ],
        "gate",
      ),
    ).toBe("gate");
  });

  it("an unmatched request on an idle rail still opens nothing", () => {
    expect(
      initialFocus([{ id: "instrument", state: "pending" }], "gate"),
    ).toBeNull();
  });
});
