import { useEffect, useState } from "preact/hooks";
import type { JSX } from "preact";

import { HandoffDispatchPanel, HandoffStage } from "../HandoffStage";
import type { HandoffDispatch } from "../HandoffStage";
import { ProcessBrief } from "../ProcessBrief";
import { ProcessOutcome } from "../ProcessOutcome";
import { Icon, Spinner, type IconName } from "../../icons";
import { SectionCard } from "../../SectionCard";
import type { SectionTone } from "../../SectionCard";
import { Stat, StatGrid } from "../../Stat";
import { StateList } from "../../StateList";
import type { StateListRow } from "../../StateList";
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
 *
 * Both stage bodies are rebuilt on the stage-body grammar
 * (`INTERFACE_DESIGN_2.md §5`, P2-2). `ingest`'s collapsed rows become a
 * `StateList`, one per not-yet-expanded suggestion, coarsely stated from
 * `suggestion.gate_outcome` (already loaded by the one `suggestionsRead`
 * call above -- zero new calls). **Deviation from the design's literal
 * `row.action = expand` sketch**: `IngestGateStage.test.tsx`'s own pinned
 * assumption 4 and `FillLane.test.tsx`'s assembly tests both require a
 * `<button>` whose accessible name is *exactly* the candidate's title, found
 * within the ingest stage's node -- an `action` slot with a fixed "Expand"
 * label would not match. The trigger is therefore hosted in `row.name`
 * itself (a real `<button>`), leaving `action` unused; this also keeps the
 * title's text node singular per row, which a separate name+action pairing
 * would have duplicated. The expanded suggestion is excluded from the list
 * and rendered as its own panel (title + `HandoffStage`) directly below it,
 * exactly where it rendered before this restructure.
 *
 * `gate`'s read-only projection becomes a `SectionCard "GATE VERDICT"`: the
 * verdict word moves from an inline `<span class="health-chip">` into the
 * header as a toned chip, and the two numbers that used to read
 * `scalar 0.62 (baseline 0.60)` inside a sentence become a `StatGrid` of
 * `SCALAR`/`BASELINE`.
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
  /* The you-control's disclosure, and whether a tier-A/B dispatch has
     actually resolved. `dispatched` is held here rather than inside the
     panel so it survives the actor flipping `you -> claude` when the session
     advances and the panel unmounts -- a confirmation that quietly vanishes
     is the failure mode this whole flow exists to fix. Both reset whenever a
     different suggestion is expanded. */
  const [panelOpen, setPanelOpen] = useState(false);
  const [dispatched, setDispatched] = useState(false);

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
    setPanelOpen(false);
    setDispatched(false);
  }

  const collapsedRows = suggestions.filter(
    (suggestion) => suggestion.suggestion_id !== expandedId,
  );

  return (
    <>
      <StageShell id="ingest" state={stateOf("ingest")} position={1}>
        <SectionCard title="APPROVED SOURCES">
          <>
            <p class="muted">
              Approved sources with an open handoff to Claude -- select one to
              open its session.
            </p>
            {error ? (
              <p class="fill-ingest-error" role="alert">
                {error}
              </p>
            ) : null}
            {loading && suggestions.length === 0 ? (
              <p class="muted">
                <Spinner />
                Loading approved suggestions…
              </p>
            ) : suggestions.length === 0 ? (
              <p class="muted">No approved suggestions waiting on ingest.</p>
            ) : (
              <StateList
                label="Approved sources"
                rows={collapsedRows.map((suggestion) =>
                  ingestRow(suggestion, () =>
                    toggleExpanded(suggestion.suggestion_id),
                  ),
                )}
              />
            )}
            {expandedSuggestion ? (
              <>
                <h4 class="ingest-row-title">
                  {expandedSuggestion.candidate.title}
                </h4>
                {client ? (
                  <HandoffStage
                    client={client}
                    topic={topic}
                    vault={vault}
                    suggestionId={expandedSuggestion.suggestion_id}
                    command="fill"
                    ask={`Claude writes the pages for "${expandedSuggestion.candidate.title}" into ${topic}, using the open candidate session.`}
                    active
                    renderYouControl={(status, dispatch) => (
                      <IngestYouControl
                        suggestion={expandedSuggestion}
                        status={status}
                        dispatch={dispatch}
                        open={panelOpen}
                        dispatched={dispatched}
                        onToggle={() => setPanelOpen((current) => !current)}
                        onDispatched={() => setDispatched(true)}
                      />
                    )}
                  />
                ) : null}
              </>
            ) : null}
          </>
        </SectionCard>
      </StageShell>

      <StageShell id="gate" state={stateOf("gate")} position={2}>
        <GateStageBody suggestion={expandedSuggestion} />
      </StageShell>
    </>
  );
}

