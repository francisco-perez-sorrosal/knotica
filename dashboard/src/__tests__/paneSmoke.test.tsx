import { cleanup, render } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

// `ImproveLane`'s `observe` stage pulls in `uplot`, which calls
// `window.matchMedia` at import time -- unimplemented by jsdom. Mocked the
// same way `ObserveStage.test.tsx`/`loopPaneStepper.characterization.test.tsx`
// already do; `vi.mock` is hoisted above these imports by Vitest.
vi.mock("uplot", () => ({ default: class {} }));

import { AskPane } from "../AskPane";
import { IngestPane } from "../IngestPane";
import { SourcesPane } from "../SourcesPane";
import { ImproveLane } from "../lanes/improve/ImproveLane";
import { TendLane } from "../lanes/tend/TendLane";

/**
 * Full-tree smoke render for the five panes the dissolution's removal phase
 * (Step 79) leaves standing: `ingest`, `ask`, `sources`, `improve`, `tend`.
 * Catches an import-orphan `tsc --noEmit` alone cannot see -- a type that
 * still compiles but whose only remaining reference lives inside a file the
 * removal phase just deleted (a broken re-export chain, a shared helper
 * quietly moved, etc.), the same defect class the plan's own dissolution
 * commentary calls out.
 *
 * All five components already exist and mount cleanly today (Steps 65-77
 * landed them), so unlike `crossLaneLinkCensus.test.ts`'s file-absence
 * assertions this suite is green today by construction -- mirroring Step
 * 78's own "additive-only" regression net. Its value is as a load-bearing
 * guard once Step 79's deletion runs: if removing the nine dissolved files
 * (or their cross-lane props) accidentally breaks a survivor, this is the
 * suite that goes red.
 *
 * Minimal props throughout -- `client: null` and `status`/`metrics: null`
 * are valid, already-exercised states for every pane here (each renders its
 * own "no data yet" / disconnected affordance rather than throwing).
 */

afterEach(cleanup);

describe("every remaining pane mounts without throwing or rendering empty", () => {
  it("renders IngestPane", () => {
    const { container } = render(
      <IngestPane client={null} topic="t" vault="v" obsidianCtx={{}} />,
    );
    expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  it("renders AskPane", () => {
    const { container } = render(
      <AskPane
        client={null}
        topic="t"
        vault="v"
        obsidianCtx={{}}
        status={null}
      />,
    );
    expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  it("renders SourcesPane", () => {
    const { container } = render(
      <SourcesPane client={null} topic="t" vault="v" />,
    );
    expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  it("renders ImproveLane", () => {
    const { container } = render(
      <ImproveLane
        client={null}
        topic="t"
        vault="v"
        status={null}
        metrics={null}
        obsidianCtx={{}}
      />,
    );
    expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });

  it("renders TendLane", () => {
    const { container } = render(
      <TendLane client={null} vault="v" topic="t" obsidianCtx={{}} />,
    );
    expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
  });
});
