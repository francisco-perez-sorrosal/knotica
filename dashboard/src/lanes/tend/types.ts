/**
 * Tend-lane wire shapes: per-vault mechanical maintenance.
 *
 * doctor -> lint -> okf -> migrate -> drift, plus the vault metadata tree.
 * The whole personal-notes family lives here because Tend's drift stage is the
 * only lane surface that consumes it; `note_capture` is dispatched from
 * Answer's `react` stage, but its result shape references `NoteAnchor` /
 * `NoteIntent` and splitting the family would cost more cohesion than the
 * lane boundary buys. Re-exported verbatim from `src/types.ts`, so
 * `import type { X } from "../../types"` still resolves.
 */

import type { GapOrigin } from "../../types";

export interface DoctorCheck {
  name: string;
  status: "PASS" | "WARN" | "FAIL" | string;
  message: string;
  remediation: string | null;
}

export interface DoctorFixGuidance {
  kind: string;
  summary: string;
  commands: string[];
  note: string;
}

export interface DoctorReport {
  schema_version: number;
  vault: string | null;
  quick: boolean;
  ok: boolean;
  exit_code: number;
  checks: DoctorCheck[];
  summary: { pass: number; warn: number; fail: number };
  /** Present when doctor_run was called with fix=true (CLI ``--fix``). */
  fix_guidance?: DoctorFixGuidance | null;
}

export interface DirtyEntry {
  path: string;
  code: string;
  tracked: boolean;
  untracked: boolean;
}

export interface DoctorRepairResult {
  mode: "dry-run" | "apply" | string;
  dirty_count?: number;
  entries?: DirtyEntry[];
  tracked_paths?: string[];
  untracked_paths?: string[];
  restored?: string[];
  message?: string;
}

export interface LintViolation {
  check: string;
  path: string;
  line: number | null;
  message: string;
  fix: string;
}

export interface VaultLintResult {
  topic: string;
  violations: LintViolation[];
}

export interface OkfCheckResult {
  status: string;
  failed: boolean;
  bundle_root: string;
  concept_files_checked: number;
  reserved_files_checked: number;
  errors: Array<{
    path: string;
    code: string;
    message: string;
    severity: string;
  }>;
  notes: string[];
  strict_failures: string[];
}

export interface OkfRepairResult {
  status: string;
  dry_run: boolean;
  mode: string;
  files_changed: string[];
  notes: string[];
  report_path: string | null;
  commit_sha: string | null;
}
export type MetadataNodeKind = "file" | "dir";

export interface MetadataTreeNode {
  name: string;
  path: string;
  kind: MetadataNodeKind;
  exists: boolean;
  size?: number;
  mtime?: string;
  scope?: "topic";
  children?: MetadataTreeNode[];
}

export interface VaultMetadataTree {
  schema_version: number;
  topic: string | null;
  children: MetadataTreeNode[];
}
// ---------------------------------------------------------------------------
// Personal notes (marginalia) — the read-only `notes` dispatcher.
// ---------------------------------------------------------------------------

/** Why the note was written. Filterable on the list action. */
export type NoteIntent = "reflection" | "dispute" | "gap" | "question";
export type NoteIntentFilter = NoteIntent | "all";

/**
 * How precisely an anchor located its target. The resolver ladder produces only
 * these three — there is no `block`/`section` rung, so none is declared.
 */
export type AnchorFidelity = "span" | "page" | "topic";

/**
 * A note's resolved-anchor bucket — the filterable, countable set. `unanchored`
 * is not drift: the anchor never pointed at a page. `shifted` is not drift: the
 * anchor healed itself at a new offset with the verbatim text intact. `fuzzy`
 * and `orphaned` are drift: `fuzzy` found only a paraphrase, `orphaned` found
 * nothing at all.
 */
export type AnchorStatus =
  | "exact"
  | "unanchored"
  | "shifted"
  | "fuzzy"
  | "orphaned";
export type AnchorStatusFilter = AnchorStatus | "all";

/**
 * A single anchor's projection status. Adds `anchor-invalid` — a record that
 * never located anything at all (unreadable claimed page, ambiguous quote).
 * It is a data-integrity problem, not drift, so it is excluded from the
 * note-level bucket entirely rather than folded into `orphaned`.
 */
export type AnchorProjectionStatus = AnchorStatus | "anchor-invalid";

