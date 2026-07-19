import { useEffect, useState } from "preact/hooks";

import type { ToolClient } from "./toolClient";
import type {
  GapOrigin,
  SuggestionAction,
  SuggestionRecord,
  SuggestionReputability,
  SuggestionsReadResult,
  SuggestionsStatusFilter,
} from "./types";

const FILTERS: Array<{ value: SuggestionsStatusFilter; label: string }> = [
  { value: "pending", label: "pending" },
  { value: "approved", label: "approved" },
  { value: "all", label: "all" },
];

/** Tier -> (shape glyph, tone class) — never color alone (WCAG 1.4.1). */
const TIER_TREATMENT: Record<string, { glyph: string; tone: string }> = {
  peer_reviewed: { glyph: "●", tone: "ok" }, // ●
  preprint_known_lab: { glyph: "◐", tone: "warn" }, // ◐
  established_org: { glyph: "○", tone: "warn" }, // ○
  general_web: { glyph: "·", tone: "" }, // ·
};

/** Gap origin -> (shape glyph, tone class) — shape + label, never color alone. */
const ORIGIN_TREATMENT: Record<GapOrigin, { glyph: string; tone: string; label: string }> = {
  measured: { glyph: "◆", tone: "ok", label: "measured" }, // eval-proven
  reported: { glyph: "✎", tone: "warn", label: "reported" }, // conversationally filed
  retracted: { glyph: "⌫", tone: "warn", label: "retracted" }, // guillotine-weakened
};

