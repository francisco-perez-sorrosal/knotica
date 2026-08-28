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
    render(<CopyBlock code="knotica notes drift --topic decision-making" />);

    expect(screen.getByText("knotica notes drift --topic decision-making")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: /copy/i }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "knotica notes drift --topic decision-making",
    );
    expect(await screen.findByText("Copied")).toBeTruthy();
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
