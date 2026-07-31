import { useEffect, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type {
  AnchorProjectionStatus,
  NoteDecisionEnvelope,
  NoteDriftAlternative,
  NoteDriftItem,
  NoteIntent,
  NotesDriftResult,
} from "./types";

const PAGE_SIZE = 20;

/**
 * Intent -> (shape glyph, tone class) and anchor status -> (glyph, tone,
 * label, meaning) -- shared with ``NotesPane`` (the browse view imports both
 * from here, not the other way around, so this module stays import-free of
 * its sibling and the dependency graph between the two files stays
 * one-directional).
 */
export const INTENT_TREATMENT: Record<NoteIntent, { glyph: string; tone: string }> = {
  reflection: { glyph: "✎", tone: "" },
  dispute: { glyph: "⚑", tone: "warn" },
  gap: { glyph: "◆", tone: "warn" },
  question: { glyph: "?", tone: "" },
};

/**
 * Anchor status -> (glyph, tone, label, one-line meaning).
 *
 * `unanchored` and `orphaned` look alike and mean opposites, so they are
 * pulled apart deliberately: `unanchored` is the ordinary outcome of a note
 * filed against the topic -- neutral tone, no warning glyph, nothing to act
 * on. `orphaned` means the wiki moved out from under a pin the note *did*
 * make, and is one of the two buckets that earn the bad tone.
 */
export const ANCHOR_TREATMENT: Record<
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
  // that scale at all -- the record never located its own quote in the blob
  // it was pinned against, so it is corruption, not loss.
  "anchor-invalid": {
    glyph: "⊘",
    tone: "bad",
    label: "unresolvable",
    meaning: "this anchor never located a page — the record itself is unusable.",
  },
};

/** The page's bare stem — what a person would call it out loud. */
export function pageLabel(page: string): string {
  if (!page) return "";
  const file = page.split("/").pop() ?? page;
  return file.endsWith(".md") ? file.slice(0, -".md".length) : file;
}

/**
 * Shared confirm banner for every mutating action's ``mode=dry-run`` ->
 * ``mode=apply`` two-step -- the drift queue's re-anchor/detach and the
 * browse card's archive/detach/re-anchor all render the same envelope shape.
 */