export function SourcesPane({
  client,
  topic,
  vault,
  onStatusRefresh,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  onStatusRefresh?: () => void | Promise<void>;
}) {
  const [filter, setFilter] = useState<SuggestionsStatusFilter>("pending");
  const [result, setResult] = useState<SuggestionsReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reasonDraft, setReasonDraft] = useState<Record<string, string>>({});
  const [rejectOpenId, setRejectOpenId] = useState<string | null>(null);

  async function load(cursor = "", append = false) {
    if (!client || !topic) return;
    setLoading(!append);
    setError(null);
    try {
      const next = await client.suggestionsRead(topic, filter, cursor, 20, vault);
      setResult((prev) =>
        append && prev
          ? { ...next, suggestions: [...prev.suggestions, ...next.suggestions] }
          : next,
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
  }, [client, topic, vault, filter]);

  async function decide(suggestionId: string, action: SuggestionAction, reason = "") {
    if (!client || busyId) return;
    setBusyId(suggestionId);
    setError(null);
    try {
      await client.suggestionsReview(topic, suggestionId, action, "apply", reason, vault);
      setRejectOpenId(null);
      setReasonDraft((prev) => {
        const next = { ...prev };
        delete next[suggestionId];
        return next;
      });
      await Promise.all([load(), onStatusRefresh?.()]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }

  const suggestions = result?.suggestions ?? [];
  const counts = result?.status_counts;

  return (
    <section class="panel sources-panel" aria-label="Gap-fill suggestions">
      <header class="sources-header">
        <div>
          <h2>Sources · {topic}</h2>
          <p class="muted">
            Ranked sources discovered for diagnosed knowledge gaps. Approve queues an ingest
            instruction for the next interactive session; reject requires a reason.
          </p>
        </div>
        <div class="sources-filters" role="group" aria-label="Filter by status">
          {FILTERS.map((entry) => (
            <button
              type="button"
              key={entry.value}
              class={filter === entry.value ? "active" : "ghost"}
              onClick={() => setFilter(entry.value)}
            >
              {entry.label}
              {counts && entry.value !== "all" ? ` ${counts[entry.value]}` : ""}
            </button>
          ))}
          <button type="button" class="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "…" : "⟳"}
          </button>
        </div>
      </header>

      {error ? (
        <p class="sources-error" role="alert">
          {error}
        </p>
      ) : null}

      {result && result.skipped_malformed > 0 ? (
        <p class="muted sources-partial-note">
          {result.skipped_malformed} suggestion record{result.skipped_malformed === 1 ? "" : "s"}{" "}
          were malformed and skipped.
        </p>
      ) : null}

      {loading && suggestions.length === 0 ? (
        <p class="muted">Loading suggestions…</p>
      ) : suggestions.length === 0 ? (
        <div class="sources-empty">
          <p>No gap-fill suggestions yet.</p>
          <p class="muted">
            The loop writes suggestions here after it diagnoses a <code>genuine_gap</code> and
            discovery finds ranked sources. To exercise it: freeze a golden question the vault
            lacks, regress, let the loop classify, then run{" "}
            <code>knotica gapfill discover --topic {topic}</code>.
          </p>
        </div>
      ) : (
        <ul class="sources-list">
          {suggestions.map((suggestion) => (
            <SuggestionCard
              key={suggestion.suggestion_id}
              suggestion={suggestion}
              busy={busyId === suggestion.suggestion_id}
              anyBusy={busyId !== null}
              rejectOpen={rejectOpenId === suggestion.suggestion_id}
              reasonDraft={reasonDraft[suggestion.suggestion_id] ?? ""}
              onApprove={() => void decide(suggestion.suggestion_id, "approve")}
              onDefer={() => void decide(suggestion.suggestion_id, "defer")}
              onOpenReject={() => setRejectOpenId(suggestion.suggestion_id)}
              onCancelReject={() => setRejectOpenId(null)}
              onReasonChange={(value) =>
                setReasonDraft((prev) => ({ ...prev, [suggestion.suggestion_id]: value }))
              }
              onSubmitReject={() =>
                void decide(
                  suggestion.suggestion_id,
                  "reject",
                  reasonDraft[suggestion.suggestion_id] ?? "",
                )
              }
            />
          ))}
        </ul>
      )}

      {result?.has_more ? (
        <button
          type="button"
          class="ghost sources-load-more"
          disabled={loading}
          onClick={() => void load(result.next_cursor, true)}
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </section>
  );
}

function SuggestionCard({
  suggestion,
  busy,
  anyBusy,
  rejectOpen,
  reasonDraft,
  onApprove,
  onDefer,
  onOpenReject,
  onCancelReject,
  onReasonChange,
  onSubmitReject,
}: {
  suggestion: SuggestionRecord;
  busy: boolean;
  anyBusy: boolean;
  rejectOpen: boolean;
  reasonDraft: string;
  onApprove: () => void;
  onDefer: () => void;
  onOpenReject: () => void;
  onCancelReject: () => void;
  onReasonChange: (value: string) => void;
  onSubmitReject: () => void;
}) {
  const candidate = suggestion.candidate;
  const disabled = anyBusy;
  const decided = suggestion.status !== "pending" && suggestion.status !== "deferred";

  return (
    <li class="sources-card">
      <div class="sources-card-head">
        <span class="status-chip">
          {suggestion.fault_class} · gen-{suggestion.detected_generation} · rank #{suggestion.rank}
        </span>
        <span class="sources-card-badges">
          <GapOriginBadge origin={suggestion.gap_origin} />
          <ReputabilityBadge reputability={candidate.reputability} />
        </span>
      </div>

      <div class="sources-card-question">
        <span class="stat-label">Failed question</span>
        <p>“{suggestion.question}”</p>
        {suggestion.reference_pages.length > 0 ? (
          <p class="muted">references: {suggestion.reference_pages.join(", ")}</p>
        ) : null}
      </div>

      <div class="sources-card-source">
        <span class="stat-label">Suggested source</span>
        <p>
          <a href={candidate.url} target="_blank" rel="noreferrer">
            {candidate.title}
          </a>
        </p>
        <p class="muted">
          {[
            candidate.venue,
            candidate.authors && candidate.authors.length > 0
              ? candidate.authors.join(", ")
              : null,
            candidate.citation_count != null ? `${candidate.citation_count} citations` : null,
            candidate.is_open_access ? "open access" : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {candidate.doi ? (
          <p>
            <a href={`https://doi.org/${candidate.doi}`} target="_blank" rel="noreferrer">
              doi:{candidate.doi} ↗
            </a>
          </p>
        ) : null}
        {candidate.reputability && candidate.reputability.signals.length > 0 ? (
          <p class="muted sources-signals">
            signals: {candidate.reputability.signals.join(" · ")}
          </p>
        ) : null}
      </div>

      {decided ? (
        <p class="muted sources-decided">
          Decision recorded: <strong>{suggestion.status}</strong>
          {suggestion.decided_reason ? ` — ${suggestion.decided_reason}` : ""}
        </p>
      ) : (
        <div class="sources-card-actions">
          <button type="button" class="primary" disabled={disabled} onClick={onApprove}>
            {busy ? "…" : "✓ Approve"}
          </button>
          {!rejectOpen ? (
            <button type="button" class="danger" disabled={disabled} onClick={onOpenReject}>
              ✕ Reject…
            </button>
          ) : null}
          <button type="button" class="ghost" disabled={disabled} onClick={onDefer}>
            {busy ? "…" : "⧗ Defer"}
          </button>
          <span class="muted sources-provenance">
            {candidate.source_provider}
            {candidate.provider_score != null ? ` · score ${candidate.provider_score.toFixed(2)}` : ""}
          </span>
        </div>
      )}

      {rejectOpen ? (
        <div class="sources-reject-form">
          <label>
            <span>Reason for rejecting</span>
            <textarea
              rows={2}
              value={reasonDraft}
              disabled={busy}
              placeholder="Why doesn't this source fit?"
              onInput={(event) => onReasonChange((event.target as HTMLTextAreaElement).value)}
            />
          </label>
          <div class="sources-reject-actions">
            <button
              type="button"
              class="danger"
              disabled={busy || !reasonDraft.trim()}
              onClick={onSubmitReject}
            >
              {busy ? "…" : "Confirm reject"}
            </button>
            <button type="button" class="ghost" disabled={busy} onClick={onCancelReject}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

function GapOriginBadge({ origin }: { origin?: GapOrigin | null }) {
  if (!origin) return null; // older records carry no provenance — omit the badge
  const treatment = ORIGIN_TREATMENT[origin];
  if (!treatment) return null;
  return (
    <span class={`health-chip sources-origin ${treatment.tone}`} title={`gap origin: ${treatment.label}`}>
      <span aria-hidden="true">{treatment.glyph}</span> {treatment.label}
    </span>
  );
}

function ReputabilityBadge({
  reputability,
}: {
  reputability: SuggestionReputability | null;
}) {
  if (!reputability) return null;
  const treatment = TIER_TREATMENT[reputability.tier] ?? TIER_TREATMENT.general_web;
  return (
    <span class={`health-chip sources-reputability ${treatment.tone}`}>
      <span aria-hidden="true">{treatment.glyph}</span> {reputability.tier.replace(/_/g, " ")}{" "}
      {reputability.score.toFixed(2)}
    </span>
  );
}
