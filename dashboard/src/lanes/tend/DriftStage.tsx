import { useState } from "preact/hooks";
import type { JSX } from "preact";

import { Spinner } from "../../icons";
import { NotePromoteDialog } from "../../NotePromoteDialog";
import {
  ActionConfirm,
  ANCHOR_TREATMENT,
  INTENT_TREATMENT,
  pageLabel,
} from "../../notePresentation";
import type { ToolClient } from "../../toolClient";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import type { ProcessId } from "../processMeta";
import type {
  NoteAnchor,
  NoteDecisionEnvelope,
  NoteDrift,
  NoteDriftAlternative,
  NoteDriftItem,
  NoteRecord,
  NotesDriftResult,
  NotesListResult,
} from "../../types";

/**
 * Tend's fifth checklist stage -- merges the
 * former notes browser and drift review queue into one collapsed-by-default
 * surface. Collapsed is the honest state: nothing is fetched, and nothing is
 * claimed clean or broken, until the operator pays the one-git-read-per-anchor
 * cost explicitly via `[Check]`.
 *
 * Both predecessors are now deleted; their shared building blocks
 * (`ActionConfirm`, the treatment tables, `pageLabel`) survive in
 * `notePresentation.tsx` and are reused verbatim here. Their private per-item
 * card renderers did not survive -- the merge's shape (one list, not two tabs)
 * does not carry them over unchanged.
 *
 * Decoration is driven by presence in the `notesDrift` result, not by a
 * client-side copy of the server's queue-membership rule: `notesDrift`
 * already returns only fuzzy/orphaned/anchor-invalid anchors, so matching
 * `notesList`'s anchors against that result by `(note_id, anchor_index)` is
 * the filter, sourced from one place instead of two.
 *
 * The checklist row this stage backs is **always** `pending` -- unlike
 * doctor/lint/okf, drift never resolves to a verdict on its own; the honest
 * "not checked" state is the point, so `TendLane` never
 * asks this component to report one.
 */

const MERGE_PAGE_SIZE = 100;

function itemKey(noteId: string, anchorIndex: number): string {
  return `${noteId}:${anchorIndex}`;
}

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

async function loadNotesAndDrift(
  client: ToolClient,
  topic: string,
  vault: string,
): Promise<{ list: NotesListResult; drift: NotesDriftResult }> {
  const [list, drift] = await Promise.all([
    client.notesList(topic, "all", "all", "", MERGE_PAGE_SIZE, vault),
    client.notesDrift(topic, "", MERGE_PAGE_SIZE, vault),
  ]);
  return { list, drift };
}

type PendingAction = {
  key: string;
  kind: "reanchor" | "detach" | "archive";
  noteId: string;
  anchorIndex: number | null;
  page?: string;
  quote?: string;
  envelope: NoteDecisionEnvelope;
};

const PROCESS_BY_KIND: Record<PendingAction["kind"], ProcessId> = {
  reanchor: "tend.note_reanchor",
  detach: "tend.note_detach",
  archive: "tend.note_archive",
};

/**
 * What the last applied mutation was, so the stage can say what it did.
 *
 * It is held **here, in the stage** rather than in the card or the dialog
 * that ran it: promotion's dialog unmounts on success and a drift card's row
 * is re-rendered from a fresh scan, so an outcome parked in either would
 * vanish at exactly the moment it needed to be read. It is superseded by the
 * next action, never cleared on a timer -- a state that quietly disappears is
 * the failure this contract exists to fix.
 */
type AppliedOutcome = {
  process: ProcessId;
  discriminant?: string | null;
};

