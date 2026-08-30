import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import { PROCESS_META } from "../processMeta";

/**
 * The two lifecycle compositions, tested for the behaviour the registry
 * promises rather than for their markup.
 *
 * `processMeta.test.ts` asserts that every row *has* the six answers; this
 * suite asserts that a surface mounting these components actually *shows*
 * them, and — the part worth guarding — that the two honesty rules hold:
 * a `refresh` process never renders a blank outcome, and a component whose
 * caller already owns the live region does not announce a second time.
 */

afterEach(cleanup);

describe("ProcessBrief shows why a click is necessary and what it will do", () => {
  it("keeps both answers behind one trigger and reveals them together", () => {
    const meta = PROCESS_META["tend.note_detach"];
    render(<ProcessBrief process="tend.note_detach" />);

    expect(screen.queryByText(meta.why)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /why this/i }));

    expect(screen.getByText(meta.why)).toBeTruthy();
    expect(screen.getByText(meta.willDo)).toBeTruthy();
  });

  it("prices a billed process on a chip beside the trigger, never inside it", () => {
    const { container } = render(<ProcessBrief process="improve.run_eval" />);

    expect(container.querySelector(".chip.cost")?.textContent).toBe("billed");
    // The chip is a sibling of the hint trigger, so no control's accessible
    // name absorbs the price -- the house rule the two-phase flows rely on.
    expect(
      screen.getByRole("button", { name: /why this/i }).textContent,
    ).not.toMatch(/billed/i);
  });

  it("says nothing about cost for a free process", () => {
    const { container } = render(<ProcessBrief process="tend.note_archive" />);
    expect(container.querySelector(".chip.cost")).toBeNull();
  });
});

describe("ProcessOutcome says what was done and where it leads", () => {
  it("supplies the registry sentence when the only visible change is a re-read", () => {
    const meta = PROCESS_META["tend.note_archive"];
    render(<ProcessOutcome process="tend.note_archive" />);

    const status = screen.getByRole("status");
    expect(status.textContent).toBe(meta.outcomeFallback);
  });

  it("stays silent when the caller already announced the server's own sentence", () => {
    // `verdict`/`result` processes render their message in the caller's live
    // region; a second one here would announce the same event twice.
    render(<ProcessOutcome process="improve.gate_candidate" />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("routes a conditional next on the discriminant the caller already holds", () => {
    render(
      <ProcessOutcome process="improve.gate_candidate" discriminant="pass" />,
    );
    expect(screen.getByText(/Go to Improve → Promote\./)).toBeTruthy();

    cleanup();
    render(
      <ProcessOutcome process="improve.gate_candidate" discriminant="fail" />,
    );
    expect(screen.getByText(/Go to Improve → Heal\./)).toBeTruthy();
  });

  it("still names a destination for a verdict this build does not know", () => {
    render(
      <ProcessOutcome process="improve.gate_candidate" discriminant="wat" />,
    );
    // The fallback exists precisely so a server ahead of the client cannot
    // produce a dead end.
    expect(screen.getByText(/Go to Improve → Observe\./)).toBeTruthy();
  });

  it("answers a terminal process with why it ends rather than with nothing", () => {
    const meta = PROCESS_META["tend.note_detach"];
    const { container } = render(<ProcessOutcome process="tend.note_detach" />);

    expect(container.textContent).toContain("NEXT STEP");
    if (meta.next.kind === "terminal") {
      expect(container.textContent).toContain(meta.next.why);
    }
    expect(container.textContent).not.toMatch(/Go to/);
  });
});
