import { useState } from "preact/hooks";
import type { JSX } from "preact";

import { Icon } from "../../icons";
import { GapOriginBadge, ReputabilityBadge, tierKey } from "./badges";
import { verbLabel } from "./verbLabel";
import type { GateOutcome, SuggestionAction, SuggestionRecord, SuggestionStatus } from "./types";

/**
 * How a just-decided row states its own outcome: a glyph, the decision word,
 * and -- where the decision commits the vault to something -- what it committed
 * to. Word and glyph both, so the re-toned left edge is never the only carrier
 * (WCAG 1.4.1).
 */
const GHOST_DECISION: Partial<Record<SuggestionStatus, { glyph: string; text: string }>> = {
  approved: { glyph: "✓", text: "approved — queued for ingest" },
  rejected: { glyph: "✕", text: "rejected" },
  deferred: { glyph: "⧗", text: "deferred" },
};

/**
 * One suggestion as a collapsed triage row.
 *
 * The approve queue is routinely dozens of records long, so the row is sized
 * for scanning, not for reading: a tier-toned left edge, the source title as
 * the link, the two numbers a triage decision actually turns on (`rep` and
 * `rank`) as tabular values, one muted provenance line, and the three actions
 * inline. Everything else -- the failed question it answers, the snippet, the
 * signals behind the tier, the DOI, the provider, the gate's verdict -- lives
 * behind a disclosure, because none of it changes the common-case decision.
 *
 * The tier tone on the left edge is never the only carrier: the tier *word*
 * rides in the badge next to it (WCAG 1.4.1).
 *
 * Opening the reject form auto-expands the row: a reason is a considered
 * judgement, and asking for one while the evidence is folded away would be
 * asking for a guess.
 *
 * A row the reader has just decided is passed back as a `ghost`: the server has
 * already dropped it from the filtered payload, but it keeps its slot here and
 * swaps its actions for the decision it recorded. The transformation *is* the
 * feedback -- a row that simply vanished among dozens of near-identical ones
 * was indistinguishable from nothing having happened at all.
 */
