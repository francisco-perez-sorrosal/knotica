import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

/**
 * M3 entry gate: proves the DOM test environment actually works end to end --
 * `jsdom` renders a Preact component, and `@testing-library/preact`'s
 * `render`/`screen` can query what landed in it. Every later characterization
 * and rail-rendering test in this milestone depends on this wiring; if it
 * breaks, this is the file that goes red first.
 *
 * `vitest.config.ts` now sets `test.environment: "jsdom"` globally -- Vitest
 * 4 removed the per-glob (`environmentMatchGlobs`) and per-file
 * (`@vitest-environment` docblock) environment overrides that older Vitest
 * versions offered, so a per-file split is not available at this pin. The
 * suite is small enough (7 pre-existing files) that this is a non-issue in
 * practice.
 *
 * `@testing-library/preact` auto-registers `afterEach(cleanup)` only when
 * `afterEach` is a test-runner global -- this project imports test globals
 * explicitly rather than enabling vitest's `globals: true`, so `cleanup` is
 * called by hand. Later DOM test files should do the same.
 */

function Greeting({ name }: { name: string }) {
  return <p>Hello, {name}!</p>;
}

afterEach(cleanup);

describe("the DOM test environment", () => {
  it("renders a Preact component and queries it via testing-library", () => {
    render(<Greeting name="dashboard" />);

    expect(screen.getByText("Hello, dashboard!")).toBeTruthy();
  });
});
