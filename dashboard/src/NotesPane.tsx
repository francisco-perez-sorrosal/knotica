import { useEffect, useState } from "preact/hooks";

import { NotePromoteDialog } from "./NotePromoteDialog";
import {
  ActionConfirm,
  ANCHOR_TREATMENT,
  INTENT_TREATMENT,
  NotesDriftView,
  pageLabel,
} from "./NotesDriftView";
import type { ToolClient } from "./toolClient";
import type {
  AnchorProjectionStatus,
  AnchorStatusFilter,
  NoteAnchor,
  NoteDecisionEnvelope,
  NoteIntentFilter,
  NoteRecord,
  NotesListResult,
} from "./types";

const PAGE_SIZE = 20;

const INTENT_FILTERS: Array<{ value: NoteIntentFilter; label: string }> = [
  { value: "all", label: "all" },
  { value: "reflection", label: "reflection" },
  { value: "dispute", label: "dispute" },
  { value: "gap", label: "gap" },
  { value: "question", label: "question" },
];

const ANCHOR_FILTERS: Array<{ value: AnchorStatusFilter; label: string }> = [
  { value: "all", label: "all" },
  { value: "exact", label: "exact" },
  { value: "unanchored", label: "unanchored" },
  { value: "shifted", label: "shifted" },
  { value: "fuzzy", label: "fuzzy" },
  { value: "orphaned", label: "orphaned" },
];

/** ``notes action=drift``'s queue membership -- mirrors the server's own
 * `_QUEUE_MEMBER_STATUSES` (fuzzy ∪ orphaned ∪ anchor-invalid). Used here to
 * decide whether an anchor row should point at the drift queue instead of
 * offering a blind mutation with no comparison context. */
const QUEUE_ELIGIBLE_STATUSES = new Set<AnchorProjectionStatus>([
  "fuzzy",
  "orphaned",
  "anchor-invalid",
]);

const ARCHIVED_STATUS = "archived";

type PendingCardAction = {
  key: string;
  kind: "archive" | "detach" | "reanchor";
  noteId: string;
  anchorIndex: number | null;
  envelope: NoteDecisionEnvelope;
};

function cardActionKey(
  kind: PendingCardAction["kind"],
  noteId: string,
  anchorIndex: number | null,
): string {
  return `${kind}:${noteId}:${anchorIndex ?? "note"}`;
}