export function SuggestionRow({
  suggestion,
  busyAction,
  anyBusy,
  ghost = false,
  rejectOpen,
  reasonDraft,
  onApprove,
  onDefer,
  onOpenReject,
  onCancelReject,
  onReasonChange,
  onSubmitReject,
  onWithdraw,
}: {
  suggestion: SuggestionRecord;
  /** The verb in flight on *this* row, so only the clicked control goes busy. */
  busyAction: SuggestionAction | null;
  anyBusy: boolean;
  /** Decided here, and gone from the payload: render the outcome, not actions. */
  ghost?: boolean;
  rejectOpen: boolean;
  reasonDraft: string;
  onApprove: () => void;
  onDefer: () => void;
  onOpenReject: () => void;
  onCancelReject: () => void;
  onReasonChange: (value: string) => void;
  onSubmitReject: () => void;
  onWithdraw?: () => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const candidate = suggestion.candidate;
  const busy = busyAction !== null;
  const disabled = anyBusy;
  const decided = suggestion.status !== "pending" && suggestion.status !== "deferred";
  const ghostDecision = ghost ? GHOST_DECISION[suggestion.status] : undefined;
  // Rejecting needs the evidence visible; the disclosure cannot hide it.
  const expanded = open || rejectOpen;
  const detailId = `triage-detail-${suggestion.suggestion_id}`;

  const meta = [
    candidate.venue,
    candidate.citation_count != null ? `${candidate.citation_count} citations` : null,
    candidate.is_open_access ? "open access" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li
      class="triage-row"
      data-tier={tierKey(candidate.reputability)}
      data-open={expanded}
      data-decision={ghostDecision ? suggestion.status : undefined}
      aria-busy={busy ? "true" : undefined}
    >
      <div class="triage-head">
        <button
          type="button"
          class="triage-disclose"
          aria-expanded={expanded}
          aria-controls={detailId}
          aria-label={`Details for ${candidate.title}`}
          onClick={() => setOpen((value) => !value)}
        >
          <Icon name="chevron-right" size={16} />
        </button>

        <div class="triage-identity">
          <p class="triage-title">
            <a href={candidate.url} target="_blank" rel="noreferrer">
              {candidate.title}
            </a>
          </p>
          {meta || suggestion.gap_origin ? (
            <p class="triage-meta muted">
              {meta}
              <GapOriginBadge origin={suggestion.gap_origin} />
            </p>
          ) : null}
        </div>

        <span class="triage-values">
          <ReputabilityBadge reputability={candidate.reputability} />
          <span class="triage-metric">
            rep {candidate.reputability ? candidate.reputability.score.toFixed(2) : "—"}
          </span>
          <span class="triage-metric">rank #{suggestion.rank}</span>
        </span>

        {ghostDecision ? (
          <div class="triage-actions triage-ghost">
            <p class="triage-ghost-statement">
              <span aria-hidden="true">{ghostDecision.glyph}</span> {ghostDecision.text}
            </p>
            {/* Undo, and nothing more: `withdraw` spends nothing, is itself
                reversible, and only ever applies to an approval -- so it needs
                no two-phase confirm, and there is nothing to undo on a
                rejection or a deferral the reader can simply re-decide. */}
            {suggestion.status === "approved" && onWithdraw ? (
              <button
                type="button"
                class="quiet-action"
                data-tone="neutral"
                disabled={disabled}
                aria-busy={busyAction === "withdraw" || undefined}
                onClick={onWithdraw}
              >
                {verbLabel(busyAction === "withdraw", "Withdraw")}
              </button>
            ) : null}
          </div>
        ) : decided ? (
          <p class="muted sources-decided triage-decision">
            Decision recorded: <strong>{suggestion.status}</strong>
            {suggestion.decided_reason ? ` — ${suggestion.decided_reason}` : ""}
          </p>
        ) : (
          <div class="triage-actions">
            <button
              type="button"
              class="quiet-action"
              data-tone="good"
              disabled={disabled}
              aria-busy={busyAction === "approve" || undefined}
              onClick={onApprove}
            >
              {verbLabel(busyAction === "approve", "✓ Approve")}
            </button>
            {!rejectOpen ? (
              <button
                type="button"
                class="quiet-action"
                data-tone="bad"
                disabled={disabled}
                onClick={onOpenReject}
              >
                ✕ Reject…
              </button>
            ) : null}
            <button
              type="button"
              class="quiet-action"
              data-tone="neutral"
              disabled={disabled}
              aria-busy={busyAction === "defer" || undefined}
              onClick={onDefer}
            >
              {verbLabel(busyAction === "defer", "⧗ Defer")}
            </button>
          </div>
        )}
      </div>

      {expanded ? (
        <div class="triage-detail" id={detailId}>
          <div class="sources-card-question">
            <span class="stat-label microlabel">Failed question</span>
            <p>“{suggestion.question}”</p>
            {suggestion.reference_pages.length > 0 ? (
              <p class="muted">references: {suggestion.reference_pages.join(", ")}</p>
            ) : null}
          </div>

          <div class="sources-card-source">
            <span class="stat-label microlabel">About this source</span>
            {candidate.snippet ? <p class="muted">{candidate.snippet}</p> : null}
            {candidate.authors && candidate.authors.length > 0 ? (
              <p class="muted">{candidate.authors.join(", ")}</p>
            ) : null}
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
            <p class="muted sources-provenance">
              {suggestion.fault_class} · gen-{suggestion.detected_generation} ·{" "}
              {candidate.source_provider}
              {candidate.provider_score != null
                ? ` · score ${candidate.provider_score.toFixed(2)}`
                : ""}
            </p>
          </div>

          {decided ? <GateOutcomeNote outcome={suggestion.gate_outcome} /> : null}

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
                  aria-busy={busyAction === "reject" || undefined}
                  onClick={onSubmitReject}
                >
                  {verbLabel(busyAction === "reject", "Confirm reject")}
                </button>
                <button type="button" class="ghost" disabled={busy} onClick={onCancelReject}>
                  Cancel
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/** Refused-gate note: reason + top regressed questions (already in the record -- no extra call). */
function GateOutcomeNote({ outcome }: { outcome?: GateOutcome | null }): JSX.Element | null {
  const [expanded, setExpanded] = useState(false);
  if (!outcome || outcome.verdict !== "refused") return null;
  const regressed = outcome.regressed_questions ?? [];
  const delta = outcome.scalar - outcome.baseline_scalar;

  return (
    <div class="sources-gate-refused">
      <p class="muted">
        <span class="health-chip warn">
          <span aria-hidden="true">⚠</span> refused
        </span>{" "}
        {delta.toFixed(4)} — {outcome.reason}
        {regressed.length > 0 ? (
          <button
            type="button"
            class="ghost sources-view-diff"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "hide diff" : "[view diff]"}
          </button>
        ) : null}
      </p>
      {expanded ? (
        <div class="sources-regressed-questions">
          <p class="muted">quarantined at {outcome.ref}</p>
          <ul>
            {regressed.map((row) => (
              <li key={row.qa_id} class="muted">
                “{row.question}” {row.baseline_score.toFixed(2)} → {row.candidate_score.toFixed(2)}{" "}
                ({row.delta.toFixed(2)})
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
