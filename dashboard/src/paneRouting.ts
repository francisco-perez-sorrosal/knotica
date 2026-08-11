/** `?pane=` deep-link routing — the single reader of the `pane` search param. */

import type { PaneId } from "./types";

/**
 * Every `?pane=` value the dashboard accepts, mapped to the pane it opens.
 * Keys are the allowlist; a key whose value differs from itself is a legacy
 * alias kept working after the pane was renamed (`golden` → `datasets`).
 * `vault` is absent on purpose — it is reached by falling through to the default.
 */
const PANE_BY_PARAM = new Map<string, PaneId>([
  ["datasets", "datasets"],
  ["golden", "datasets"],
  ["ingest", "ingest"],
  ["loop", "loop"],
  ["ask", "ask"],
  ["arena", "arena"],
  ["sources", "sources"],
  ["notes", "notes"],
]);

const DEFAULT_PANE: PaneId = "vault";

/** Resolve a raw `?pane=` value (or `null`, when absent) to the pane to open. */
export function resolvePane(param: string | null): PaneId {
  if (param === null) return DEFAULT_PANE;
  return PANE_BY_PARAM.get(param) ?? DEFAULT_PANE;
}
