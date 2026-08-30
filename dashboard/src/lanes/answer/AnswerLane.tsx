import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { AnswerCard } from "../../answerPresentation";
import type { ObsidianContext } from "../../obsidianLinks";
import { SectionCard } from "../../SectionCard";
import { TermHint } from "../../TermHint";
import { Spinner } from "../../icons";
import type { ToolClient } from "../../toolClient";
import type { QueryAnswer, WikiStatus } from "../../types";
import { deriveSequenceStages, type StageState } from "../laneRailState";
import { LoopStrip } from "../LoopStrip";

/**
 * `AnswerLane` (`INTERFACE_DESIGN.md §2.3`) -- the three-stage `ask -> cite ->
 * react` rail that absorbed the dissolved ask pane's question box and
 * citation rendering unchanged (`AnswerCard`, imported from
 * `answerPresentation.tsx` rather than reimplemented, exactly the way
 * `ProveStage.tsx` already reuses it for Improve's own in-lane probe).
 *
 * The watermark lives in **component state only** -- `§2.3`'s own decision,
 * re-affirmed here: `query` stays a non-writer, so nothing about this rail is
 * journal-backed or restored across a remount. Submitting a question
 * completes `ask` and activates `cite` immediately, before the LLM call
 * resolves; only once the answer lands does `cite` complete and `react`
 * become current.
 *
 * `react`'s four actions all terminate inside Answer (`§2.0` clause 2, `§2.3`
 * clause 2) -- none of them navigates to another lane. `Good example`/
 * `Bad example` reuse the unchanged `client.curateExample` call `AskPane.tsx`
 * already makes; `Note it`/`Report gap` are the two new flat Tier-1 tools
 * this step wires onto `ToolClient` (`note_capture`/`gap_report`).
 *
 * `react`'s body is rebuilt on the stage-body grammar
 * (`INTERFACE_DESIGN_2.md §5`, P2-1): one `SectionCard "REACT"` whose muted
 * explanation names what the four buttons do; `Good example`/`Bad example`
 * keep their exact accessible names and their default (non-quiet) button
 * class, since they are the pair the loop trains on directly; `Note it`/
 * `Report gap` become `class="ghost"` quiet actions, since they route
 * through the loop's queues rather than feeding a training signal directly.
 * The `role="status"` outcome note stays in the footer, unchanged text. Ask
 * and Cite are untouched -- `§5` names only React for this budget.
 */

/** React's four verbs, named so the one in flight can be told from its peers. */
type ReactVerb = "good" | "bad" | "note" | "gap";

interface AnswerStage {
  readonly id: "ask" | "cite" | "react";
  readonly title: string;
}

const ANSWER_STAGES: readonly AnswerStage[] = [
  { id: "ask", title: "Ask" },
  { id: "cite", title: "Cite" },
  { id: "react", title: "React" },
];

function isCurrentStage(state: StageState): boolean {
  return state === "active" || state === "blocked";
}

function stageGlyph(state: StageState, position: number): string {
  if (state === "complete") return "✓";
  if (state === "blocked") return "!";
  return String(position);
}

function StageRow({
  state,
  position,
  title,
  children,
}: {
  state: StageState;
  position: number;
  title: string;
  children: JSX.Element | Array<JSX.Element | null>;
}): JSX.Element {
  return (
    <li
      class="lane-stage"
      data-state={state}
      aria-current={isCurrentStage(state) ? "step" : undefined}
    >
      <span class="lane-stage-index" aria-hidden="true">
        {stageGlyph(state, position)}
      </span>
      <div class="lane-stage-content">
        <div class="lane-stage-heading">
          <strong>{title}</strong>
          <span class="lane-state-label muted">{state}</span>
        </div>
        <div class="lane-stage-body">{children}</div>
      </div>
    </li>
  );
}

