/**
 * Tend's half of `ToolClient`: per-vault maintenance.
 *
 * Two families, both mechanical and both vault-scoped rather than
 * topic-scoped: the `vault_health` dispatcher (doctor, lint, OKF, the metadata
 * tree) and the whole personal-notes overlay (list, read, drift, and the four
 * mutating actions). Notes live here for the same reason their types do --
 * Tend's drift stage is their only lane surface, and the family is densely
 * self-referential.
 */

import type { ToolCallGroup } from "../../toolClientCore";

import type {
  AnchorStatusFilter,
  DoctorRepairResult,
  DoctorReport,
  NoteAnchorActionResult,
  NoteArchiveActionResult,
  NoteDecisionEnvelope,
  NoteIntentFilter,
  NotePromoteActionResult,
  NoteReadResult,
  NotesDriftResult,
  NotesListResult,
  OkfCheckResult,
  OkfRepairResult,
  PromoteTarget,
  VaultLintResult,
  VaultMetadataTree,
} from "./types";

export interface TendToolCalls {
  doctorRun(
    vault?: string,
    quick?: boolean,
    fix?: boolean,
  ): Promise<DoctorReport>;
  doctorRepair(
    mode: "dry-run" | "apply",
    vault?: string,
    paths?: string[],
    allTracked?: boolean,
    deleteUntracked?: boolean,
  ): Promise<DoctorRepairResult>;
  vaultLint(topic?: string, vault?: string): Promise<VaultLintResult>;
  vaultMetadataTree(vault?: string, topic?: string): Promise<VaultMetadataTree>;
  okfCheck(vault?: string, strict?: boolean): Promise<OkfCheckResult>;
  okfRepair(
    mode: "dry-run" | "apply",
    vault?: string,
    force?: boolean,
  ): Promise<OkfRepairResult>;
  notesList(
    topic: string,
    intent?: NoteIntentFilter,
    status?: AnchorStatusFilter,
    cursor?: string,
    limit?: number,
    vault?: string,
  ): Promise<NotesListResult>;
  notesRead(
    topic: string,
    noteId: string,
    vault?: string,
  ): Promise<NoteReadResult>;
  notesDrift(
    topic: string,
    cursor?: string,
    limit?: number,
    vault?: string,
  ): Promise<NotesDriftResult>;
  notesReanchor(
    topic: string,
    noteId: string,
    anchorIndex: number,
    mode: "dry-run" | "apply",
    page?: string,
    quote?: string,
    vault?: string,
  ): Promise<NoteDecisionEnvelope | NoteAnchorActionResult>;
  notesDetach(
    topic: string,
    noteId: string,
    anchorIndex: number,
    mode: "dry-run" | "apply",
    vault?: string,
  ): Promise<NoteDecisionEnvelope | NoteAnchorActionResult>;
  notesPromote(
    topic: string,
    noteId: string,
    target: PromoteTarget,
    mode: "dry-run" | "apply",
    fields?: { question?: string; answer?: string; verdict?: "good" | "bad" },
    vault?: string,
  ): Promise<NoteDecisionEnvelope | NotePromoteActionResult>;
  notesArchive(
    topic: string,
    noteId: string,
    mode: "dry-run" | "apply",
    vault?: string,
  ): Promise<NoteDecisionEnvelope | NoteArchiveActionResult>;
}

export const tendToolCalls: ToolCallGroup<TendToolCalls> = {
  doctorRun(vault = "", quick = false, fix = false): Promise<DoctorReport> {
    return this.call("vault_health", { action: "doctor", vault, quick, fix });
  },

  doctorRepair(
    mode: "dry-run" | "apply",
    vault = "",
    paths: string[] = [],
    allTracked = false,
    deleteUntracked = false,
  ): Promise<DoctorRepairResult> {
    return this.call("vault_health", {
      action: "repair",
      mode,
      vault,
      paths_json: JSON.stringify(paths),
      all_tracked: allTracked,
      delete_untracked: deleteUntracked,
    });
  },

  vaultLint(topic = "", vault = ""): Promise<VaultLintResult> {
    return this.call("vault_health", { action: "lint", topic, vault });
  },

  vaultMetadataTree(vault = "", topic = ""): Promise<VaultMetadataTree> {
    return this.call("vault_health", { action: "metadata_tree", vault, topic });
  },

  okfCheck(vault = "", strict = false): Promise<OkfCheckResult> {
    return this.call("vault_health", { action: "okf_check", vault, strict });
  },

  okfRepair(
    mode: "dry-run" | "apply",
    vault = "",
    force = false,
  ): Promise<OkfRepairResult> {
    return this.call("vault_health", {
      action: "okf_repair",
      mode,
      vault,
      force,
    });
  },

  notesList(
    topic: string,
    intent: NoteIntentFilter = "all",
    status: AnchorStatusFilter = "all",
    cursor = "",
    limit = 20,
    vault = "",
  ): Promise<NotesListResult> {
    return this.call("notes", {
      action: "list",
      topic,
      intent,
      status,
      cursor,
      limit,
      vault,
    });
  },

  notesRead(
    topic: string,
    noteId: string,
    vault = "",
  ): Promise<NoteReadResult> {
    return this.call("notes", {
      action: "read",
      topic,
      note_id: noteId,
      vault,
    });
  },

  notesDrift(
    topic: string,
    cursor = "",
    limit = 20,
    vault = "",
  ): Promise<NotesDriftResult> {
    return this.call("notes", { action: "drift", topic, cursor, limit, vault });
  },

  notesReanchor(
    topic: string,
    noteId: string,
    anchorIndex: number,
    mode: "dry-run" | "apply" = "dry-run",
    page = "",
    quote = "",
    vault = "",
  ): Promise<NoteDecisionEnvelope | NoteAnchorActionResult> {
    return this.call("notes", {
      action: "reanchor",
      topic,
      note_id: noteId,
      anchor: anchorIndex,
      mode,
      page,
      quote,
      vault,
    });
  },

  notesDetach(
    topic: string,
    noteId: string,
    anchorIndex: number,
    mode: "dry-run" | "apply" = "dry-run",
    vault = "",
  ): Promise<NoteDecisionEnvelope | NoteAnchorActionResult> {
    return this.call("notes", {
      action: "detach",
      topic,
      note_id: noteId,
      anchor: anchorIndex,
      mode,
      vault,
    });
  },

  notesPromote(
    topic: string,
    noteId: string,
    target: PromoteTarget,
    mode: "dry-run" | "apply" = "dry-run",
    fields: {
      question?: string;
      answer?: string;
      verdict?: "good" | "bad";
    } = {},
    vault = "",
  ): Promise<NoteDecisionEnvelope | NotePromoteActionResult> {
    return this.call("notes", {
      action: "promote",
      topic,
      note_id: noteId,
      target,
      mode,
      question: fields.question ?? "",
      answer: fields.answer ?? "",
      verdict: fields.verdict ?? "good",
      vault,
    });
  },

  notesArchive(
    topic: string,
    noteId: string,
    mode: "dry-run" | "apply" = "dry-run",
    vault = "",
  ): Promise<NoteDecisionEnvelope | NoteArchiveActionResult> {
    return this.call("notes", {
      action: "archive",
      topic,
      note_id: noteId,
      mode,
      vault,
    });
  },
};
