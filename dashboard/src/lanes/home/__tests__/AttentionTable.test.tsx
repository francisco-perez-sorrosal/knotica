import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AttentionRow } from "../../../types";
import { AttentionTable } from "../AttentionTable";
import { ATTENTION_KIND_META } from "../attentionMeta";

/**
 * `AttentionTable`'s rank column and per-row rationale `TermHint`
 * (`ATTENTION_KIND_META`) -- rows arrive pre-sorted from `HomeLane`
 * (`sortAttentionRows`), so this suite only asserts the table renders rank
 * in the order it is given, never re-sorts.
 */

afterEach(cleanup);

const BLOCKED_ROW: AttentionRow = {
  topic: "rag-patterns",
  lane: "fill",
  urgency: "blocked",
  kind: "refused_rework",
  narration: "1 suggestion(s) refused, awaiting rework.",
  action: "Open",
};

const WAITING_ROW: AttentionRow = {
  topic: "gap-fill",
  lane: "fill",
  urgency: "waiting",
  kind: "pending_suggestions",
  narration: "4 suggestion(s) pending review.",
  action: "Open",
};

const RUNNING_ROW: AttentionRow = {
  topic: "agentic-systems",
  lane: "improve",
  urgency: "running",
  kind: "runner_active",
  narration: "A loop runner is active.",
  action: "Watch",
};

describe("rank column", () => {
  it("renders a leading #1..#N rank cell in the given (already-sorted) order", () => {
    const { container } = render(
      <AttentionTable
        rows={[BLOCKED_ROW, WAITING_ROW, RUNNING_ROW]}
        onOpenAnchor={vi.fn()}
      />,
    );

    const ranks = Array.from(
      container.querySelectorAll(".attention-table-rank"),
    ).map((cell) => cell.textContent);
    expect(ranks).toEqual(["#1", "#2", "#3"]);
  });

  it("does not re-sort -- rank tracks array position, not urgency", () => {
    const { container } = render(
      <AttentionTable
        rows={[RUNNING_ROW, BLOCKED_ROW]}
        onOpenAnchor={vi.fn()}
      />,
    );

    const rowEls = container.querySelectorAll("tbody tr");
    expect(rowEls[0].getAttribute("data-urgency")).toBe("running");
    expect(rowEls[0].querySelector(".attention-table-rank")?.textContent).toBe(
      "#1",
    );
    expect(rowEls[1].getAttribute("data-urgency")).toBe("blocked");
    expect(rowEls[1].querySelector(".attention-table-rank")?.textContent).toBe(
      "#2",
    );
  });
});

describe("per-row rationale TermHint", () => {
  it("opens onto its kind's why/unlocks copy", () => {
    render(<AttentionTable rows={[BLOCKED_ROW]} onOpenAnchor={vi.fn()} />);

    fireEvent.click(
      screen.getByRole("button", { name: "blocked — what this means" }),
    );

    const meta = ATTENTION_KIND_META["refused_rework"];
    const note = screen.getByRole("note");
    expect(note.textContent).toContain(meta.why);
    expect(note.textContent).toContain(meta.unlocks);
  });

  it("a different row's hint carries its own kind's copy, not another row's", () => {
    render(
      <AttentionTable rows={[WAITING_ROW]} onOpenAnchor={vi.fn()} />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "waiting — what this means" }),
    );

    const meta = ATTENTION_KIND_META["pending_suggestions"];
    const note = screen.getByRole("note");
    expect(note.textContent).toContain(meta.why);
    expect(note.textContent).not.toContain(
      ATTENTION_KIND_META["refused_rework"].why,
    );
  });
});

describe("Urgency column header", () => {
  it("hosts a TermHint stating the blocked > waiting > running ordering rule", () => {
    render(<AttentionTable rows={[BLOCKED_ROW]} onOpenAnchor={vi.fn()} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Urgency — what this means" }),
    );

    expect(screen.getByRole("note").textContent).toMatch(
      /blocked outranks waiting outranks running/i,
    );
  });
});

describe("row action routes to the stage its kind names, not just the lane", () => {
  it("[Open] on the blocked row lands on fill's Gate -- where the refusal verdict is written", () => {
    const onOpenAnchor = vi.fn();
    render(<AttentionTable rows={[BLOCKED_ROW]} onOpenAnchor={onOpenAnchor} />);

    fireEvent.click(screen.getByRole("button", { name: /^open$/i }));

    expect(onOpenAnchor).toHaveBeenCalledWith("fill", "gate");
  });

  it("[Open] on the pending row lands on fill's Approve -- the same lane, a different stage", () => {
    const onOpenAnchor = vi.fn();
    render(<AttentionTable rows={[WAITING_ROW]} onOpenAnchor={onOpenAnchor} />);

    fireEvent.click(screen.getByRole("button", { name: /^open$/i }));

    expect(onOpenAnchor).toHaveBeenCalledWith("fill", "approve");
  });

  it("[Watch] on the running row lands on improve's Observe, where the trend is", () => {
    const onOpenAnchor = vi.fn();
    render(<AttentionTable rows={[RUNNING_ROW]} onOpenAnchor={onOpenAnchor} />);

    fireEvent.click(screen.getByRole("button", { name: /^watch$/i }));

    expect(onOpenAnchor).toHaveBeenCalledWith("improve", "observe");
  });
});
