/** `?pane=` deep-link routing — the single reader of the `pane` search param. */

import type { PaneId } from "./types";

/**
 * Every `?pane=` value the dashboard accepts, mapped to the pane it opens.
 * Keys are the allowlist; a key whose value differs from itself is a
 * bookmark minted before a dissolution, degraded to the lane that absorbed
 * the pane it named: `loop`/`arena`/`datasets`/`golden` → `improve`,
 * `notes` → `tend`, and — once M4's removal phase retired the last three
 * tool-shaped panes — `ingest` → `learn`, `ask` → `answer`,
 * `sources` → `fill`. Only the six lane names self-map; every legacy key
 * keeps working and lands somewhere specific rather than falling through to
 * one generic default. Exported so a caller can confirm a key is actually in
 * the allowlist, not merely falling through to the same default an
 * unrecognised value would get.
 *
 * `home` was the one lane key that did not self-map: until Home shipped as a
 * lane of its own it was parked on `tend`, the interim default. It now names
 * the pane it always meant.
 *
 * `vault` is listed for the same reason every other legacy key is, even
 * though it was never written down before: it reached `tend` only because
 * `tend` was also the default, so the moment the default moved to `home` an
 * old `?pane=vault` bookmark would have stopped landing on the lane that
 * absorbed it. An alias that works only while it agrees with the default is
 * not an alias.
 *
 * Resolution is **exact-match, no trimming, no case folding** — uniformly for
 * legacy pane keys and lane keys alike. A mistyped case or stray whitespace
 * falls back to the default pane, exactly as any other unrecognised value
 * always has.
 */
export const PANE_BY_PARAM = new Map<string, PaneId>([
  ["datasets", "improve"],
  ["golden", "improve"],
  ["ingest", "learn"],
  ["loop", "improve"],
  ["ask", "answer"],
  ["arena", "improve"],
  ["sources", "fill"],
  ["notes", "tend"],
  ["vault", "tend"],
  ["home", "home"],
  ["learn", "learn"],
  ["answer", "answer"],
  ["improve", "improve"],
  ["fill", "fill"],
  ["tend", "tend"],
]);

/**
 * The pane a bare URL opens, and the one `?pane=` is omitted for.
 *
 * Home is the landing surface: it is cross-topic and actionable, so it is
 * what a session with no stated destination should open on. It is also where
 * an unrecognised `?pane=`/`?lane=` degrades to (`dec-092`) — a bad value
 * lands on the inbox that can route the user onward, never on an error.
 */
export const DEFAULT_PANE: PaneId = "home";

/** Resolve a raw `?pane=` value (or `null`, when absent) to the pane to open. */
export function resolvePane(param: string | null): PaneId {
  if (param === null) return DEFAULT_PANE;
  return PANE_BY_PARAM.get(param) ?? DEFAULT_PANE;
}

/**
 * Resolve an `open_dashboard(lane=, focus=)` pair to the pane to open.
 *
 * `focus` no longer selects a pane. It used to: three pairs
 * (`improve:heal`, `improve:instrument`, `tend:drift`) opened a tool-shaped
 * pane instead of the lane's own, and all three of those panes were absorbed
 * by the lane that now owns the work. A focus is a *within-lane* coordinate
 * now — which stage that lane expands — so it never changes which pane opens,
 * and the parameter is accepted here only to keep the deep-link contract's
 * shape intact for the caller.
 *
 * Degrade-never-error (`dec-092`) still governs: an unrecognised `lane`
 * degrades to the default pane rather than erroring. Matching is exact, with
 * no trimming or case folding, uniformly with `resolvePane`.
 */
export function resolveLaneFocus(lane: string, _focus: string): PaneId {
  return PANE_BY_PARAM.get(lane) ?? DEFAULT_PANE;
}
