import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import { HandoffStage } from "../HandoffStage";
import type { ToolClient } from "../../toolClient";
import type {
  LaneRailStageState,
  SessionStatus,
  SuggestionRecord,
  SuggestionsReadResult,
  WikiStatus,
} from "../../types";

/**
 * `IngestGateStage` (`INTERFACE_DESIGN.md §2.5`, ④⑤) -- Fill's two newest rail
 * stages: `ingest` hands a suggestion's source-writing session to Claude
 * through the shared `HandoffStage` shell (`§3`, `dec-091`); `gate` is a
 * read-only projection of that same suggestion's own `gate_outcome`, already
 * present from the `approved`-status read below -- **zero** new calls into
 * Improve's surface (`dec-087` clause 1).
 *
 * Self-fetches `approved` suggestions on mount: `QueueStage`'s `approve`
 * stage already owns the `pending` queue, so this component's list is the
 * suggestions actively moving through ingest and gating, not a second view
 * onto the same queue.
 *
 * Exactly one suggestion's rail is open at a time (a "singleton" expansion,
 * distinct from `QueueStage`, which has no per-item expand state at all):
 * expanding a second item unmounts the first `HandoffStage` entirely rather
 * than merely pausing its poll, keeping the 3s poll cost bounded to the one
 * item a person is actually looking at.
 */

const STAGE_ORDER = ["ingest", "gate"] as const;
type StageId = (typeof STAGE_ORDER)[number];

const STAGE_TITLE: Record<StageId, string> = {
  ingest: "Ingest",
  gate: "Gate",
};

function stageGlyph(state: LaneRailStageState, position: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(position);
}