/** One anchor as recorded, plus how it resolves against the vault right now. */
export interface NoteAnchor {
  index: number;
  /** Vault-relative page path; "" for a topic-fidelity anchor. */
  page: string;
  heading: string;
  /** What the anchor bullet recorded. */
  fidelity: string;
  status: AnchorProjectionStatus;
  /** What it resolves to today; null exactly when status is `anchor-invalid`. */
  resolved_fidelity: AnchorFidelity | null;
  /** The passage originally pinned. */
  quote: string;
  /** Commit sha the pin was taken against. */
  pinned_at: string;
}

export interface NoteRecord {
  note_id: string;
  /** Vault-relative path to the note file, for opening it by hand. */
  path: string;
  intent: NoteIntent;
  created: string;
  updated: string;
  /** The note's own lifecycle field from frontmatter (defaults to "active"). */
  note_status: string;
  /** Resolved-anchor bucket; null for a note with no anchors at all. */
  status: AnchorStatus | null;
  tags: string[];
  /** The note's text. */
  note: string;
  anchors: NoteAnchor[];
  /** Anchor bullets the grammar could not parse -- data, not corruption. */
  skipped_anchor_count: number;
}

/** `notes action=read` — one note in full, with its owning topic echoed back. */
export interface NoteReadResult extends NoteRecord {
  topic: string;
}

/** `notes action=list` — one filtered, sorted, paginated page of notes. */
export interface NotesListResult {
  topic: string;
  intent_filter: NoteIntentFilter;
  status_filter: AnchorStatusFilter;
  notes: NoteRecord[];
  intent_counts: Record<NoteIntent, number>;
  /** Anchorless notes are in no bucket, so these can sum to less than total_count. */
  status_counts: Record<AnchorStatus, number>;
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  skipped_malformed: number;
}

/** ``wiki_status``'s per-topic notes summary; absent on servers that predate it. */
export interface NotesStatusSummary {
  total: number;
  drifted: number;
}

/**
 * ``note_capture``'s multi-page-match ambiguity -- one ``{page, heading}``
 * mapping per claimed page the quote matched, in claimed order. Unlike
 * ``notes action=drift``'s own ``NoteDriftAlternative``, this carries no
 * ``overlap`` -- nothing was scored here.
 */
export interface NoteCaptureAlternative {
  page: string;
  heading: string;
}

/** ``note_capture``'s result -- the flat Tier-1 tool Answer's ``react`` stage calls for "Note it". */
export interface NoteCaptureResult {
  topic: string;
  note_id: string;
  path: string;
  intent: NoteIntent;
  anchors: NoteAnchor[];
  alternatives: NoteCaptureAlternative[];
  placement: string;
  written: boolean;
  duplicate: boolean;
  commit: string;
  warnings?: string[];
}

// ---------------------------------------------------------------------------
// Personal notes -- the drift review queue and the four mutating actions
// (``reanchor``/``detach``/``promote``/``archive``). The read-only shapes
// above (``NoteAnchor``, ``NoteRecord``, ``NotesListResult``) are shared.
// ---------------------------------------------------------------------------

/**
 * ``notes action=drift``'s candidate placement -- unlike ``note_capture``'s
 * own ``alternatives`` (a different, unrelated shape: ``{page, heading}``,
 * no ``overlap``, because nothing was scored there), this one carries an
 * ``overlap`` **when one was measured**. It is `null` for a structural
 * guess: the enclosing heading survived, so the section is a real placement,
 * but no passage-level similarity was computed. Render the null case as
 * prose, never as `0%` and never as a percentage.
 */
export interface NoteDriftAlternative {
  page: string;
  heading: string;
  overlap: number | null;
}

/**
 * ``notes action=drift``'s per-item detail. ``overlap`` is `null` whenever
 * the resolver had no measurement to report -- ``anchor-invalid`` (no
 * candidate search ever ran), a deleted-page orphan (no page left to
 * search), and the case that matters most: a **surviving heading whose
 * passage shares no vocabulary with the page at all**, where the ladder
 * supplies `guess_threshold - CLAMP_EPSILON` internally to satisfy its own
 * nullability invariant. That value is a *ceiling*, so surfacing it as a
 * survival percentage showed a deleted passage as the most confident item in
 * the queue. Distinguish "0% survived" from "nothing was comparable".
 * ``rewritten_at``/``rewritten_by`` are always strings, `""` (never omitted,
 * never null) when there is no rewrite to attribute -- every
 * ``anchor-invalid`` item is in that shape, since nothing about the page
 * caused its corruption.
 */
