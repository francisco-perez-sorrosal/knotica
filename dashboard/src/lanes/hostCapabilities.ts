// Pure dispatch-tier derivation from host capabilities
// (INTERFACE_DESIGN.md §3.4).
//
// Framework-free: no Preact import, no DOM, no fetch. Dispatch is
// progressive enhancement down to the copyable-text floor (dec-091) — every
// field on the real `McpUiHostCapabilities` the host advertises is optional,
// so this function tolerates an absent capabilities object and an absent
// field alike, never assuming either is present. `mount === "bridge"` is
// checked exactly once, here — no lane may re-derive tier from the mount
// string directly (dec-091's plumbing clause).

import type { McpUiHostCapabilities } from "@modelcontextprotocol/ext-apps";

export type DispatchTier = "A" | "B" | "C" | "D";

export type Mount = "http" | "bridge";

/** The slice of `McpUiHostCapabilities` `deriveDispatchTier` reads. */
export type HostCapabilities = Pick<
  McpUiHostCapabilities,
  "message" | "updateModelContext"
>;

/**
 * Resolves which of the four dispatch tiers (`INTERFACE_DESIGN.md §3.4`)
 * applies for a given mount and the capabilities the host advertised.
 *
 * - **A** — bridge mount, host advertises `message`: a turn can happen.
 * - **B** — bridge mount, host advertises `updateModelContext` but not
 *   `message`: context can be queued, but no turn starts.
 * - **C** — bridge mount, host advertises neither: nothing programmatic
 *   exists; the caller falls back to copy-to-clipboard.
 * - **D** — HTTP mount: no host to advertise from, so the mount alone
 *   decides — identical fallback to C regardless of `caps`.
 */
export function deriveDispatchTier(
  caps: HostCapabilities | undefined,
  mount: Mount,
): DispatchTier {
  if (mount !== "bridge") {
    return "D";
  }
  if (caps?.message !== undefined) {
    return "A";
  }
  if (caps?.updateModelContext !== undefined) {
    return "B";
  }
  return "C";
}
