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
