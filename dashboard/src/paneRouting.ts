/** `?pane=` deep-link routing — the single reader of the `pane` search param. */

import type { PaneId } from "./types";

/**
 * Every `?pane=` value the dashboard accepts, mapped to the pane it opens.
 * Keys are the allowlist; a key whose value differs from itself is a legacy
 * alias kept working after the pane was renamed (`golden` → `datasets`), or
 * one of the four still-topical process lanes resolving to the pane that
 * best represents it today (`learn` → `ingest`, `answer` → `ask`,
 * `fill` → `sources`; `home` lands on `vault`). `improve` and `tend` resolve
 * to their own real lane panes now that `ImproveLane`/`TendLane` are mounted
 * (the dissolution's add phase) — Step 79's removal phase retires the panes
 * they absorb. Exported so a caller can confirm a key is actually in the
 * allowlist, not merely falling through to the same default an unrecognised
 * value would get.
 *
 * Resolution is **exact-match, no trimming, no case folding** — uniformly for
 * legacy pane keys and lane keys alike. A mistyped case or stray whitespace
 * falls back to the default pane, exactly as any other unrecognised value
 * always has; this extension does not loosen that rule for the new keys.
 */
export const PANE_BY_PARAM = new Map<string, PaneId>([
  ["datasets", "datasets"],
  ["golden", "datasets"],
  ["ingest", "ingest"],
  ["loop", "loop"],
  ["ask", "ask"],
  ["arena", "arena"],
  ["sources", "sources"],
  ["notes", "notes"],
  ["home", "vault"],
  ["learn", "ingest"],
  ["answer", "ask"],
  ["improve", "improve"],
  ["fill", "sources"],
  ["tend", "tend"],
]);

const DEFAULT_PANE: PaneId = "vault";

/**
 * The `(lane, focus)` pairs that open a different pane than the bare lane would.
 * Keyed on the pair — a `focus` meaningful under one lane means nothing under
 * another. Internal on purpose: unlike `PANE_BY_PARAM` (which the `?pane=`
 * allowlist check reads from outside), nothing outside this module needs to
 * enumerate the qualified pairs; they are observable through `resolveLaneFocus`.
 */
const PANE_BY_LANE_FOCUS = new Map<string, PaneId>([
  ["improve:heal", "arena"],
  ["improve:instrument", "datasets"],
  ["tend:drift", "notes"],
]);

/** Resolve a raw `?pane=` value (or `null`, when absent) to the pane to open. */
export function resolvePane(param: string | null): PaneId {
  if (param === null) return DEFAULT_PANE;
  return PANE_BY_PARAM.get(param) ?? DEFAULT_PANE;
}

/**
 * Resolve an `open_dashboard(lane=, focus=)` pair to the pane to open.
 *
 * Degrade-never-error (`dec-092`) governs every branch: an unmatched `focus`
 * falls through to the lane's own unqualified mapping, and an unrecognised
 * `lane` degrades to home's own pane — the same watermark `resolvePane`
 * already defaults to. Matching is exact, with no trimming or case folding,
 * uniformly with `resolvePane`.
 */
export function resolveLaneFocus(lane: string, focus: string): PaneId {
  const qualified = focus
    ? PANE_BY_LANE_FOCUS.get(`${lane}:${focus}`)
    : undefined;
  return qualified ?? PANE_BY_PARAM.get(lane) ?? DEFAULT_PANE;
}