export function AnswerLane({
  client,
  topic,
  vault,
  obsidianCtx,
}: {
  client: ToolClient | null;
  topic: string;
  vault: string;
  obsidianCtx: ObsidianContext;
  status: WikiStatus | null;
}): JSX.Element {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  /* Which of React's four verbs is in flight. `busy` alone disables all four,
     which is right -- but four spinners for one action would be a lie, so the
     glyph goes only on the one actually running. */
  const [reactBusy, setReactBusy] = useState<ReactVerb | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<QueryAnswer | null>(null);
  const [reacted, setReacted] = useState(false);
  const [reactNote, setReactNote] = useState<string | null>(null);

  const watermark = result ? 2 : busy ? 1 : 0;
  const [askStage, citeStage, reactStage] = deriveSequenceStages(
    watermark,
    ANSWER_STAGES,
  );

  async function ask() {
    if (!client || !question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setReacted(false);
    setReactNote(null);
    try {
      setResult(await client.query(topic, question.trim(), vault));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function curate(verdict: "good" | "bad") {
    if (!client || !result || busy) return;
    setBusy(true);
    setReactBusy(verdict);
    try {
      await client.curateExample(
        topic,
        result.question,
        result.answer,
        verdict,
        result.pages_used,
        vault,
      );
      setReacted(true);
      setReactNote(
        verdict === "good" ? "Saved as good example." : "Saved as bad example.",
      );
    } catch (cause) {
      setReactNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
      setReactBusy(null);
    }
  }

  async function noteIt() {
    if (!client || !result || busy) return;
    setBusy(true);
    setReactBusy("note");
    try {
      await client.noteCapture(
        topic,
        `Q: ${result.question}\nA: ${result.answer}`,
        "",
        result.pages_used,
        "reflection",
        [],
        vault,
      );
      setReacted(true);
      setReactNote("Captured as a note.");
    } catch (cause) {
      setReactNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
      setReactBusy(null);
    }
  }

  async function reportGap() {
    if (!client || !result || busy) return;
    setBusy(true);
    setReactBusy("gap");
    try {
      await client.gapReport(
        topic,
        result.question,
        "Flagged from Answer: the citations did not fully cover this question.",
        result.pages_used,
        vault,
      );
      setReacted(true);
      setReactNote("Reported as a gap.");
    } catch (cause) {
      setReactNote(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
      setReactBusy(null);
    }
  }

  return (
    <main class="pane-main answer">
      <LoopStrip
        lane="answer"
        stages={[askStage, citeStage, reactStage].map(
          ({ id, title, state }) => ({ id, title, state }),
        )}
      />

      <ol class="lane-rail" aria-label="answer stages">
        <StageRow state={askStage.state} position={1} title="Ask">
          <label class="ask-label">
            <span>Question</span>
            <textarea
              rows={3}
              value={question}
              placeholder="Ask the wiki…"
              disabled={busy || !client}
              onInput={(event) =>
                setQuestion((event.target as HTMLTextAreaElement).value)
              }
            />
          </label>
          <div class="ask-actions">
            <button
              type="button"
              disabled={!client || busy || !question.trim()}
              aria-busy={busy || undefined}
              onClick={() => void ask()}
            >
              {busy ? (
                <>
                  <Spinner />
                  Asking…
                </>
              ) : (
                "Ask"
              )}
            </button>
          </div>
          {error ? (
            <aside role="alert" class="ask-error">
              {error}
            </aside>
          ) : null}
        </StageRow>

        <StageRow state={citeStage.state} position={2} title="Cite">
          {result ? (
            <AnswerCard
              title="Answer"
              tone="latest"
              answer={result}
              topic={topic}
              obsidianCtx={obsidianCtx}
              actions={null}
            />
          ) : (
            <p class="muted">
              {busy
                ? "Asking the wiki…"
                : "Ask a question to see its answer and citations."}
            </p>
          )}
        </StageRow>

        <StageRow state={reactStage.state} position={3} title="React">
          <SectionCard
            title="REACT"
            footer={
              result ? (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    aria-busy={reactBusy === "good" || undefined}
                    onClick={() => void curate("good")}
                  >
                    {reactBusy === "good" ? <Spinner /> : null}
                    Good example
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    aria-busy={reactBusy === "bad" || undefined}
                    onClick={() => void curate("bad")}
                  >
                    {reactBusy === "bad" ? <Spinner /> : null}
                    Bad example
                  </button>
                  <button
                    type="button"
                    class="ghost"
                    disabled={busy}
                    aria-busy={reactBusy === "note" || undefined}
                    onClick={() => void noteIt()}
                  >
                    {reactBusy === "note" ? <Spinner /> : null}
                    Note it
                  </button>
                  <button
                    type="button"
                    class="ghost"
                    disabled={busy}
                    aria-busy={reactBusy === "gap" || undefined}
                    onClick={() => void reportGap()}
                  >
                    {reactBusy === "gap" ? <Spinner /> : null}
                    Report gap
                  </button>
                  {reacted && reactNote ? (
                    <p class="section-card-note" role="status">
                      Answer + signal: {reactNote}
                    </p>
                  ) : null}
                </>
              ) : undefined
            }
          >
            {result ? (
              <>
                <p class="muted">
                  These are the signals the loop learns from.
                </p>
                <p class="muted">
                  Good/Bad{" "}
                  <TermHint
                    id="answer-react-example"
                    term="example"
                    title="Curated example"
                    body="Kept as a curated example — good ones become training signal, bad ones become counter-examples."
                  />{" "}
                  trains the loop directly; Note it and Report gap route
                  through the loop's queues instead.
                </p>
              </>
            ) : (
              <p class="muted">Answer a question to react to it.</p>
            )}
          </SectionCard>
        </StageRow>
      </ol>
    </main>
  );
}
