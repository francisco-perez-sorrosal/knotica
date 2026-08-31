import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { InfoPopover } from "../InfoPopover";
import { TermHint } from "../TermHint";

/**
 * The inline dotted-underline explanatory overlay -- a
 * second overlay *class* sharing round 1's single-open signal
 * (`infoPopoverState.ts`), never a second overlay *system* (B3). At most
 * one overlay, `InfoPopover` or `TermHint`, is ever open.
 */

afterEach(cleanup);

describe("TermHint", () => {
  it("renders a note, never a dialog", () => {
    render(
      <TermHint
        id="term:test:role"
        term="0.66"
        title="Latest scalar"
        body="The score the last eval cycle got on the held-out set."
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "0.66 — what this means" }),
    );

    expect(screen.getByRole("note")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("has a real button trigger reachable by keyboard, not hover-only", () => {
    render(
      <TermHint
        id="term:test:keyboard"
        term="12"
        title="Latest gen"
        body="Each finished cycle."
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "12 — what this means",
    });
    expect(trigger.tagName).toBe("BUTTON");
  });

  it("names the trigger '<term> — what this means', never beginning with open/watch", () => {
    render(
      <TermHint
        id="term:test:name"
        term="every 6h"
        title="Cadence"
        body="The shortest gap."
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "every 6h — what this means",
    });
    expect(trigger).toBeTruthy();
    expect(trigger.getAttribute("aria-label")).not.toMatch(/^(open|watch)\b/i);
  });

  it("closes on Escape and returns focus to the trigger", () => {
    render(
      <TermHint
        id="term:test:escape"
        term="0.62"
        title="Baseline"
        body="The frozen stick."
      />,
    );

    const trigger = screen.getByRole("button", {
      name: "0.62 — what this means",
    });
    fireEvent.click(trigger);
    expect(screen.getByRole("note")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("note")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes when a pointerdown lands outside the trigger and the panel", () => {
    render(
      <div>
        <TermHint
          id="term:test:outside"
          term="40"
          title="Held-out"
          body="The frozen golden set."
        />
        <button type="button">elsewhere</button>
      </div>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "40 — what this means" }),
    );
    expect(screen.getByRole("note")).toBeTruthy();

    fireEvent.pointerDown(screen.getByRole("button", { name: "elsewhere" }));

    expect(screen.queryByRole("note")).toBeNull();
  });

  it("keeps at most one TermHint open at a time", () => {
    render(
      <>
        <TermHint
          id="term:test:a"
          term="A"
          title="A title"
          body="First target."
        />
        <TermHint
          id="term:test:b"
          term="B"
          title="B title"
          body="Second target."
        />
      </>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "A — what this means" }),
    );
    expect(screen.getByText("First target.")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "B — what this means" }),
    );
    expect(screen.queryByText("First target.")).toBeNull();
    expect(screen.getByText("Second target.")).toBeTruthy();
  });

  it("opening a TermHint closes an open InfoPopover", () => {
    render(
      <>
        <InfoPopover
          id="popover:shared:a"
          title="Observe"
          ariaLabel="About Observe"
          whatThisIs="Runs the eval harness."
        />
        <TermHint
          id="term:shared:a"
          term="0.66"
          title="Latest scalar"
          body="The score."
        />
      </>,
    );

    fireEvent.click(screen.getByRole("button", { name: "About Observe" }));
    expect(screen.getByText("Runs the eval harness.")).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "0.66 — what this means" }),
    );

    expect(screen.queryByText("Runs the eval harness.")).toBeNull();
    expect(screen.getByText("The score.")).toBeTruthy();
  });

  it("opening an InfoPopover closes an open TermHint", () => {
    render(
      <>
        <TermHint
          id="term:shared:b"
          term="0.66"
          title="Latest scalar"
          body="The score."
        />
        <InfoPopover
          id="popover:shared:b"
          title="Observe"
          ariaLabel="About Observe"
          whatThisIs="Runs the eval harness."
        />
      </>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "0.66 — what this means" }),
    );
    expect(screen.getByText("The score.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "About Observe" }));

    expect(screen.queryByText("The score.")).toBeNull();
    expect(screen.getByText("Runs the eval harness.")).toBeTruthy();
  });
});
