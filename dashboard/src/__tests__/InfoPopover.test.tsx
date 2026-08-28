import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { InfoPopover } from "../InfoPopover";

/**
 * The non-modal three-slot overlay primitive (design §7.1). Every assertion
 * here traces to one row of that interaction table: single-open, Escape +
 * focus return, outside-click, and the `role="note"` (never `dialog`)
 * accessibility-tree contract that keeps it distinct from `ArmedButton`'s
 * confirmation grammar.
 */

afterEach(cleanup);

describe("InfoPopover", () => {
  it("renders a note, never a dialog", () => {
    render(
      <InfoPopover
        id="popover:test:role"
        title="Observe"
        ariaLabel="About Observe"
        whatThisIs="Runs the eval harness."
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "About Observe" }));

    const panel = screen.getByRole("note");
    expect(panel).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps at most one popover open at a time", () => {
    render(
      <>
        <InfoPopover
          id="popover:test:a"
          title="A"
          ariaLabel="About A"
          whatThisIs="First target."
        />
        <InfoPopover
          id="popover:test:b"
          title="B"
          ariaLabel="About B"
          whatThisIs="Second target."
        />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "About A" }));
    expect(screen.getByText("First target.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "About B" }));
    expect(screen.queryByText("First target.")).toBeNull();
    expect(screen.getByText("Second target.")).toBeTruthy();
  });

  it("closes on Escape and returns focus to the trigger", () => {
    render(
      <InfoPopover
        id="popover:test:escape"
        title="Gate"
        ariaLabel="About Gate"
        whatThisIs="Runs the promotion gate."
      />,
    );

    const trigger = screen.getByRole("button", { name: "About Gate" });
    fireEvent.click(trigger);
    expect(screen.getByRole("note")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("note")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes when a pointerdown lands outside the trigger and the panel", () => {
    render(
      <div>
        <InfoPopover
          id="popover:test:outside"
          title="Heal"
          ariaLabel="About Heal"
          whatThisIs="Applies a candidate fix."
        />
        <button type="button">elsewhere</button>
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "About Heal" }));
    expect(screen.getByRole("note")).toBeTruthy();

    fireEvent.pointerDown(screen.getByRole("button", { name: "elsewhere" }));

    expect(screen.queryByRole("note")).toBeNull();
  });

  it("omits the states slot when the target has no states", () => {
    render(
      <InfoPopover
        id="popover:test:no-states"
        title="Vault path"
        ariaLabel="About the vault path"
        whatThisIs="Where this knowledge base lives on disk."
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "About the vault path" }));

    expect(screen.queryByText("What the states mean")).toBeNull();
  });
});
