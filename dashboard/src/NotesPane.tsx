import { useEffect, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type {
  AnchorProjectionStatus,
  AnchorStatusFilter,
  NoteAnchor,
  NoteIntent,
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

/** Intent -> (shape glyph, tone class) — shape + label, never color alone (WCAG 1.4.1). */
const INTENT_TREATMENT: Record<NoteIntent, { glyph: string; tone: string }> = {
  reflection: { glyph: "✎", tone: "" },
  dispute: { glyph: "⚑", tone: "warn" },
  gap: { glyph: "◆", tone: "warn" },
  question: { glyph: "?", tone: "" },
};

/**
 * Anchor status -> (glyph, tone, label, one-line meaning).
 *
 * `unanchored` and `orphaned` look alike and mean opposites, so they are pulled
 * apart deliberately: `unanchored` is the ordinary outcome of a note filed
 * against the topic — neutral tone, no warning glyph, nothing to act on.
 * `orphaned` means the wiki moved out from under a pin the note *did* make, and
 * is the only bucket that earns the bad tone.
 */
const ANCHOR_TREATMENT: Record<
  AnchorProjectionStatus,
  { glyph: string; tone: string; label: string; meaning: string }
> = {
  exact: {
    glyph: "●",
    tone: "ok",
    label: "exact",
    meaning: "the pinned passage is still there, word for word.",
  },
  unanchored: {
    glyph: "○",
    tone: "",
    label: "unanchored",
    meaning: "filed against the topic, never pinned to a page — normal, nothing to fix.",
  },
  shifted: {
    glyph: "◐",
    tone: "warn",
    label: "shifted",
    meaning: "the passage moved or was reworded, but survives.",
  },
  // `◔` not `○`: the glyphs read as a fill-level continuum of how much of the
  // pinned passage survives -- ● exact, ◐ shifted, ◔ fuzzy, ⌫ orphaned -- with
  // the empty `○` reserved for `unanchored`, which never had an anchor to lose.
  // Sharing `○` with `unanchored` collided a "needs review" status with a
  // "normal, nothing to fix" one, the two furthest apart in actionability.
  fuzzy: {
    glyph: "◔",
    tone: "warn",
    label: "fuzzy",
    meaning: "a similar passage was found but it is not an exact match.",
  },
  orphaned: {
    glyph: "⌫",
    tone: "bad",
    label: "orphaned",
    meaning: "the passage this pointed at is gone from the page.",
  },
  // `⊘` sits deliberately outside the ●◐◔⌫○ fill-continuum: those grade *how
  // much of the pinned passage survives*, and this status is not a point on
  // that scale at all -- the record never located its own quote in the blob it
  // was pinned against, so it is corruption, not loss. Sharing `⌫` with
  // `orphaned` read it as the worst case of drift, which is precisely the
  // conflation the status vocabulary exists to prevent: an orphan means the
  // wiki moved on, an invalid anchor means the note file is damaged, and they
  // want opposite responses from the reader.
  "anchor-invalid": {
    glyph: "⊘",
    tone: "bad",
    label: "unresolvable",
    meaning: "this anchor never located a page — the record itself is unusable.",
  },
};

export function NotesPane({
  client,
  topic,
  vault,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
}) {
  const [intent, setIntent] = useState<NoteIntentFilter>("all");
  const [anchorStatus, setAnchorStatus] = useState<AnchorStatusFilter>("all");
  const [result, setResult] = useState<NotesListResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recheckingId, setRecheckingId] = useState<string | null>(null);

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
            <span class="health-chip warn" title="Notes whose anchor pointed at a passage the page no longer has">
              <span aria-hidden="true">⚠</span> {driftedTotal} drifted
            </span>
          ) : null}
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
}: {
  note: NoteRecord;
  rechecking: boolean;
  anyRechecking: boolean;
  onRecheck: () => void;
}) {
  const intent = INTENT_TREATMENT[note.intent];
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
            <AnchorRow key={anchor.index} anchor={anchor} />
          ))}
        </ul>
      )}

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

function AnchorRow({ anchor }: { anchor: NoteAnchor }) {
  const treatment = ANCHOR_TREATMENT[anchor.status];
  const page = pageLabel(anchor.page);
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
    </li>
  );
}

/** The page's bare stem — what a person would call it out loud. */
function pageLabel(page: string): string {
  if (!page) return "";
  const file = page.split("/").pop() ?? page;
  return file.endsWith(".md") ? file.slice(0, -".md".length) : file;
}
