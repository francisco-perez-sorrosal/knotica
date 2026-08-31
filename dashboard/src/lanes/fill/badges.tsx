import type { JSX } from "preact";

import type { GapOrigin, SuggestionReputability } from "./types";

/**
 * The two provenance badges shared by the gap queue (`QueueStage`'s `GapCard`)
 * and the suggestion triage rows (`SuggestionRow`). Extracted so the row file
 * does not import back into its own parent -- the tone treatments are data,
 * not stage logic.
 *
 * Both badges pair a shape glyph with a visible word: tone is never the sole
 * carrier of meaning (WCAG 1.4.1).
 */

/** Tier -> (shape glyph, tone class) -- never color alone (WCAG 1.4.1). */
const TIER_TREATMENT: Record<string, { glyph: string; tone: string }> = {
  peer_reviewed: { glyph: "●", tone: "ok" },
  preprint_known_lab: { glyph: "◐", tone: "warn" },
  established_org: { glyph: "○", tone: "warn" },
  general_web: { glyph: "·", tone: "" },
};

/** Gap origin -> (shape glyph, tone class, label) -- shape + label, never color alone. */
const ORIGIN_TREATMENT: Record<
  GapOrigin,
  { glyph: string; tone: string; label: string }
> = {
  measured: { glyph: "◆", tone: "ok", label: "measured" }, // eval-proven
  reported: { glyph: "✎", tone: "warn", label: "reported" }, // conversationally filed
  retracted: { glyph: "⌫", tone: "warn", label: "retracted" }, // guillotine-weakened
};

/**
 * The `data-tier` value a triage row's left edge is toned from. `"none"` is a
 * real value, not a missing attribute: an ungraded candidate reads neutral,
 * which is different from "no edge at all".
 */
export function tierKey(reputability: SuggestionReputability | null): string {
  return reputability?.tier ?? "none";
}

export function GapOriginBadge({
  origin,
}: {
  origin?: GapOrigin | null;
}): JSX.Element | null {
  if (!origin) return null; // older records carry no provenance -- omit the badge
  const treatment = ORIGIN_TREATMENT[origin];
  if (!treatment) return null;
  return (
    <span
      class={`health-chip sources-origin ${treatment.tone}`}
      title={`gap origin: ${treatment.label}`}
    >
      <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
    </span>
  );
}

/**
 * The tier word, without its score.
 *
 * The numeric score is rendered as a tabular `rep 0.87` metric beside this
 * badge, so repeating it inside the badge would print the same number twice on
 * every row -- the exact duplication this pass exists to remove. The badge
 * carries the *word* (and its glyph); the metric carries the *number*.
 */
export function ReputabilityBadge({
  reputability,
}: {
  reputability: SuggestionReputability | null;
}): JSX.Element | null {
  if (!reputability) return null;
  const treatment =
    TIER_TREATMENT[reputability.tier] ?? TIER_TREATMENT.general_web;
  return (
    <span class={`health-chip sources-reputability ${treatment.tone}`}>
      <span aria-hidden="true">{treatment.glyph}</span>{" "}
      {reputability.tier.replace(/_/g, " ")}
    </span>
  );
}