export function NotesPane({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
}) {
  const [view, setView] = useState<"browse" | "drift">("browse");
  const [intent, setIntent] = useState<NoteIntentFilter>("all");
  const [anchorStatus, setAnchorStatus] = useState<AnchorStatusFilter>("all");
  const [result, setResult] = useState<NotesListResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recheckingId, setRecheckingId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingCardAction | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [promoteNoteId, setPromoteNoteId] = useState<string | null>(null);

  async function load(cursor = "", append = false) {
    if (!client || !topic) return;
    setLoading(!append);
    setError(null);
    try {
      const next = await client.notesList(topic, intent, anchorStatus, cursor, PAGE_SIZE, vault);
      setResult((prev) =>
        append && prev ? { ...next, notes: [...prev.notes, ...next.notes] } : next,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault, intent, anchorStatus]);

  /** Re-resolve one note's anchors against the vault as it stands now. */
  async function recheck(noteId: string) {
    if (!client || recheckingId) return;
    setRecheckingId(noteId);
    setError(null);
    try {
      const fresh = await client.notesRead(topic, noteId, vault);
      setResult((prev) =>
        prev
          ? {
              ...prev,
              notes: prev.notes.map((note) => (note.note_id === noteId ? fresh : note)),
            }
          : prev,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecheckingId(null);
    }
  }

  async function previewArchive(note: NoteRecord) {
    if (!client || actionBusy) return;
    const key = cardActionKey("archive", note.note_id, null);
    setActionBusy(key);
    setError(null);
    try {
      const envelope = await client.notesArchive(topic, note.note_id, "dry-run", vault);
      if (envelope.mode !== "dry-run") return;
      setPending({ key, kind: "archive", noteId: note.note_id, anchorIndex: null, envelope });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(null);
    }
  }

  async function previewDetach(note: NoteRecord, anchorIndex: number) {
    if (!client || actionBusy) return;
    const key = cardActionKey("detach", note.note_id, anchorIndex);
    setActionBusy(key);
    setError(null);
    try {
      const envelope = await client.notesDetach(topic, note.note_id, anchorIndex, "dry-run", vault);
      if (envelope.mode !== "dry-run") return;
      setPending({ key, kind: "detach", noteId: note.note_id, anchorIndex, envelope });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(null);
    }
  }

  async function previewReanchorHere(note: NoteRecord, anchorIndex: number) {
    if (!client || actionBusy) return;
    const key = cardActionKey("reanchor", note.note_id, anchorIndex);
    setActionBusy(key);
    setError(null);
    try {
      const envelope = await client.notesReanchor(
        topic,
        note.note_id,
        anchorIndex,
        "dry-run",
        "",
        "",
        vault,
      );
      if (envelope.mode !== "dry-run") return;
      setPending({ key, kind: "reanchor", noteId: note.note_id, anchorIndex, envelope });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(null);
    }
  }

  async function applyPending() {
    if (!client || !pending) return;
    setActionBusy(pending.key);
    setError(null);
    try {
      if (pending.kind === "archive") {
        await client.notesArchive(topic, pending.noteId, "apply", vault);
      } else if (pending.kind === "detach") {
        await client.notesDetach(topic, pending.noteId, pending.anchorIndex ?? 0, "apply", vault);
      } else {
        await client.notesReanchor(
          topic,
          pending.noteId,
          pending.anchorIndex ?? 0,
          "apply",
          "",
          "",
          vault,
        );
      }
      setPending(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setActionBusy(null);
    }
  }

  if (view === "drift") {
    return (
      <NotesDriftView
        client={client}
        topic={topic}
        vault={vault}
        onBack={() => {
          setView("browse");
          void load();
        }}
      />
    );
  }

  const notes = result?.notes ?? [];
  const intentCounts = result?.intent_counts;
  const statusCounts = result?.status_counts;
  const topicTotal = intentCounts
    ? Object.values(intentCounts).reduce((sum, count) => sum + count, 0)
    : 0;
  // Drifted counts `fuzzy` + `orphaned`: both mean the KB moved under a note.
  // `shifted` self-healed (the passage survives word for word) and `unanchored`
  // never pointed at a page, so neither is drift. Must stay byte-identical in
  // meaning to core/status.py's _DRIFTED_ANCHOR_STATUSES -- this header and the
  // wiki_status-fed tab badge render the same number from different sources, so
  // a divergence here is a visible on-screen contradiction, not a rounding
  // difference. `anchor-invalid` is deliberately absent: it is corruption, not
  // drift, and belongs on the review surface rendered distinctly.
  const driftedTotal = statusCounts ? statusCounts.fuzzy + statusCounts.orphaned : 0;
  const promoteNote = promoteNoteId ? notes.find((note) => note.note_id === promoteNoteId) : null;

  return (
    <section class="panel notes-panel" aria-label="Personal notes">
      <header class="notes-header">
        <div>
          <h2>Notes · {topic}</h2>
          <p class="muted">
            Your own marginalia — private, never scored, never part of the wiki corpus. Notes live
            outside the pages, so <code>search</code> and <code>query</code> never see them.
          </p>
        </div>
        <div class="notes-header-counts">
          <span class="health-chip">{topicTotal} notes</span>
          {driftedTotal > 0 ? (
            <span
              class="health-chip warn"
              title="Notes whose anchor pointed at a passage the page no longer has"
            >
              <span aria-hidden="true">⚠</span> {driftedTotal} drifted
            </span>
          ) : null}
          <button type="button" class="ghost" onClick={() => setView("drift")}>
            Review drift →
          </button>
          <button type="button" class="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "…" : "⟳"}
          </button>
        </div>
      </header>

      <div class="notes-filters" role="group" aria-label="Filter by intent">
        <span class="stat-label">intent</span>
        {INTENT_FILTERS.map((entry) => (
          <button
            type="button"
            key={entry.value}
            class={intent === entry.value ? "active" : "ghost"}
            onClick={() => setIntent(entry.value)}
          >
            {entry.label}
            {intentCounts && entry.value !== "all" ? ` ${intentCounts[entry.value]}` : ""}
          </button>
        ))}
      </div>

      <div class="notes-filters" role="group" aria-label="Filter by anchor status">
        <span class="stat-label">anchor</span>
        {ANCHOR_FILTERS.map((entry) => (
          <button
            type="button"
            key={entry.value}
            class={anchorStatus === entry.value ? "active" : "ghost"}
            title={entry.value === "all" ? undefined : ANCHOR_TREATMENT[entry.value].meaning}
            onClick={() => setAnchorStatus(entry.value)}
          >
            {entry.label}
            {statusCounts && entry.value !== "all" ? ` ${statusCounts[entry.value]}` : ""}
          </button>
        ))}
      </div>

      {error ? (
        <p class="notes-error" role="alert">
          {error}
        </p>
      ) : null}

      {result && result.skipped_malformed > 0 ? (
        <p class="muted notes-partial-note">
          {result.skipped_malformed} note{result.skipped_malformed === 1 ? "" : "s"} could not be
          read and {result.skipped_malformed === 1 ? "was" : "were"} skipped — open{" "}
          <code>notes/{topic}/</code> to check {result.skipped_malformed === 1 ? "it" : "them"}.
        </p>
      ) : null}

      {loading && notes.length === 0 ? (
        <p class="muted">Loading notes…</p>
      ) : notes.length === 0 ? (
        <NotesEmpty topic={topic} filtered={intent !== "all" || anchorStatus !== "all"} />
      ) : (
        <ul class="notes-list">
          {notes.map((note) => (
            <NoteCard
              key={note.note_id}
              note={note}
              rechecking={recheckingId === note.note_id}
              anyRechecking={recheckingId !== null}
              onRecheck={() => void recheck(note.note_id)}
              pending={pending && pending.noteId === note.note_id ? pending : null}
              anyActionBusy={actionBusy !== null}
              onArchive={() => void previewArchive(note)}
              onPromote={() => setPromoteNoteId(note.note_id)}
              onDetach={(anchorIndex) => void previewDetach(note, anchorIndex)}
              onReanchor={(anchorIndex) => void previewReanchorHere(note, anchorIndex)}
              onOpenDrift={() => setView("drift")}
              onConfirmPending={() => void applyPending()}
              onCancelPending={() => setPending(null)}
            />
          ))}
        </ul>
      )}

      {result?.has_more ? (
        <button
          type="button"
          class="ghost notes-load-more"
          disabled={loading}
          onClick={() => void load(result.next_cursor, true)}
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      ) : null}

      {promoteNote ? (
        <NotePromoteDialog
          client={client}
          topic={topic}
          vault={vault}
          note={promoteNote}
          onClose={() => setPromoteNoteId(null)}
          onPromoted={() => {
            setPromoteNoteId(null);
            void load();
          }}
        />
      ) : null}
    </section>
  );
}

function NotesEmpty({ topic, filtered }: { topic: string; filtered: boolean }) {
  if (filtered) {
    return (
      <div class="notes-empty">
        <p>No notes match this filter.</p>
        <p class="muted">Widen the intent or anchor filter to see the rest of the topic.</p>
      </div>
    );
  }
  return (
    <div class="notes-empty">
      <p>
        <span class="notes-empty-glyph" aria-hidden="true">
          ✎
        </span>
      </p>
      <p>No notes on this topic yet.</p>
      <p class="muted">
        Notes are your own marginalia — private, never scored, never part of the wiki. Write one
        from a conversation (&ldquo;note this…&rdquo;), with <code>/knotica:note</code>, or by hand
        in <code>notes/{topic}/</code> in Obsidian.
      </p>
    </div>
  );
}

function NoteCard({
  note,
  rechecking,
  anyRechecking,
  onRecheck,
  pending,
  anyActionBusy,
  onArchive,
  onPromote,
  onDetach,
  onReanchor,
  onOpenDrift,
  onConfirmPending,
  onCancelPending,
}: {
  note: NoteRecord;
  rechecking: boolean;
  anyRechecking: boolean;
  onRecheck: () => void;
  pending: PendingCardAction | null;
  anyActionBusy: boolean;
  onArchive: () => void;
  onPromote: () => void;
  onDetach: (anchorIndex: number) => void;
  onReanchor: (anchorIndex: number) => void;
  onOpenDrift: () => void;
  onConfirmPending: () => void;
  onCancelPending: () => void;
}) {
  const intent = INTENT_TREATMENT[note.intent];
  const archived = note.note_status === ARCHIVED_STATUS;
  const cardPending = pending && pending.anchorIndex === null ? pending : null;

  return (
    <li class="notes-card">
      <div class="notes-card-head">
        <span class={`status-chip ${intent?.tone ?? ""}`}>
          <span aria-hidden="true">{intent?.glyph ?? "·"}</span> {note.intent} ·{" "}
          {note.created.slice(0, 10)}
        </span>
        <span class="notes-card-badges">
          {note.tags.map((tag) => (
            <span class="health-chip notes-tag" key={tag}>
              #{tag}
            </span>
          ))}
          <NoteStatusBadge status={note.status} />
        </span>
      </div>

      <p class="notes-body">{note.note}</p>

      {note.anchors.length === 0 ? (
        <p class="muted notes-anchor-none">
          No anchors — this note is filed against the topic and points at no page.
        </p>
      ) : (
        <ul class="notes-anchors">
          {note.anchors.map((anchor) => (
            <AnchorRow
              key={anchor.index}
              anchor={anchor}
              pending={pending && pending.anchorIndex === anchor.index ? pending : null}
              anyActionBusy={anyActionBusy}
              onDetach={() => onDetach(anchor.index)}
              onReanchor={() => onReanchor(anchor.index)}
              onOpenDrift={onOpenDrift}
              onConfirmPending={onConfirmPending}
              onCancelPending={onCancelPending}
            />
          ))}
        </ul>
      )}

      {cardPending ? (
        <ActionConfirm
          envelope={cardPending.envelope}
          busy={anyActionBusy}
          applyLabel="Confirm archive"
          onConfirm={onConfirmPending}
          onCancel={onCancelPending}
        />
      ) : null}

      <div class="notes-card-actions">
        <span class="muted notes-path" title="Vault-relative path — open it in Obsidian yourself">
          <code>{note.path}</code>
        </span>
        <button
          type="button"
          class="ghost notes-recheck"
          disabled={anyRechecking}
          title="Re-resolve this note's anchors against the vault as it stands now"
          onClick={onRecheck}
        >
          {rechecking ? "…" : "⟳ recheck anchors"}
        </button>
        <button type="button" class="ghost" disabled={anyActionBusy} onClick={onPromote}>
          Promote…
        </button>
        {archived ? (
          <span class="muted notes-archived-badge">archived</span>
        ) : (
          <button type="button" class="ghost" disabled={anyActionBusy} onClick={onArchive}>
            Archive
          </button>
        )}
      </div>
    </li>
  );
}

/** The note's overall bucket — as drifted as its weakest anchor. */
function NoteStatusBadge({ status }: { status: NoteRecord["status"] }) {
  if (status === null) return null; // no anchors: claiming "exact" would assert a pin never made
  const treatment = ANCHOR_TREATMENT[status];
  return (
    <span class={`health-chip notes-anchor-status ${treatment.tone}`} title={treatment.meaning}>
      <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
    </span>
  );
}

function AnchorRow({
  anchor,
  pending,
  anyActionBusy,
  onDetach,
  onReanchor,
  onOpenDrift,
  onConfirmPending,
  onCancelPending,
}: {
  anchor: NoteAnchor;
  pending: PendingCardAction | null;
  anyActionBusy: boolean;
  onDetach: () => void;
  onReanchor: () => void;
  onOpenDrift: () => void;
  onConfirmPending: () => void;
  onCancelPending: () => void;
}) {
  const treatment = ANCHOR_TREATMENT[anchor.status];
  const page = pageLabel(anchor.page);
  // A blind "accept the current match" only means something when a resolved
  // position actually exists and differs from the pin -- `shifted`/`fuzzy`.
  // `orphaned`/`anchor-invalid` have no resolved position to accept, and
  // `exact`/`unanchored` have nothing to correct.
  const canReanchorHere = anchor.status === "shifted" || anchor.status === "fuzzy";
  const needsReview = QUEUE_ELIGIBLE_STATUSES.has(anchor.status);

  return (
    <li class="notes-anchor">
      <p class="notes-anchor-target">
        <span aria-hidden="true">↳</span>{" "}
        {page ? (
          <>
            anchored to <strong>{page}</strong>
            {anchor.heading ? <> › {anchor.heading}</> : null}
          </>
        ) : (
          <>filed against the topic — no page</>
        )}{" "}
        <span class={`health-chip notes-anchor-status ${treatment.tone}`} title={treatment.meaning}>
          <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
        </span>
      </p>
      {anchor.quote ? <blockquote class="notes-anchor-quote">{anchor.quote}</blockquote> : null}
      <p class={`notes-anchor-meaning ${treatment.tone === "bad" ? "bad" : "muted"}`}>
        {treatment.tone === "bad" ? <span aria-hidden="true">⚠ </span> : null}
        {treatment.meaning}
        {anchor.pinned_at ? <> pinned@{anchor.pinned_at}</> : null}
      </p>

      {pending ? (
        <ActionConfirm
          envelope={pending.envelope}
          busy={anyActionBusy}
          applyLabel={pending.kind === "reanchor" ? "Confirm re-anchor" : "Confirm detach"}
          onConfirm={onConfirmPending}
          onCancel={onCancelPending}
        />
      ) : (
        <div class="notes-anchor-actions">
          {canReanchorHere ? (
            <button type="button" class="ghost" disabled={anyActionBusy} onClick={onReanchor}>
              Re-anchor
            </button>
          ) : null}
          {needsReview ? (
            <button type="button" class="ghost" disabled={anyActionBusy} onClick={onOpenDrift}>
              Review drift →
            </button>
          ) : null}
          <button type="button" class="ghost" disabled={anyActionBusy} onClick={onDetach}>
            Detach
          </button>
        </div>
      )}
    </li>
  );
}

