import type { JSX } from "preact";
import { useState } from "preact/hooks";

import { AnswerCard } from "../../AskPane";
import type { ObsidianContext } from "../../obsidianLinks";
import type { ToolClient } from "../../toolClient";
import type { QueryAnswer, WikiStatus } from "../../types";
import { deriveSequenceStages, type StageState } from "../laneRailState";

/**
 * `AnswerLane` (`INTERFACE_DESIGN.md §2.3`) -- the three-stage `ask -> cite ->
 * react` rail absorbing `AskPane.tsx`'s question box and citation rendering
 * unchanged (`AnswerCard`, imported rather than reimplemented, exactly the
 * way `ProveStage.tsx` already reuses it for Improve's own in-lane probe).
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
 */

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
    }
  }

  async function noteIt() {
    if (!client || !result || busy) return;
    setBusy(true);
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
    }
  }

  async function reportGap() {
    if (!client || !result || busy) return;
    setBusy(true);
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
    }
  }

  return (
    <main class="pane-main answer">
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
              onClick={() => void ask()}
            >
              {busy ? "Asking…" : "Ask"}
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
          {result ? (
            <div class="ask-curate">
              <button
                type="button"
                disabled={busy}
                onClick={() => void curate("good")}
              >
                Good example
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void curate("bad")}
              >
                Bad example
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void noteIt()}
              >
                Note it
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void reportGap()}
              >
                Report gap
              </button>
              {reacted && reactNote ? (
                <p class="muted" role="status">
                  Answer + signal: {reactNote}
                </p>
              ) : null}
            </div>
          ) : (
            <p class="muted">Answer a question to react to it.</p>
          )}
        </StageRow>
      </ol>
    </main>
  );
}