export function ActionConfirm({
  envelope,
  busy,
  applyLabel = "Confirm",
  onConfirm,
  onCancel,
}: {
  envelope: NoteDecisionEnvelope;
  busy: boolean;
  applyLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const preview = envelope.options[0]?.preview ?? envelope.summary;
  return (
    <div class="notes-action-confirm" role="status" aria-live="polite">
      <p>{preview}</p>
      <div class="notes-action-confirm-buttons">
        <button type="button" class="primary" disabled={busy} onClick={onConfirm}>
          {busy ? "…" : applyLabel}
        </button>
        <button type="button" class="ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

type PendingAction = {
  key: string;
  kind: "reanchor" | "detach";
  noteId: string;
  anchorIndex: number;
  page: string;
  quote: string;
  envelope: NoteDecisionEnvelope;
};

function itemKey(item: NoteDriftItem): string {
  return `${item.note.note_id}:${item.drift.anchor_index}`;
}

export function NotesDriftView({
  client,
  topic,
  vault,
  onBack,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  onBack: () => void;
}) {
  const [result, setResult] = useState<NotesDriftResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [selectedAlt, setSelectedAlt] = useState<Record<string, number>>({});
  // "Keep the old pin" has no backing mutation (the dispatcher exposes only
  // reanchor/detach/promote/archive) -- dismissing is client-local and the
  // item resurfaces next time the queue is fetched, since nothing was
  // recorded. See NotesDriftView's own card copy for the honest disclosure.
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  async function load(cursor = "", append = false) {
    if (!client || !topic) return;
    setLoading(!append);
    setError(null);
    try {
      const next = await client.notesDrift(topic, cursor, PAGE_SIZE, vault);
      setResult((prev) =>
        append && prev ? { ...next, items: [...prev.items, ...next.items] } : next,
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
  }, [client, topic, vault]);

  async function previewReanchor(item: NoteDriftItem, page: string, quote: string) {
    if (!client || busyKey) return;
    const key = itemKey(item);
    setBusyKey(key);
    setError(null);
    try {
      const envelope = await client.notesReanchor(
        topic,
        item.note.note_id,
        item.drift.anchor_index,
        "dry-run",
        page,
        quote,
        vault,
      );
      if (envelope.mode !== "dry-run") return;
      setPending({
        key,
        kind: "reanchor",
        noteId: item.note.note_id,
        anchorIndex: item.drift.anchor_index,
        page,
        quote,
        envelope,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyKey(null);
    }
  }

  async function previewDetach(item: NoteDriftItem) {
    if (!client || busyKey) return;
    const key = itemKey(item);
    setBusyKey(key);
    setError(null);
    try {
      const envelope = await client.notesDetach(
        topic,
        item.note.note_id,
        item.drift.anchor_index,
        "dry-run",
        vault,
      );
      if (envelope.mode !== "dry-run") return;
      setPending({
        key,
        kind: "detach",
        noteId: item.note.note_id,
        anchorIndex: item.drift.anchor_index,
        page: "",
        quote: "",
        envelope,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyKey(null);
    }
  }

  async function applyPending() {
    if (!client || !pending) return;
    setBusyKey(pending.key);
    setError(null);
    try {
      if (pending.kind === "reanchor") {
        await client.notesReanchor(
          topic,
          pending.noteId,
          pending.anchorIndex,
          "apply",
          pending.page,
          pending.quote,
          vault,
        );
      } else {
        await client.notesDetach(topic, pending.noteId, pending.anchorIndex, "apply", vault);
      }
      setPending(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyKey(null);
    }
  }

  const items = (result?.items ?? []).filter((item) => !dismissed.has(itemKey(item)));

  return (
    <section class="panel notes-drift-panel" aria-label="Notes drift review queue">
      <header class="notes-drift-header">
        <div>
          <button type="button" class="ghost notes-drift-back" onClick={onBack}>
            ← Notes
          </button>
          <h2>Notes › Drift · {topic}</h2>
          <p class="muted">
            The wiki moved under these pins, or the record itself is unreadable. Pick what each
            note now points at — nothing is erased, every choice appends to the note&rsquo;s
            anchor history.
          </p>
        </div>
        <div class="notes-header-counts">
          <span class="health-chip">{result?.total_count ?? 0} need review</span>
          {result && result.invalid_count > 0 ? (
            <span
              class="health-chip bad"
              title="Anchors that never located a page — damaged records, not moved passages"
            >
              <span aria-hidden="true">⊘</span> {result.invalid_count} unresolvable
            </span>
          ) : null}
          <button type="button" class="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "…" : "⟳"}
          </button>
        </div>
      </header>

      {error ? (
        <p class="notes-error" role="alert">
          {error}
        </p>
      ) : null}

      {loading && items.length === 0 ? (
        <p class="muted">Loading the drift queue…</p>
      ) : items.length === 0 ? (
        <div class="notes-empty">
          <p>
            <span class="notes-empty-glyph" aria-hidden="true">
              ✓
            </span>
          </p>
          <p>Nothing needs review right now.</p>
          <p class="muted">
            Notes land here when a page is rewritten under a pin, or when an anchor can no longer
            locate the passage it recorded.
          </p>
        </div>
      ) : (
        <ul class="notes-drift-list">
          {items.map((item) => {
            const key = itemKey(item);
            return (
              <DriftItemCard
                key={key}
                item={item}
                busy={busyKey === key}
                anyBusy={busyKey !== null}
                pending={pending && pending.key === key ? pending : null}
                selectedAlt={selectedAlt[key] ?? 0}
                onSelectAlt={(index) => setSelectedAlt((prev) => ({ ...prev, [key]: index }))}
                onReanchorHere={() => void previewReanchor(item, "", "")}
                onReanchorToSelected={(alt) =>
                  void previewReanchor(item, alt.page, item.drift.pinned_quote)
                }
                onDetach={() => void previewDetach(item)}
                onKeep={() => setDismissed((prev) => new Set(prev).add(key))}
                onConfirm={() => void applyPending()}
                onCancel={() => setPending(null)}
              />
            );
          })}
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

function DriftItemCard({
  item,
  busy,
  anyBusy,
  pending,
  selectedAlt,
  onSelectAlt,
  onReanchorHere,
  onReanchorToSelected,
  onDetach,
  onKeep,
  onConfirm,
  onCancel,
}: {
  item: NoteDriftItem;
  busy: boolean;
  anyBusy: boolean;
  pending: PendingAction | null;
  selectedAlt: number;
  onSelectAlt: (index: number) => void;
  onReanchorHere: () => void;
  onReanchorToSelected: (alt: NoteDriftAlternative) => void;
  onDetach: () => void;
  onKeep: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { note, drift } = item;
  const anchor = note.anchors.find((candidate) => candidate.index === drift.anchor_index);
  // Defensive fallback: an anchor missing from its own note's live list would
  // be a payload inconsistency, not a state this UI should crash on.
  const status: AnchorProjectionStatus = anchor?.status ?? "anchor-invalid";
  const treatment = ANCHOR_TREATMENT[status];
  const intent = INTENT_TREATMENT[note.intent];
  const isInvalid = status === "anchor-invalid";
  const hasResolvedMatch = drift.live_quote !== "";
  const alternative = drift.alternatives[selectedAlt];
  const disabled = anyBusy;

  return (
    <li class="notes-drift-card">
      <div class="notes-drift-card-head">
        <span class={`status-chip ${intent?.tone ?? ""}`}>
          <span aria-hidden="true">{intent?.glyph ?? "·"}</span> {note.intent} ·{" "}
          {note.created.slice(0, 10)}
        </span>
        <span class={`health-chip notes-anchor-status ${treatment.tone}`} title={treatment.meaning}>
          <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
        </span>
      </div>

      <p class="notes-body">{note.note}</p>

      <p class="muted notes-drift-target">
        <span aria-hidden="true">↳</span> was anchored to{" "}
        {anchor?.page ? (
          <>
            <strong>{pageLabel(anchor.page)}</strong>
            {anchor.heading ? <> › {anchor.heading}</> : null}
          </>
        ) : (
          "the topic — no page"
        )}
        {anchor?.pinned_at ? <> pinned@{anchor.pinned_at}</> : null}
      </p>

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
            <p class="stat-label">
              what is there now
              {drift.rewritten_at ? (
                <>
                  {" "}
                  (rewritten {drift.rewritten_at.slice(0, 10)}
                  {drift.rewritten_by ? `, ${drift.rewritten_by}` : ""})
                </>
              ) : null}
            </p>
            {hasResolvedMatch ? (
              <blockquote class="notes-anchor-quote">{drift.live_quote}</blockquote>
            ) : (
              <p class="muted">
                {status === "orphaned"
                  ? "the passage you pinned no longer exists on this page."
                  : treatment.meaning}
              </p>
            )}
            <p class="muted notes-drift-overlap">
              {drift.overlap === null
                ? "the section you pinned survived, but none of the passage did — nothing here is comparable to it."
                : `${Math.round(drift.overlap * 100)}% of the pinned passage survives.`}
            </p>
          </>
        )}
      </div>

      {!isInvalid && !hasResolvedMatch && drift.alternatives.length > 0 ? (
        <div
          class="notes-drift-alternatives"
          role="radiogroup"
          aria-label="Closest surviving passage"
        >
          <p class="stat-label">closest surviving passage (best guess)</p>
          {drift.alternatives.map((alt, index) => (
            <label key={`${alt.page}:${alt.heading}:${index}`} class="notes-drift-alt">
              <input
                type="radio"
                name={`alt-${note.note_id}-${drift.anchor_index}`}
                checked={selectedAlt === index}
                onChange={() => onSelectAlt(index)}
              />
              {pageLabel(alt.page)}
              {alt.heading ? ` › ${alt.heading}` : ""} —{" "}
              {alt.overlap === null
                ? "the section survived (no measurable overlap)"
                : `${Math.round(alt.overlap * 100)}% overlap`}
            </label>
          ))}
        </div>
      ) : null}

      {pending ? (
        <ActionConfirm
          envelope={pending.envelope}
          busy={busy}
          applyLabel={pending.kind === "reanchor" ? "Confirm re-anchor" : "Confirm detach"}
          onConfirm={onConfirm}
          onCancel={onCancel}
        />
      ) : (
        <div class="notes-drift-actions">
          {!isInvalid && hasResolvedMatch ? (
            <button type="button" class="primary" disabled={disabled} onClick={onReanchorHere}>
              Re-anchor here
            </button>
          ) : null}
          {!isInvalid && !hasResolvedMatch && alternative ? (
            <button
              type="button"
              class="primary"
              disabled={disabled}
              onClick={() => onReanchorToSelected(alternative)}
            >
              Re-anchor to selected
            </button>
          ) : null}
          <button type="button" class="ghost" disabled={disabled} onClick={onKeep}>
            Keep the old pin
          </button>
          <button type="button" class="danger" disabled={disabled} onClick={onDetach}>
            Detach
          </button>
        </div>
      )}

      <p class="muted notes-drift-note">
        Nothing is erased — every choice appends to this note&rsquo;s anchor history.
      </p>
    </li>
  );
}