/**
 * One collapsed suggestion as a `StateList` row. `state`/`stateLabel`/`tone`
 * are a coarse read of the suggestion's own already-loaded `gate_outcome` --
 * not a live session poll, which only the expanded item's `HandoffStage`
 * makes (zero new calls, `dec-087` clause 1).
 */
function ingestRow(
  suggestion: SuggestionRecord,
  onExpand: () => void,
): StateListRow {
  const outcome = suggestion.gate_outcome;
  const state = outcome?.verdict ?? "awaiting-gate";
  const icon: IconName =
    state === "merged"
      ? "state:complete"
      : state === "refused"
        ? "state:blocked"
        : "state:pending";
  const stateLabel =
    state === "merged"
      ? "merged"
      : state === "refused"
        ? "refused"
        : "awaiting gate";
  const tone: SectionTone | undefined =
    state === "merged" ? "good" : state === "refused" ? "bad" : undefined;

  return {
    id: suggestion.suggestion_id,
    state,
    icon,
    name: (
      <button type="button" class="ghost ingest-expand" onClick={onExpand}>
        {suggestion.candidate.title}
      </button>
    ),
    stateLabel,
    tone,
  };
}

/** What the control is called, and why the work happens in Claude. */
interface YouAffordance {
  label: string;
  why: string;
}

/**
 * One entry per `next.actor === "you"` state. The label is the historical
 * one and is **byte-preserved** -- `Open a session` is matched as an exact
 * accessible name by the lane's assembly tests, so nothing may be appended
 * to it in text.
 *
 * The narration answers the question the bare button never did: *why does
 * this happen over in Claude rather than here?* The dashboard can read a
 * candidate session; only the client's own LLM can write into one, which is
 * the client-as-brain split the whole surface is built on.
 */
function affordanceFor(
  state: SessionStatus["state"],
  previouslyRefused: boolean,
): YouAffordance | null {
  switch (state) {
    case "not_started":
      return previouslyRefused
        ? {
            label: "Rework it",
            why: "Reworking a refused candidate happens in your Claude session: it reopens the quarantined session and rewrites from what is already there.",
          }
        : {
            label: "Open a session",
            why: "Opening the candidate session — and writing the source and its pages into it — happens in your Claude session. The dashboard can read the session; only Claude can write into it.",
          };
    case "client_wrote":
      return {
        label: "Submit",
        why: "Submitting runs the preflight in your Claude session, then — only after you confirm there — finalizes the candidate and runs the gate.",
      };
    case "refused":
      return {
        label: "Rework it",
        why: "The gate refused this candidate. Reworking it reopens the quarantined session in Claude and rewrites from what is already there.",
      };
    case "swept":
      return {
        label: "Reopen",
        why: "This session expired after 24 hours. Reopening restarts it in Claude from the source that was already stored.",
      };
    default:
      // `blocked` deliberately gets no control: `HandoffStage`'s own
      // three-part `next.do` already names the missing baseline, and no
      // in-lane click can freeze one.
      return null;
  }
}

/**
 * The in-lane control for every `next.actor === "you"` state, and the inline
 * disclosure it owns. The control itself still calls no client method
 * (`dec-091`: only the dispatched `/knotica:fill` writes to the vault) --
 * the first click opens the panel, and dispatch is a second, explicit click
 * inside it.
 *
 * `[Rework it]` re-enters `ingest` in-lane (`INTERFACE_DESIGN.md §2.5`): it
 * is the *same* control as "Open a session" once the suggestion's own
 * last-recorded `gate_outcome` was `refused`, so a single affordance covers
 * both the never-started and the already-refused reading with nothing to
 * keep in sync.
 *
 * The command is identical for all four states -- `/knotica:fill` branches
 * on the session state itself -- so this is one command with four entry
 * points, not four commands.
 */
