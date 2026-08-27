/**
 * How a note's intent, its anchor status, and a pending note mutation are
 * presented. Extracted verbatim from `NotesDriftView.tsx` when the drift
 * review queue and the notes browser dissolved into Tend's `drift` stage:
 * the queue and the browser are gone, these four building blocks are not —
 * `DriftStage.tsx` renders all of them.
 *
 * Kept as its own module rather than folded into its single caller so the
 * vocabulary stays where a second surface can find it — the glyph/tone
 * tables are the shared reading of anchor health, not one stage's private
 * detail.
 */

import type {
  AnchorProjectionStatus,
  NoteDecisionEnvelope,
  NoteIntent,
} from "./types";

/** Intent -> (shape glyph, tone class). */
export const INTENT_TREATMENT: Record<
  NoteIntent,
  { glyph: string; tone: string }
> = {
  reflection: { glyph: "✎", tone: "" },
  dispute: { glyph: "⚑", tone: "warn" },
  gap: { glyph: "◆", tone: "warn" },
  question: { glyph: "?", tone: "" },
};

/**
 * Anchor status -> (glyph, tone, label, one-line meaning).
 *
 * `unanchored` and `orphaned` look alike and mean opposites, so they are
 * pulled apart deliberately: `unanchored` is the ordinary outcome of a note
 * filed against the topic -- neutral tone, no warning glyph, nothing to act
 * on. `orphaned` means the wiki moved out from under a pin the note *did*
 * make, and is one of the two buckets that earn the bad tone.
 */
export const ANCHOR_TREATMENT: Record<
  AnchorProjectionStatus,
  { glyph: string; tone: string; label: string; meaning: string }
> = {
  exact: {
    glyph: "●",
    tone: "ok",
    label: "exact",
    meaning: "the pinned passage is still there, word for word.",
  },
  unanchored: {
    glyph: "○",
    tone: "",
    label: "unanchored",
    meaning:
      "filed against the topic, never pinned to a page — normal, nothing to fix.",
  },
  shifted: {
    glyph: "◐",
    tone: "warn",
    label: "shifted",
    meaning: "the passage moved or was reworded, but survives.",
  },
  // `◔` not `○`: the glyphs read as a fill-level continuum of how much of the
  // pinned passage survives -- ● exact, ◐ shifted, ◔ fuzzy, ⌫ orphaned -- with
  // the empty `○` reserved for `unanchored`, which never had an anchor to lose.
  fuzzy: {
    glyph: "◔",
    tone: "warn",
    label: "fuzzy",
    meaning: "a similar passage was found but it is not an exact match.",
  },
  orphaned: {
    glyph: "⌫",
    tone: "bad",
    label: "orphaned",
    meaning: "the passage this pointed at is gone from the page.",
  },
  // `⊘` sits deliberately outside the ●◐◔⌫○ fill-continuum: those grade *how
  // much of the pinned passage survives*, and this status is not a point on
  // that scale at all -- the record never located its own quote in the blob
  // it was pinned against, so it is corruption, not loss.
  "anchor-invalid": {
    glyph: "⊘",
    tone: "bad",
    label: "unresolvable",
    meaning:
      "this anchor never located a page — the record itself is unusable.",
  },
};

/** The page's bare stem — what a person would call it out loud. */
export function pageLabel(page: string): string {
  if (!page) return "";
  const file = page.split("/").pop() ?? page;
  return file.endsWith(".md") ? file.slice(0, -".md".length) : file;
}

/**
 * Shared confirm banner for every mutating action's ``mode=dry-run`` ->
 * ``mode=apply`` two-step -- re-anchor, detach and the browse card's archive
 * all render the same envelope shape, so there is exactly one preview/confirm
 * implementation regardless of which affordance opened it.
 */
export function ActionConfirm({
  envelope,
  busy,
  applyLabel = "Confirm",
  onConfirm,
  onCancel,
}: {
  envelope: NoteDecisionEnvelope;
  busy: boolean;
  applyLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const preview = envelope.options[0]?.preview ?? envelope.summary;
  return (
    <div class="notes-action-confirm" role="status" aria-live="polite">
      <p>{preview}</p>
      <div class="notes-action-confirm-buttons">
        <button
          type="button"
          class="primary"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "…" : applyLabel}
        </button>
        <button type="button" class="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
