import { describe, expect, it } from "vitest";

import {
  flywheelLabel,
  flywheelTone,
  isCompileActive,
} from "../compileStages";

describe("dashboard test runner", () => {
  it("imports a pure source module and sees its exports", () => {
    expect(typeof isCompileActive).toBe("function");
    expect(typeof flywheelLabel).toBe("function");
    expect(typeof flywheelTone).toBe("function");
  });

  it("evaluates an imported export", () => {
    expect(isCompileActive("optimizing")).toBe(true);
    expect(flywheelLabel({ compiledPresent: true })).toBe("Compiled");
    expect(flywheelTone("Compiled")).toBe("ok");
  });
});