function IngestYouControl({
  suggestion,
  status,
  dispatch,
  open,
  dispatched,
  onToggle,
  onDispatched,
}: {
  suggestion: SuggestionRecord;
  status: SessionStatus;
  dispatch: HandoffDispatch;
  open: boolean;
  dispatched: boolean;
  onToggle: () => void;
  onDispatched: () => void;
}): JSX.Element | null {
  const affordance = affordanceFor(
    status.state,
    suggestion.gate_outcome?.verdict === "refused",
  );
  if (!affordance) return null;

  const panelId = `handoff-panel-${suggestion.suggestion_id}`;

  return (
    <>
      {/* The panel is a sibling, never a child: a `<div>` inside a
          `<button>` is invalid and would pollute the accessible name, which
          `aria-expanded`/`aria-controls` deliberately do not. */}
      <button
        type="button"
        class="handoff-you-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
      >
        {affordance.label}
        <Icon name="chevron-right" class="lane-disclosure-icon" />
      </button>
      {/* Sibling of the trigger, never a child: the four you-state labels are
          matched as exact accessible names by the lane's assembly tests, so
          nothing may be appended to them in text. */}
      <ProcessBrief process="fill.ingest_dispatch" term="why in Claude" align="end" />
      {open ? (
        <div class="handoff-you-panel" id={panelId}>
          <p class="handoff-panel-why">{affordance.why}</p>
          <HandoffDispatchPanel
            dispatch={dispatch}
            dispatched={dispatched}
            onDispatched={onDispatched}
          />
          <p class="handoff-panel-next muted">
            Then continue in your Claude session — this panel stays open and
            updates on its own as the session writes.
          </p>
          {/* Named only once the dispatch has actually gone: before that the
              follow-up is the dispatch, and stamping a NEXT STEP on an
              un-sent payload would point past a step nobody has taken. The
              outcome itself stays `external` -- this claims nothing about the
              session's progress, only where the work lands when it finishes. */}
          {dispatched ? <ProcessOutcome process="fill.ingest_dispatch" /> : null}
        </div>
      ) : null}
    </>
  );
}

/**
 * Read-only projection of the expanded suggestion's own `gate_outcome` --
 * zero new calls, per `dec-087` clause 1. `merged` narrates that the
 * originating gap is now resolved: the gate closing its gap is Fill's
 * terminal state (`dec-087`). Rebuilt on the grammar (`INTERFACE_DESIGN_2.md
 * §5`): the verdict word moves from an inline chip into the card header, and
 * the scalar/baseline pair -- previously `scalar 0.62 (baseline 0.60)` inside
 * a sentence -- becomes a `StatGrid`.
 */
function GateStageBody({
  suggestion,
}: {
  suggestion: SuggestionRecord | undefined;
}): JSX.Element {
  if (!suggestion) {
    return (
      <SectionCard title="GATE VERDICT">
        <p class="muted">Select an item in Ingest to see its gate verdict.</p>
      </SectionCard>
    );
  }

  const outcome = suggestion.gate_outcome;
  if (!outcome) {
    return (
      <SectionCard title="GATE VERDICT">
        <p class="muted">Not yet gated -- no verdict recorded.</p>
      </SectionCard>
    );
  }

  const tone: SectionTone = outcome.verdict === "merged" ? "good" : "bad";

  return (
    <SectionCard
      title="GATE VERDICT"
      tone={tone}
      headerActions={
        <span class="chip" data-tone={tone}>
          {outcome.verdict}
        </span>
      }
    >
      <>
        <StatGrid>
          <Stat label="SCALAR" value={outcome.scalar.toFixed(2)} />
          <Stat label="BASELINE" value={outcome.baseline_scalar.toFixed(2)} />
        </StatGrid>
        {outcome.verdict === "merged" ? (
          <p class="muted">The originating gap is now resolved.</p>
        ) : (
          <p class="muted">{outcome.reason}</p>
        )}
      </>
    </SectionCard>
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
    <li class="lane-stage" data-state={state} data-anchor={`fill:${id}`}>
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