export function DriftStage({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
}): JSX.Element {
  const [checked, setChecked] = useState(false);
  const [list, setList] = useState<NotesListResult | null>(null);
  const [drift, setDrift] = useState<NotesDriftResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [promoteNoteId, setPromoteNoteId] = useState<string | null>(null);
  const [applied, setApplied] = useState<AppliedOutcome | null>(null);

  // Post-mutation refetch. `checked` is already true by the time this runs, so
  // it only refreshes the data -- it never touches the collapse state.
  async function refresh() {
    if (!client) return;
    setError(null);
    try {
      const { list: nextList, drift: nextDrift } = await loadNotesAndDrift(
        client,
        topic,
        vault,
      );
      setList(nextList);
      setDrift(nextDrift);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  // The one and only transition out of the collapsed "not checked" state --
  // it flips to the expanded view only once the data has actually arrived,
  // never eagerly, so a caller polling the DOM for "not checked" to
  // disappear is polling for a real, loaded result, not an empty shell.
  async function runCheck() {
    if (!client || loading || checked) return;
    setLoading(true);
    setError(null);
    try {
      const { list: nextList, drift: nextDrift } = await loadNotesAndDrift(
        client,
        topic,
        vault,
      );
      setList(nextList);
      setDrift(nextDrift);
      setChecked(true);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  }

  async function previewReanchor(
    note: NoteRecord,
    anchorIndex: number,
    page: string,
    quote: string,
  ) {
    if (!client || pending || busy) return;
    setBusy(true);
    setError(null);
    setApplied(null);
    try {
      const envelope = await client.notesReanchor(
        topic,
        note.note_id,
        anchorIndex,
        "dry-run",
        page,
        quote,
        vault,
      );
      if (envelope.mode !== "dry-run") return;
      setPending({
        key: itemKey(note.note_id, anchorIndex),
        kind: "reanchor",
        noteId: note.note_id,
        anchorIndex,
        page,
        quote,
        envelope,
      });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function previewDetach(note: NoteRecord, anchorIndex: number) {
    if (!client || pending || busy) return;
    setBusy(true);
    setError(null);
    setApplied(null);
    try {
      const envelope = await client.notesDetach(
        topic,
        note.note_id,
        anchorIndex,
        "dry-run",
        vault,
      );
      if (envelope.mode !== "dry-run") return;
      setPending({
        key: itemKey(note.note_id, anchorIndex),
        kind: "detach",
        noteId: note.note_id,
        anchorIndex,
        envelope,
      });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function previewArchive(note: NoteRecord) {
    if (!client || pending || busy) return;
    setBusy(true);
    setError(null);
    setApplied(null);
    try {
      const envelope = await client.notesArchive(
        topic,
        note.note_id,
        "dry-run",
        vault,
      );
      if (envelope.mode !== "dry-run") return;
      setPending({
        key: `archive:${note.note_id}`,
        kind: "archive",
        noteId: note.note_id,
        anchorIndex: null,
        envelope,
      });
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function applyPending() {
    if (!client || !pending) return;
    setBusy(true);
    setError(null);
    try {
      if (pending.kind === "reanchor") {
        await client.notesReanchor(
          topic,
          pending.noteId,
          pending.anchorIndex ?? 0,
          "apply",
          pending.page ?? "",
          pending.quote ?? "",
          vault,
        );
      } else if (pending.kind === "detach") {
        await client.notesDetach(
          topic,
          pending.noteId,
          pending.anchorIndex ?? 0,
          "apply",
          vault,
        );
      } else {
        await client.notesArchive(topic, pending.noteId, "apply", vault);
      }
      setApplied({ process: PROCESS_BY_KIND[pending.kind] });
      setPending(null);
      await refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!checked) {
    return (
      <div class="notes-drift-collapsed">
        <p class="muted">not checked — one git read per anchor</p>
        {error ? (
          <p class="notes-error" role="alert">
            {error}
          </p>
        ) : null}
        <button
          type="button"
          disabled={!client || loading}
          aria-busy={loading || undefined}
          onClick={() => void runCheck()}
        >
          {loading ? (
            <>
              <Spinner />
              Check
            </>
          ) : (
            "Check"
          )}
        </button>
      </div>
    );
  }

  const driftByAnchor = new Map<string, NoteDriftItem>();
  for (const driftItem of drift?.items ?? []) {
    driftByAnchor.set(
      itemKey(driftItem.note.note_id, driftItem.drift.anchor_index),
      driftItem,
    );
  }
  const notes = list?.notes ?? [];
  const promoteNote = promoteNoteId
    ? (notes.find((note) => note.note_id === promoteNoteId) ?? null)
    : null;
  const disabled = busy || pending !== null;

  return (
    <div class="notes-drift-merged">
      {error ? (
        <p class="notes-error" role="alert">
          {error}
        </p>
      ) : null}

      {applied ? (
        <ProcessOutcome
          process={applied.process}
          discriminant={applied.discriminant}
        />
      ) : null}

      {notes.length === 0 ? (
        <p class="muted">No notes on this topic.</p>
      ) : (
        <ul class="notes-drift-merged-list">
          {notes.map((note) => (
            <NoteRow
              key={note.note_id}
              note={note}
              driftByAnchor={driftByAnchor}
              pending={pending}
              disabled={disabled}
              busy={busy}
              onReanchor={(anchorIndex, page, quote) =>
                void previewReanchor(note, anchorIndex, page, quote)
              }
              onDetach={(anchorIndex) => void previewDetach(note, anchorIndex)}
              onArchive={() => void previewArchive(note)}
              onPromote={() => setPromoteNoteId(note.note_id)}
              onConfirm={() => void applyPending()}
              onCancel={() => setPending(null)}
            />
          ))}
        </ul>
      )}

      {promoteNote ? (
        <NotePromoteDialog
          client={client}
          topic={topic}
          vault={vault}
          note={promoteNote}
          onClose={() => setPromoteNoteId(null)}
          onPromoted={(target) => {
            // The dialog unmounts on success, so the outcome is recorded here
            // and the destination it picked becomes the Next's discriminant.
            setApplied({ process: "tend.note_promote", discriminant: target });
            setPromoteNoteId(null);
            void refresh();
          }}
        />
      ) : null}
    </div>
  );
}

function splitAnchors(
  note: NoteRecord,
  driftByAnchor: Map<string, NoteDriftItem>,
): {
  decorated: Array<{ anchor: NoteAnchor; item: NoteDriftItem }>;
  plain: NoteAnchor[];
} {
  const decorated: Array<{ anchor: NoteAnchor; item: NoteDriftItem }> = [];
  const plain: NoteAnchor[] = [];
  for (const anchor of note.anchors) {
    const item = driftByAnchor.get(itemKey(note.note_id, anchor.index));
    if (item) decorated.push({ anchor, item });
    else plain.push(anchor);
  }
  return { decorated, plain };
}

function NoteRow({
  note,
  driftByAnchor,
  pending,
  disabled,
  busy,
  onReanchor,
  onDetach,
  onArchive,
  onPromote,
  onConfirm,
  onCancel,
}: {
  note: NoteRecord;
  driftByAnchor: Map<string, NoteDriftItem>;
  pending: PendingAction | null;
  disabled: boolean;
  busy: boolean;
  onReanchor: (anchorIndex: number, page: string, quote: string) => void;
  onDetach: (anchorIndex: number) => void;
  onArchive: () => void;
  onPromote: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}): JSX.Element {
  const intent = INTENT_TREATMENT[note.intent];
  const { decorated, plain } = splitAnchors(note, driftByAnchor);
  const archivePending =
    pending?.key === `archive:${note.note_id}` ? pending : null;

  return (
    <li class="notes-drift-note-row">
      <div class="notes-card-head">
        <span class={`status-chip ${intent?.tone ?? ""}`}>
          <span aria-hidden="true">{intent?.glyph ?? "·"}</span> {note.intent}
        </span>
      </div>

      <p class="notes-body">{note.note}</p>

      {plain.length > 0 ? (
        <ul class="notes-anchors">
          {plain.map((anchor) => (
            <PlainAnchorRow key={anchor.index} anchor={anchor} />
          ))}
        </ul>
      ) : null}

      {decorated.map(({ anchor, item }) => {
        const key = itemKey(note.note_id, anchor.index);
        return (
          <DriftAnchorCard
            key={key}
            note={note}
            anchor={anchor}
            drift={item.drift}
            pending={pending?.key === key ? pending : null}
            disabled={disabled}
            busy={busy}
            onReanchor={(page, quote) => onReanchor(anchor.index, page, quote)}
            onDetach={() => onDetach(anchor.index)}
            onConfirm={onConfirm}
            onCancel={onCancel}
          />
        );
      })}

      {archivePending ? (
        <ActionConfirm
          envelope={archivePending.envelope}
          busy={busy}
          applyLabel="Confirm archive"
          onConfirm={onConfirm}
          onCancel={onCancel}
        />
      ) : (
        <div class="notes-card-actions">
          <button type="button" disabled={disabled} onClick={onPromote}>
            Promote…
          </button>
          <button type="button" disabled={disabled} onClick={onArchive}>
            Archive
          </button>
          <ProcessBrief process="tend.note_archive" align="end" />
        </div>
      )}
    </li>
  );
}

function PlainAnchorRow({ anchor }: { anchor: NoteAnchor }): JSX.Element {
  const treatment = ANCHOR_TREATMENT[anchor.status];
  return (
    <li class="notes-anchor">
      <span
        class={`health-chip notes-anchor-status ${treatment.tone}`}
        title={treatment.meaning}
      >
        <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
      </span>
      {anchor.page ? (
        <span class="muted"> — {pageLabel(anchor.page)}</span>
      ) : null}
    </li>
  );
}

function DriftAnchorCard({
  note,
  anchor,
  drift,
  pending,
  disabled,
  busy,
  onReanchor,
  onDetach,
  onConfirm,
  onCancel,
}: {
  note: NoteRecord;
  anchor: NoteAnchor;
  drift: NoteDrift;
  pending: PendingAction | null;
  disabled: boolean;
  busy: boolean;
  onReanchor: (page: string, quote: string) => void;
  onDetach: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}): JSX.Element {
  const [selectedAlt, setSelectedAlt] = useState(0);
  const treatment = ANCHOR_TREATMENT[anchor.status];
  const isInvalid = anchor.status === "anchor-invalid";
  const hasResolvedMatch = drift.live_quote !== "";
  const isSuperseded = drift.cause === "superseded";
  const alternative = drift.alternatives[selectedAlt];

  return (
    <div class="notes-drift-card">
      <div class="notes-drift-card-head">
        <span
          class={`health-chip notes-anchor-status ${treatment.tone}`}
          title={treatment.meaning}
        >
          <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
        </span>
        {anchor.page ? (
          <span class="muted">{pageLabel(anchor.page)}</span>
        ) : null}
      </div>

      <p class="notes-body">{note.note}</p>

      <DriftDiff
        drift={drift}
        treatment={treatment}
        isInvalid={isInvalid}
        hasResolvedMatch={hasResolvedMatch}
        isSuperseded={isSuperseded}
      />

      {!isInvalid && !hasResolvedMatch && drift.alternatives.length > 0 ? (
        <AlternativesGroup
          alternatives={drift.alternatives}
          selected={selectedAlt}
          onSelect={setSelectedAlt}
        />
      ) : null}

      {pending ? (
        <ActionConfirm
          envelope={pending.envelope}
          busy={busy}
          applyLabel={
            pending.kind === "reanchor" ? "Confirm re-anchor" : "Confirm detach"
          }
          onConfirm={onConfirm}
          onCancel={onCancel}
        />
      ) : (
        <div class="notes-drift-actions">
          {!isInvalid && hasResolvedMatch ? (
            <button
              type="button"
              disabled={disabled}
              onClick={() => onReanchor("", "")}
            >
              Re-anchor here
            </button>
          ) : null}
          {!isInvalid && !hasResolvedMatch && alternative ? (
            <button
              type="button"
              disabled={disabled}
              onClick={() => onReanchor(alternative.page, drift.pinned_quote)}
            >
              Re-anchor to selected
            </button>
          ) : null}
          <button type="button" disabled={disabled} onClick={onDetach}>
            Detach
          </button>
          {/* Two processes share this row, so each brief names its own. */}
          <ProcessBrief
            process="tend.note_reanchor"
            term="why re-anchor"
            align="end"
          />
          <ProcessBrief
            process="tend.note_detach"
            term="why detach"
            align="end"
          />
        </div>
      )}
    </div>
  );
}

function DriftDiff({
  drift,
  treatment,
  isInvalid,
  hasResolvedMatch,
  isSuperseded,
}: {
  drift: NoteDrift;
  treatment: { tone: string; meaning: string };
  isInvalid: boolean;
  hasResolvedMatch: boolean;
  isSuperseded: boolean;
}): JSX.Element {
  return (
    <div class="notes-drift-diff">
      <p class="stat-label">what you pinned</p>
      <blockquote class="notes-anchor-quote">{drift.pinned_quote}</blockquote>

      {isInvalid ? (
        <p class="notes-anchor-meaning bad">
          <span aria-hidden="true">⚠ </span>
          {treatment.meaning}
        </p>
      ) : (
        <>
          <p class="stat-label">what is there now</p>
          {hasResolvedMatch ? (
            <blockquote class="notes-anchor-quote">
              {drift.live_quote}
            </blockquote>
          ) : isSuperseded ? (
            <p class="notes-anchor-meaning bad">
              <span aria-hidden="true">⚠ </span>
              this page was replaced, not reworded — its content and every
              heading changed. Your passage did not move somewhere else on it;
              there is nowhere on this page it could have gone.
            </p>
          ) : (
            <p class="muted">{treatment.meaning}</p>
          )}
        </>
      )}
    </div>
  );
}

function AlternativesGroup({
  alternatives,
  selected,
  onSelect,
}: {
  alternatives: NoteDriftAlternative[];
  selected: number;
  onSelect: (index: number) => void;
}): JSX.Element {
  return (
    <div
      class="notes-drift-alternatives"
      role="radiogroup"
      aria-label="Closest surviving passage"
    >
      <p class="stat-label">closest surviving passage (best guess)</p>
      {alternatives.map((alt, index) => (
        <label
          key={`${alt.page}:${alt.heading}:${index}`}
          class="notes-drift-alt"
        >
          <input
            type="radio"
            name="drift-alternative"
            checked={selected === index}
            onChange={() => onSelect(index)}
          />
          {pageLabel(alt.page)}
          {alt.heading ? ` › ${alt.heading}` : ""} —{" "}
          {alt.overlap === null
            ? "the section survived (no measurable overlap)"
            : `${Math.round(alt.overlap * 100)}% overlap`}
        </label>
      ))}
    </div>
  );
}
