import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { EmptyState } from "../EmptyState";

/**
 * The centred icon/title/sentence/one-action template
 * replacing the bare `<p>Nothing needs you.</p>` and `<aside role="alert">`
 * patterns that carried no icon, no hierarchy, and no route to a fix.
 */

afterEach(cleanup);

describe("EmptyState", () => {
  it("renders an icon, an uppercase title, one sentence, and one action", () => {
    render(
      <EmptyState
        icon="state:complete"
        title="NOTHING NEEDS YOU"
        sentence="Every topic is settled. The loop runs on its own until something wants a decision."
        action={<button type="button">Open Improve</button>}
      />,
    );

    expect(document.querySelector(".empty-state-icon svg")).toBeTruthy();
    expect(screen.getByText("NOTHING NEEDS YOU")).toBeTruthy();
    expect(
      screen.getByText(
        "Every topic is settled. The loop runs on its own until something wants a decision.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open Improve" })).toBeTruthy();
  });

  it("renders extra content between the sentence and the action, when supplied", () => {
    render(
      <EmptyState
        icon="lane:tend"
        title="SERVER UNREACHABLE"
        sentence="Cannot reach the knotica server at 127.0.0.1:8765."
        action={<button type="button">Retry</button>}
      >
        <code>knotica mcp --http --port 8765</code>
      </EmptyState>,
    );

    expect(screen.getByText("knotica mcp --http --port 8765")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("renders no action element when none is supplied", () => {
    render(
      <EmptyState
        icon="lane:fill"
        title="NO TOPICS YET"
        sentence="This knowledge base has no topics."
      />,
    );

    expect(document.querySelector(".empty-state-action")).toBeNull();
  });
});
