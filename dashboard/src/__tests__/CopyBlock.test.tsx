import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyBlock } from "../CopyBlock";

/**
 * The copy affordance replacing bare `<code>`/prose remediation hints
 * (design §3.5) -- the command a user is asked to run should be one click
 * from the clipboard, not a retype.
 */

afterEach(cleanup);

beforeEach(() => {
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

describe("CopyBlock", () => {
  it("renders the code and copies it to the clipboard on click", async () => {
    render(<CopyBlock code="tend action=notes notes_action=drift topic=decision-making" />);

    expect(screen.getByText("tend action=notes notes_action=drift topic=decision-making")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: /copy/i }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "tend action=notes notes_action=drift topic=decision-making",
    );
    expect(await screen.findByText("Copied")).toBeTruthy();
  });

  it("copies the payload, not the displayed code, when the two diverge", async () => {
    // The handoff dispatch line is the one case: the reader sees the bare
    // invocation, but `dec-091`'s payload leads with the prose a non-slash
    // host routes on.
    render(
      <CopyBlock
        code="/knotica:fill s_1a2b3c4d rag-patterns"
        copyText={"Claude writes the pages.\n\n/knotica:fill s_1a2b3c4d rag-patterns"}
      />,
    );

    expect(screen.getByText("/knotica:fill s_1a2b3c4d rag-patterns")).toBeTruthy();
    await fireEvent.click(screen.getByRole("button", { name: /copy/i }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "Claude writes the pages.\n\n/knotica:fill s_1a2b3c4d rag-patterns",
    );
  });

  it("takes its accessible name from actionLabel's visible text, never both", async () => {
    render(
      <CopyBlock code="/knotica:fill s_1a2b3c4d rag-patterns" actionLabel="Copy the instruction" />,
    );

    const action = screen.getByRole("button", { name: "Copy the instruction" });
    // The visible text is the name -- no `aria-label` shadowing it.
    expect(action.getAttribute("aria-label")).toBeNull();
    expect(action.getAttribute("data-labelled")).toBe("true");
    expect(screen.getByText("Copy the instruction")).toBeTruthy();
  });

  it("reports a failed copy rather than swallowing the error", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<CopyBlock code="knotica mcp --http --port 8765" />);

    await fireEvent.click(screen.getByRole("button", { name: /copy/i }));

    expect(await screen.findByText("Copy failed")).toBeTruthy();
  });
});
