import type { ProcessId, ProcessMeta } from "../processContract";

/**
 * The two vault-level processes, both triggered from the app chrome.
 *
 * Keyed by `Extract<ProcessId, "vault.*">` rather than by a hand-written union:
 * the `Record` is exhaustive over exactly the ids in this namespace, so a new
 * `vault.*` id added to `ProcessId` is a compile error here until its row is
 * written, and a row belonging to another namespace cannot be filed here by
 * accident. The id namespace is the split axis because rows change together
 * per lane -- which is how every migration wave was scoped.
 */
export const VAULT_PROCESSES: Record<
  Extract<ProcessId, `vault.${string}`>,
  ProcessMeta
> = {
  "vault.create": {
    lane: "learn",
    stage: null,
    // Ships under the same `Create` label as the topic form beside it; they
    // are two forms in one drawer, told apart by which fields they carry.
    title: "Create",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "vaultCreate",
    why: "A wiki lives in its own git repository at a path you choose, and until one exists there is nowhere for a topic, a page, a note or an eval to be written.",
    willDo:
      "Creates the vault at the path you gave, seeds its first topic if you named one, and switches the dashboard to it. Nothing is billed. It writes a new repository and never touches a vault that already exists.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback:
      "The knowledge base is created and the dashboard is now reading it.",
    next: {
      kind: "always",
      go: {
        lane: "learn",
        stage: "source",
        why: "A new knowledge base has no pages — storing a source is what gives it something to be a wiki about.",
      },
    },
  },

  "vault.use": {
    // Per-vault and mechanical, which is Tend's half of the lane
    // discriminator; the switch itself sits in the chrome.
    lane: "tend",
    stage: null,
    title: "Switch vault",
    spend: "free",
    // Rewrites which vault is active in the config; no wiki content changes.
    mutates: false,
    dispatch: "client",
    clientMethod: "vaultUse",
    why: "Every number on screen — the baseline, the queues, the drift count, the flywheel chip — was read from one vault, and switching replaces all of them at once with another vault's without saying so.",
    willDo:
      "Points the server at the vault you picked and re-reads everything for it. Nothing is billed and no wiki content changes — switching back is the same control.",
    previewMode: "none",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback:
      "The dashboard is now reading the vault you picked; every number on screen belongs to it.",
    next: {
      kind: "always",
      go: {
        lane: "home",
        // Home is the only lane with no stages, and the only surface that
        // re-reads every topic at once -- which is exactly what a vault
        // switch invalidates.
        stage: null,
        why: "Everything you knew a moment ago belonged to the other vault, and Home is the one surface that re-reads every topic so you can see what this one is asking for.",
      },
    },
  },
};