export interface NoteDrift {
  anchor_index: number;
  /**
   * Why this anchor drifted, so the review surface can say the true thing.
   *
   * `superseded` means the anchored page was **replaced wholesale**, not
   * edited: page similarity collapsed and no heading survived. That is a
   * different event from a reword and wants a different affordance -- the
   * ladder's best guess points into content unrelated to the anchored passage,
   * so the server sends `alternatives: []` and the UI must not invite a
   * re-anchor onto an arbitrary span. Phase 3 measured one such event supplying
   * 85% of all observed orphaning, indistinguishable here from a reword.
   *
   * Optional for forward/backward compatibility: a dashboard talking to a
   * server that predates the classifier sees it absent and falls back to
   * `rewritten`, which is the pre-existing behaviour.
   */
  cause?: "rewritten" | "superseded";
  /** Always populated, orphans included -- the historical text is never withheld. */
  pinned_quote: string;
  /** The current text at the resolved placement; "" when nothing is confidently placed. */
  live_quote: string;
  overlap: number | null;
  alternatives: NoteDriftAlternative[];
  rewritten_at: string;
  rewritten_by: string;
}

/** One review-queue member: the note it belongs to, plus that anchor's drift detail. */
export interface NoteDriftItem {
  note: NoteRecord;
  drift: NoteDrift;
}

/**
 * ``notes action=drift`` -- the review queue: every anchor resolving
 * ``fuzzy``, ``orphaned``, or ``anchor-invalid``. ``total_count`` is
 * ``items.length``, the whole queue including ``anchor-invalid``, so
 * pagination stays one contract with ``items``; ``invalid_count`` is a
 * breakdown of how many of those are ``anchor-invalid``, not a disjoint
 * bucket. Deliberately unlike ``NotesStatusSummary.drifted`` (``fuzzy +
 * orphaned`` only, from ``wiki_status``) -- the queue header and that badge
 * disagree by design, not by bug.
 */
export interface NotesDriftResult {
  topic: string;
  items: NoteDriftItem[];
  next_cursor: string;
  has_more: boolean;
  total_count: number;
  invalid_count: number;
}

export type NoteAction = "reanchor" | "detach" | "promote" | "archive";

/**
 * The uniform ``mode=dry-run`` preview every ``notes`` mutating action
 * returns -- the same decision-envelope shape ``suggestions_review`` renders.
 */
export interface NoteDecisionEnvelope {
  mode: "dry-run";
  topic: string;
  note_id: string;
  action: NoteAction;
  decision_id: string;
  summary: string;
  context: Record<string, unknown>;
  options: Array<{ action: string; preview: string; reversible: boolean }>;
  provenance: Record<string, unknown>;
  reason_required: boolean;
}

/** ``notes action=reanchor|detach``'s ``mode=apply`` result -- both append one anchor record. */
export interface NoteAnchorActionResult {
  mode: "apply";
  topic: string;
  action: "reanchor" | "detach";
  committed: boolean;
  note_id: string;
  path: string;
  anchor_index: number;
  /** Opaque, forward-compatible -- "reanchored" or "detached" today. */
  kind: string;
  commit: string;
}

/** ``notes action=archive``'s ``mode=apply`` result -- frontmatter-only, touches no anchor. */
export interface NoteArchiveActionResult {
  mode: "apply";
  topic: string;
  action: "archive";
  committed: boolean;
  note_id: string;
  path: string;
  status: string;
  /** False when this call changed nothing -- see ``duplicate``. */
  written: boolean;
  /** True when the note was already archived; mirrors ``capture``'s own vocabulary. */
  duplicate: boolean;
  commit: string;
}

/** ``promote target=trainset``'s ``mode=apply`` result -- delegates to ``curate_example``. */
export interface NotePromoteTrainsetResult {
  mode: "apply";
  topic: string;
  action: "promote";
  committed: boolean;
  path: string;
  example_count: number;
  appended: boolean;
}

/** ``notes action=promote target=gap``'s ``mode=apply`` result -- delegates to ``report_gap``. */
export interface NotePromoteGapResult {
  mode: "apply";
  topic: string;
  action: "promote";
  committed: boolean;
  gap_id: string;
  qa_id: string;
  question: string;
  fault_class: string;
  status: string;
  origin: GapOrigin;
  reference_pages: string[];
  written: boolean;
}

/** The two ``target``s the dashboard offers; ``golden`` always rejects tool-side (dec-059). */
export type PromoteTarget = "trainset" | "gap";

export type NotePromoteActionResult =
  | NotePromoteTrainsetResult
  | NotePromoteGapResult;