export function IngestGateStage({
  client,
  topic,
  vault,
  status,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  status?: WikiStatus | null;
  onStatusRefresh?: () => void | Promise<void>;
}): JSX.Element {
  const [result, setResult] = useState<SuggestionsReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    if (!client || !topic) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    client
      .suggestionsRead(topic, "approved", "", 20, vault)
      .then((next) => {
        if (!cancelled) setResult(next);
      })
      .catch((cause) => {
        if (!cancelled)
          setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, topic, vault]);

  const declared =
    status?.topics.find((row) => row.topic === topic)?.lanes?.fill ?? [];
  const byId = new Map(declared.map((stage) => [stage.id, stage] as const));
  const stateOf = (id: StageId): LaneRailStageState =>
    byId.get(id)?.state ?? "pending";

  const suggestions = result?.suggestions ?? [];
  const expandedSuggestion = suggestions.find(
    (suggestion) => suggestion.suggestion_id === expandedId,
  );

  function toggleExpanded(suggestionId: string) {
    setExpandedId((current) =>
      current === suggestionId ? null : suggestionId,
    );
  }

  return (
    <>
      <StageShell id="ingest" state={stateOf("ingest")} position={1}>
        <p class="muted">
          Approved sources with an open handoff to Claude -- select one to open
          its session.
        </p>
        {error ? (
          <p class="fill-ingest-error" role="alert">
            {error}
          </p>
        ) : null}
        {loading && suggestions.length === 0 ? (
          <p class="muted">Loading approved suggestions…</p>
        ) : suggestions.length === 0 ? (
          <p class="muted">No approved suggestions waiting on ingest.</p>
        ) : (
          <ul class="ingest-list">
            {suggestions.map((suggestion) => (
              <li key={suggestion.suggestion_id} class="ingest-row">
                {expandedId === suggestion.suggestion_id ? (
                  <>
                    <h4 class="ingest-row-title">
                      {suggestion.candidate.title}
                    </h4>
                    {client ? (
                      <HandoffStage
                        client={client}
                        topic={topic}
                        vault={vault}
                        suggestionId={suggestion.suggestion_id}
                        command="fill"
                        ask={`Claude writes the pages for "${suggestion.candidate.title}" into ${topic}, using the open candidate session.`}
                        active
                        renderYouControl={renderYouControlFor(suggestion)}
                      />
                    ) : null}
                  </>
                ) : (
                  <button
                    type="button"
                    class="ghost ingest-expand"
                    onClick={() => toggleExpanded(suggestion.suggestion_id)}
                  >
                    {suggestion.candidate.title}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </StageShell>

      <StageShell id="gate" state={stateOf("gate")} position={2}>
        <GateStageBody suggestion={expandedSuggestion} />
      </StageShell>
    </>
  );
}

/**
 * The in-lane control for every `next.actor === "you"` state -- calls no
 * client method itself (`dec-091`: only the dispatched `/knotica:fill`
 * command writes to the vault). `[Rework it]` re-enters `ingest` in-lane
 * (`INTERFACE_DESIGN.md §2.5`): it is the *same* control as "Open a session"
 * once the suggestion's own last-recorded `gate_outcome` was `refused` --
 * closing over the suggestion this way means it renders correctly whether
 * the live session hasn't started yet (`not_started`) or has already been
 * marked `refused` by a prior poll, with no second, independently-tracked
 * affordance to keep in sync with it. `blocked` gets no button -- `
 * HandoffStage`'s own three-part `next.do` text already says what is
 * missing, and there is nothing an in-lane click could do about a missing
 * baseline.
 */
function renderYouControlFor(
  suggestion: SuggestionRecord,
): (status: SessionStatus) => JSX.Element | null {
  const previouslyRefused = suggestion.gate_outcome?.verdict === "refused";
  return function renderYouControl(status: SessionStatus): JSX.Element | null {
    switch (status.state) {
      case "not_started":
        return (
          <button type="button">
            {previouslyRefused ? "Rework it" : "Open a session"}
          </button>
        );
      case "client_wrote":
        return <button type="button">Submit</button>;
      case "refused":
        return <button type="button">Rework it</button>;
      case "swept":
        return <button type="button">Reopen</button>;
      default:
        return null;
    }
  };
}

/**
 * Read-only projection of the expanded suggestion's own `gate_outcome` --
 * zero new calls, per `dec-087` clause 1. `merged` narrates that the
 * originating gap is now resolved: the gate closing its gap is Fill's
 * terminal state (`dec-087`), rendered here for the first time.
 */
function GateStageBody({
  suggestion,
}: {
  suggestion: SuggestionRecord | undefined;
}): JSX.Element {
  if (!suggestion) {
    return (
      <p class="muted">Select an item in Ingest to see its gate verdict.</p>
    );
  }

  const outcome = suggestion.gate_outcome;
  if (!outcome) {
    return <p class="muted">Not yet gated -- no verdict recorded.</p>;
  }

  if (outcome.verdict === "merged") {
    return (
      <div class="gate-verdict gate-merged">
        <p>
          <span class="health-chip">merged</span> scalar{" "}
          {outcome.scalar.toFixed(2)} (baseline{" "}
          {outcome.baseline_scalar.toFixed(2)}).
        </p>
        <p class="muted">The originating gap is now resolved.</p>
      </div>
    );
  }

  return (
    <div class="gate-verdict gate-refused">
      <p>
        <span class="health-chip warn">refused</span> {outcome.reason}
      </p>
    </div>
  );
}

/** Matches `QueueStage.tsx`'s `StageShell` markup contract exactly, so both
 * halves of Fill's rail stay visually consistent under `FillLane`'s shared
 * `<ol class="lane-rail">` (assembled in Step 100). */
function StageShell({
  id,
  state,
  position,
  children,
}: {
  id: StageId;
  state: LaneRailStageState;
  position: number;
  children: JSX.Element | Array<JSX.Element | null>;
}): JSX.Element {
  return (
    <li class="lane-stage" data-state={state}>
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(state, position)}
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{STAGE_TITLE[id]}</strong>
          <span class="lane-state-label muted">{state}</span>
        </div>
        <div class="lane-stage-body">{children}</div>
      </div>
    </li>
  );
}
