import type { ProcessId, ProcessMeta } from "../processContract";

/**
 * Tend's six: the mutating maintenance family plus the CLI-only migration.
 *
 * Keyed by `Extract<ProcessId, "tend.*">` rather than by a hand-written union:
 * the `Record` is exhaustive over exactly the ids in this namespace, so a new
 * `tend.*` id added to `ProcessId` is a compile error here until its row is
 * written, and a row belonging to another namespace cannot be filed here by
 * accident. The id namespace is the split axis because rows change together
 * per lane -- which is how every migration wave was scoped.
 */
export const TEND_PROCESSES: Record<
  Extract<ProcessId, `tend.${string}`>,
  ProcessMeta
> = {
  "tend.migrate": {
    lane: "tend",
    stage: "migrate",
    title: "Copy",
    spend: "free",
    mutates: true,
    // The one `cli` row. There is no MCP surface for migrate, so the honest
    // affordance is the command itself -- and like a handoff, this surface
    // cannot see the run and may not claim to.
    dispatch: "cli",
    clientMethod: null,
    why: "A vault's on-disk layout can fall behind the schema this build expects, and one that has fallen behind is reported against by every later check without any of them being able to fix it.",
    willDo:
      "Nothing from here: this copies the CLI dry run for you to paste. The dry run only reports what would change — applying it is a second command you run yourself.",
    previewMode: "dry-run",
    progressMode: "external",
    outcomeMode: "external",
    next: {
      kind: "always",
      go: {
        lane: "tend",
        stage: "doctor",
        why: "A migration rewrites the layout every other check reads, so the health report is what confirms it landed clean.",
      },
    },
  },

  // The mutating Tend family. Every one of these is free, dry-run-first, and
  // reports itself by re-reading the surface it changed -- which is why they
  // are the first `outcomeFallback` users: without that sentence a successful
  // repair and a no-op look identical.
  "tend.okf_repair": {
    lane: "tend",
    stage: "okf",
    title: "Repair apply",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "okfRepair",
    why: "The vault has drifted from the Open Knowledge Format, and a vault that does not match the format cannot be read by anything that expects it.",
    willDo:
      "Rewrites the files the dry run listed and records one git commit. Nothing is billed. Reversible — the commit is a normal one you can revert.",
    previewMode: "dry-run",
    progressMode: "busy",
    // `OkfRepairResult` carries `files_changed` / `notes` / `commit_sha` and
    // no message field, so there is no server sentence to render verbatim.
    // The registry supplies the sentence instead of inventing a server change.
    outcomeMode: "refresh",
    outcomeFallback:
      "The repair ran and its changed files and commit are listed below.",
    next: {
      kind: "always",
      go: {
        lane: "tend",
        stage: "lint",
        why: "A repair rewrote pages, and lint is what proves those pages still validate against their schemas.",
      },
    },
  },

  "tend.note_reanchor": {
    lane: "tend",
    stage: "drift",
    // Ships under two labels -- `Re-anchor here` when the passage resolved
    // itself, `Re-anchor to selected` when you pick among alternatives. Same
    // process, same server action; the second is the first with a target.
    title: "Re-anchor here",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesReanchor",
    why: "The page moved under this note's anchor, so the note now points at text that is no longer there and cites something nobody can find.",
    willDo:
      "Repoints the anchor at the passage you chose and commits the note. Nothing is billed. Reversible — re-anchoring again moves it back.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The anchor now points at the passage you chose.",
    next: {
      kind: "terminal",
      why: "The note cites live text again — nothing downstream was waiting on it.",
    },
  },

  "tend.note_detach": {
    lane: "tend",
    stage: "drift",
    title: "Detach",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesDetach",
    why: "The passage this note pinned is gone rather than moved, so there is nothing left to re-anchor to and the broken anchor will be reported for as long as it exists.",
    willDo:
      "Removes the anchor and keeps the note itself, in one commit. Nothing is billed. Reversible in the sense that the note survives — the anchor does not, and re-pinning it is manual.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The anchor is gone; the note itself is kept.",
    next: {
      kind: "terminal",
      why: "The note stands on its own now and no longer claims a page it cannot point at.",
    },
  },

  "tend.note_promote": {
    lane: "tend",
    stage: "drift",
    title: "Promote…",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesPromote",
    why: "This note carries something the wiki should act on, and a note nobody promotes stays an annotation forever.",
    willDo:
      "Writes the note into the destination you pick — a training example or a filed gap — in one commit, after showing you the resolved question and grounding pages. Nothing is billed.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The note was written into the destination you picked.",
    next: {
      kind: "conditional",
      // The discriminant is the dialog's own `target` state -- already held by
      // the caller, never re-fetched.
      branches: [
        {
          when: "trainset",
          go: {
            lane: "improve",
            stage: "instrument",
            why: "A training example only matters once it is in the trainset a compile reads — that is where it turns into a better prompt.",
          },
        },
        {
          when: "gap",
          go: {
            lane: "fill",
            stage: "discover",
            why: "A filed gap that nobody discovers against stays open forever; discovery is what proposes sources to close it.",
          },
        },
      ],
      fallback: {
        lane: "tend",
        stage: "drift",
        why: "The destination was not one this build recognises — read the note's anchors here before deciding again.",
      },
    },
  },

  "tend.note_archive": {
    lane: "tend",
    stage: "drift",
    title: "Archive",
    spend: "free",
    mutates: true,
    dispatch: "client",
    clientMethod: "notesArchive",
    why: "This note has been dealt with or has stopped being true, and leaving it in the live set means every later drift scan re-reports it.",
    willDo:
      "Moves the note out of the live set and commits it. Nothing is billed. Reversible — an archived note is kept, not deleted.",
    previewMode: "dry-run",
    progressMode: "busy",
    outcomeMode: "refresh",
    outcomeFallback: "The note is archived and out of the live set.",
    next: {
      kind: "terminal",
      why: "The note is settled — archiving is how a note stops asking for attention.",
    },
  },
};
