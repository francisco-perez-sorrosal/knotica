/** `?pane=` deep-link routing — the single reader of the `pane` search param. */

import type { PaneId } from "./types";

/**
 * Every `?pane=` value the dashboard accepts, mapped to the pane it opens.
 * Keys are the allowlist; a key whose value differs from itself is a legacy
 * alias kept working after the pane was renamed (`golden` → `datasets`), or
 * one of the six process lanes resolving to the pane that best represents it
 * today (`learn` → `ingest`, `answer` → `ask`, `improve` → `loop`,
 * `fill` → `sources`; `home` and `tend` both land on `vault`). Exported so a
 * caller can confirm a key is actually in the allowlist, not merely falling
 * through to the same default an unrecognised value would get.
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
  ["improve", "loop"],
  ["fill", "sources"],
  ["tend", "vault"],
]);

const DEFAULT_PANE: PaneId = "vault";

/** Resolve a raw `?pane=` value (or `null`, when absent) to the pane to open. */
export function resolvePane(param: string | null): PaneId {
  if (param === null) return DEFAULT_PANE;
  return PANE_BY_PARAM.get(param) ?? DEFAULT_PANE;
}
